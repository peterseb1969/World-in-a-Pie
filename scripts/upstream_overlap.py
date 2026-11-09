#!/usr/bin/env python3
"""Does what arrived upstream touch what a test run depends on?

Used by scripts/wip-test.sh. Parallel clones push to the same branch; a test
run that finishes against code another clone already changed has to run
again, so the wrapper stops such a run as soon as the overlap arrives, and
the same check runs before a push.

    upstream_overlap.py snapshot <repo> <state-file>
        Fetch, then record where each remote's copy of the current branch
        points. A run compares against this, so only commits that arrive
        while it runs count as "came in during the run".

    upstream_overlap.py check <repo> <component>... [--since <state-file>]
        Fetch, then list the commits that arrived — since the snapshot with
        --since, else every upstream commit HEAD does not have — and whether
        they touch the components' watch paths or what this clone changed
        itself (a rebase would change that code too).

Every remote that carries the current branch is fetched and compared: in a
clone whose tracking remote is a mirror, the primary may be ahead of it.

Exit codes: 0 = nothing that came in overlaps; 75 = overlap (one reason line
on stdout); 76 = unknown — a fetch failed or the branch has no remote copy,
so nothing could be checked (never read as clean).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

OVERLAP = 75
UNKNOWN = 76
FETCH_TIMEOUT = 30

# Files whose change invalidates any run: the wrapper, this helper, and the
# root pytest configuration.
ALWAYS_WATCHED = ("scripts/wip-test.sh", "scripts/upstream_overlap.py", "pyproject.toml")

# The toolkit's integration suite mounts these services in-process
# (mirrors wip-test.sh's provisioning for wip-toolkit).
TOOLKIT_EXTRA = ("libs/wip-archive", "components/registry", "components/def-store",
                 "components/template-store", "components/document-store")

_EDITABLE_LIB = re.compile(r"^\s*-e\s+(\S+)")


def git(repo: Path, *args: str, timeout: float | None = None) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        check=True, timeout=timeout,
    ).stdout


def component_dir(repo: Path, component: str) -> str | None:
    """The repo-relative directory of a wip-test.sh target."""
    if component == "wip-toolkit":
        return "WIP-Toolkit"
    for candidate in (f"components/{component}", f"libs/{component}", component):
        if (repo / candidate).is_dir():
            return candidate
    return None


def watch_paths(repo: Path, component: str) -> list[str]:
    """Path prefixes a run of `component` depends on. `all` (or an unknown
    target) depends on everything, so it is the empty prefix."""
    base = component_dir(repo, component)
    if component == "all" or base is None:
        return [""]
    paths = {base, *ALWAYS_WATCHED}
    if component == "wip-toolkit":
        paths.update(TOOLKIT_EXTRA)
    requirements = repo / base / "requirements.txt"
    if requirements.is_file():
        for line in requirements.read_text().splitlines():
            match = _EDITABLE_LIB.match(line)
            if match:
                lib = (repo / base / match.group(1)).resolve()
                try:
                    paths.add(str(lib.relative_to(repo.resolve())))
                except ValueError:
                    continue
    return sorted(paths)


def remote_refs(repo: Path) -> dict[str, str]:
    """remote name -> 'remote/branch' for every remote carrying the current
    branch (after a fetch has brought the refs up to date)."""
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    refs: dict[str, str] = {}
    for remote in git(repo, "remote").split():
        ref = f"{remote}/{branch}"
        try:
            git(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/{ref}")
        except subprocess.CalledProcessError:
            continue
        refs[remote] = ref
    return refs


def fetch(repo: Path) -> dict[str, str] | None:
    """Fetch every remote carrying the branch; their ref -> sha, or None
    when any fetch failed or none carries it."""
    refs = remote_refs(repo)
    if not refs:
        return None
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    for remote in refs:
        try:
            git(repo, "fetch", "--quiet", remote, branch, timeout=FETCH_TIMEOUT)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None
    return {ref: git(repo, "rev-parse", ref).strip() for ref in refs.values()}


def changed_paths(repo: Path, old: str, new: str) -> set[str]:
    out = git(repo, "diff", "--name-only", f"{old}...{new}")
    return {line for line in out.splitlines() if line}


def local_paths(repo: Path, ref: str) -> set[str]:
    """What this clone changed relative to where it left `ref`: commits not
    upstream, plus staged and unstaged edits."""
    base = git(repo, "merge-base", "HEAD", ref).strip()
    out = git(repo, "diff", "--name-only", base)
    return {line for line in out.splitlines() if line}


def overlap(paths: set[str], prefixes: list[str], local: set[str]) -> list[str]:
    hits = {
        p for p in paths
        if p in local or any(p == pre or p.startswith(pre.rstrip("/") + "/") or pre == ""
                             for pre in prefixes)
    }
    return sorted(hits)


def _top_level(paths: list[str]) -> str:
    groups = sorted({"/".join(p.split("/")[:2]) for p in paths})
    return ", ".join(groups[:6]) + (" …" if len(groups) > 6 else "")


def cmd_snapshot(repo: Path, state_file: Path) -> int:
    shas = fetch(repo)
    state_file.write_text(json.dumps(shas or {}))
    if shas is None:
        print("upstream: fetch failed or no remote carries this branch — not watched")
        return UNKNOWN
    return 0


def cmd_check(repo: Path, components: list[str], since: Path | None) -> int:
    shas = fetch(repo)
    if shas is None:
        print("upstream: fetch failed or no remote carries this branch — overlap unknown")
        return UNKNOWN
    start = json.loads(since.read_text()) if since and since.is_file() else {}
    prefixes = sorted({p for c in components for p in watch_paths(repo, c)})
    incoming: set[str] = set()
    commits: set[str] = set()
    local: set[str] = set()
    for ref, sha in shas.items():
        old = start.get(ref) or git(repo, "rev-parse", "HEAD").strip()
        if old == sha:
            continue
        incoming |= changed_paths(repo, old, sha)
        commits |= set(git(repo, "rev-list", f"{old}..{sha}").split())
        local |= local_paths(repo, ref)
    hits = overlap(incoming, prefixes, local)
    if not hits:
        return 0
    print(
        f"superseded: upstream {len(commits)} commit(s) touch {_top_level(hits)} "
        f"— what this run of {' '.join(components)} depends on"
    )
    return OVERLAP


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[0] == "snapshot":
        return cmd_snapshot(Path(argv[1]), Path(argv[2]))
    if len(argv) >= 3 and argv[0] == "check":
        rest = argv[2:]
        since = None
        if "--since" in rest:
            i = rest.index("--since")
            since = Path(rest[i + 1])
            rest = rest[:i] + rest[i + 2:]
        return cmd_check(Path(argv[1]), rest, since)
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
