#!/usr/bin/env python3
"""Append a commit's entry to the session's commits.md, from git alone.

Runs from the app's .githooks/post-commit, so every commit is logged —
including one made outside the agent, or by a model that forgot the
bookkeeping. Every mechanical field comes from git; the model writes only
judgment, and writes it into the commit message itself:

  - the subject is the entry's heading (what changed);
  - the body is the entry's Why;
  - the trailers `Tests:`, `PoNIF:` and `Discovered:` become those fields.

The session comes from .claude/.session-id, the time from the commit, the
author from git (so a commit someone makes by hand in an agent's clone is
not attributed to the agent). An entry whose hash is already listed is not
written again. A clone without a session sentinel logs nothing.

A commit that changes only files under reports/ is bookkeeping (a session
close, a running-log entry, the commit log itself) and is not logged. The
hook runs after the commit exists, so a commit can never carry its own
entry: without this rule, the commit that sweeps up the last pending entry
would log itself, and every session would end with commits.md modified.

Usage: log-commit.py [--why TEXT] [REV]     (REV defaults to HEAD)

Stdlib only — vendored into apps as .claude/scripts/log-commit.py.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

JUDGMENT_TRAILERS = ("Tests", "PoNIF", "Discovered")
TRAILER_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9-]*):\s*(.*)$")


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout


def project_root() -> Path:
    # The repository being committed to wins: git runs the hook at its top
    # level, and CLAUDE_PROJECT_DIR may name another project when an agent
    # commits in a second repo.
    try:
        return Path(git(Path.cwd(), "rev-parse", "--show-toplevel").strip())
    except (OSError, subprocess.CalledProcessError):
        pass
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else Path(__file__).resolve().parents[2]


def trailers(root: Path, rev: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in git(root, "log", "-1", "--format=%(trailers:only,unfold)", rev).splitlines():
        match = TRAILER_LINE.match(line)
        if match and match.group(1) in JUDGMENT_TRAILERS:
            out[match.group(1)] = match.group(2).strip()
    return out


def body_without_trailers(root: Path, rev: str) -> str:
    body = git(root, "log", "-1", "--format=%b", rev).strip()
    if not git(root, "log", "-1", "--format=%(trailers:only)", rev).strip():
        return body
    paragraphs = body.split("\n\n")
    last = paragraphs[-1].splitlines()
    if last and all(TRAILER_LINE.match(line) or line.startswith((" ", "\t")) for line in last):
        paragraphs = paragraphs[:-1]
    return "\n\n".join(paragraphs).strip()


def report_only(root: Path, rev: str) -> bool:
    paths = [p for p in git(root, "show", "--name-only", "--format=", rev).splitlines() if p]
    return bool(paths) and all(p.startswith("reports/") for p in paths)


def diffstat(root: Path, rev: str) -> tuple[int, int, int]:
    files = added = removed = 0
    for line in git(root, "show", "--numstat", "--format=", rev).splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        files += 1
        added += int(parts[0]) if parts[0].isdigit() else 0
        removed += int(parts[1]) if parts[1].isdigit() else 0
    return files, added, removed


def entry(root: Path, rev: str, why_override: str | None) -> tuple[str, str]:
    short, subject, author, email, when = git(
        root, "log", "-1", "--format=%h%x00%s%x00%an%x00%ae%x00%cI", rev
    ).rstrip("\n").split("\x00")
    files, added, removed = diffstat(root, rev)
    found = trailers(root, rev)
    why = why_override or body_without_trailers(root, rev) or "not recorded"
    time = datetime.fromisoformat(when).astimezone().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"## {short} — {subject}",
        f"**Time:** {time}",
        f"**Author:** {author} <{email}>",
        f"**Files:** {files} changed, +{added}/-{removed}",
        f"**Tests:** {found.get('Tests', 'not recorded')}",
        f"**Why:** {why}",
    ]
    for key in ("PoNIF", "Discovered"):
        if key in found:
            lines.append(f"**{key}:** {found[key]}")
    return short, "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--why", help="override the Why taken from the commit body")
    parser.add_argument("rev", nargs="?", default="HEAD")
    args = parser.parse_args(argv)

    root = project_root()
    sentinel = root / ".claude" / ".session-id"
    if not sentinel.is_file() or not sentinel.read_text().strip():
        print("log-commit: no .claude/.session-id — commit not logged", file=sys.stderr)
        return 0
    session = sentinel.read_text().strip()

    try:
        if report_only(root, args.rev):
            return 0
        short, text = entry(root, args.rev, args.why)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"log-commit: could not read {args.rev} from git ({exc}) — commit not logged",
              file=sys.stderr)
        return 0

    log = root / "reports" / session / "commits.md"
    existing = log.read_text() if log.is_file() else ""
    if re.search(rf"^## {re.escape(short)}\b", existing, re.MULTILINE):
        return 0
    log.parent.mkdir(parents=True, exist_ok=True)
    if not existing:
        existing = f"# Commits — {session}\n"
    log.write_text(existing.rstrip("\n") + "\n\n" + text)
    print(f"log-commit: {short} logged to reports/{session}/commits.md")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
