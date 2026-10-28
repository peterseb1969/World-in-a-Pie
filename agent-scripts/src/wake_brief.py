#!/usr/bin/env python3
"""Print what a waking app agent needs to resume, from files and git only.

The slim /wip-wake runs this right after wake-rollover.py, in place of the
full profile's two dozen reads and its WIP census. No judgment happens here:
it prints, and the agent decides.

  1. Phase — the content of .claude/.app-phase with the date it was last
     committed and the card that phase reads. Never inferred from WIP state:
     a phase nobody recorded is reported as unrecorded.
  2. Prior session — its `## Session Summary` (or a note that it is a stub
     or absent; this script never reconstructs one) and the size of its
     commit log and running log, with the running log's latest headline.
  3. Git — the last 5 commits and `git status --short`.
  4. Project brief — the "Open cases" and "Decisions" sections of the brief
     wake-rollover.py saved in the new session's report dir, if any.
  5. Warnings — a commit hook that is not active (core.hooksPath is not
     .githooks, so commits are not logged).

Usage: wake-brief.py [--prior SESSION_ID]
The prior defaults to the current session's `continues_from`.

Stdlib only — vendored into apps as .claude/scripts/wake-brief.py. Output is
capped so the whole brief stays near 2k tokens.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PHASE_CARDS = {
    "explore": "docs/cards/model.md",
    "design-model": "docs/cards/model.md",
    "implement": "docs/cards/model.md",
    "build-app": "docs/cards/build.md",
    "improve": "docs/cards/build.md",
}
SUMMARY_CAP = 3000
STATUS_CAP = 15
BRIEF_SECTIONS = ("Open cases", "Decisions")
BRIEF_ITEMS_CAP = 10


def project_root() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else Path.cwd()


def git(root: Path, *args: str) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                              text=True, check=True, timeout=15).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None


def frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    fm = {}
    for line in text[4:end].splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fm[key.strip()] = value.strip()
    return fm, text[end + 5:]


def section(body: str, heading_prefix: str, level: str = "## ") -> str | None:
    """The text under the first heading starting with `heading_prefix`."""
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(level + heading_prefix):
            out = []
            for following in lines[i + 1:]:
                if following.startswith(level) or (level == "### " and following.startswith("## ")):
                    break
                out.append(following)
            return "\n".join(out).strip()
    return None


def phase_part(root: Path) -> list[str]:
    path = root / ".claude" / ".app-phase"
    if not path.is_file() or not path.read_text().strip():
        return ["Phase: not recorded (.claude/.app-phase is absent — the next phase command records it)"]
    value = path.read_text().strip()
    name = value.split()[-1]
    stamp = git(root, "log", "-1", "--format=%ct", "--", ".claude/.app-phase")
    if stamp and stamp.strip():
        days = (datetime.now() - datetime.fromtimestamp(int(stamp.strip()))).days
        age = "set today" if days == 0 else f"set {days} day{'s' if days != 1 else ''} ago"
    else:
        age = "not yet committed"
    out = [f"Phase: {value} ({age})"]
    card = PHASE_CARDS.get(name)
    if card:
        out.append(f"Phase card: {card}")
    return out


def prior_part(root: Path, prior: str | None) -> list[str]:
    if not prior:
        return ["Prior session: none (a fresh start)"]
    d = root / "reports" / prior
    md = d / "session.md"
    out = [f"Prior session: {prior}"]
    if not md.is_file():
        return [*out, "  (no session.md on this clone)"]
    _fm, body = frontmatter(md.read_text())
    summary = section(body, "Session Summary")
    if summary is None:
        out.append("  Summary: absent")
    elif not summary:
        out.append("  Summary: a stub (the session ended without session-end). Not "
                   "reconstructed here; a full-profile agent or the field reporter backfills it.")
    else:
        if len(summary) > SUMMARY_CAP:
            summary = summary[:SUMMARY_CAP].rstrip() + "\n  … (truncated; read reports/"
            summary += f"{prior}/session.md for the rest)"
        out.append("  Summary:")
        out.extend("    " + line if line else "" for line in summary.splitlines())
    commits = d / "commits.md"
    n = len(re.findall(r"^## ", commits.read_text(), re.MULTILINE)) if commits.is_file() else 0
    out.append(f"  Commit log: {n} entr{'y' if n == 1 else 'ies'}")
    updates = d / "session-updates.md"
    if updates.is_file():
        heads = re.findall(r"^## (.+)$", updates.read_text(), re.MULTILINE)
        if heads:
            out.append(f"  Running log: {len(heads)} entries; latest: {heads[-1]}")
    return out


def git_part(root: Path) -> list[str]:
    log = git(root, "log", "--oneline", "-5")
    status = git(root, "status", "--short")
    if log is None:
        return ["Git: not a repository"]
    out = ["Last commits:"] + ["  " + line for line in log.splitlines()]
    lines = status.splitlines() if status else []
    if not lines:
        out.append("Uncommitted: nothing")
    else:
        out.append(f"Uncommitted ({len(lines)}):")
        out.extend("  " + line for line in lines[:STATUS_CAP])
        if len(lines) > STATUS_CAP:
            out.append(f"  … and {len(lines) - STATUS_CAP} more")
    return out


def brief_part(root: Path, session: str | None) -> list[str]:
    if not session:
        return []
    path = root / "reports" / session / "project-brief.md"
    if not path.is_file():
        return []
    text = path.read_text()
    out = []
    for title in BRIEF_SECTIONS:
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith(f"### {title}"):
                out.append(line.lstrip("# "))
                items = []
                for following in lines[i + 1:]:
                    if following.startswith("#"):
                        break
                    if following.strip():
                        items.append(following)
                out.extend(items[:BRIEF_ITEMS_CAP])
                if len(items) > BRIEF_ITEMS_CAP:
                    out.append(f"  … see {path.relative_to(root)}")
                break
    return out


def warnings(root: Path) -> list[str]:
    out = []
    hooks = git(root, "config", "--get", "core.hooksPath")
    if (hooks is not None or (root / ".git").exists()) and (hooks or "").strip() != ".githooks":
        out.append("WARNING: core.hooksPath is not .githooks — commits are not logged to "
                   "commits.md. Fix: git config core.hooksPath .githooks")
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prior", help="the prior session (default: continues_from)")
    args = parser.parse_args(argv)

    root = project_root()
    sentinel = root / ".claude" / ".session-id"
    session = sentinel.read_text().strip() if sentinel.is_file() else None
    prior = args.prior
    if prior is None and session:
        md = root / "reports" / session / "session.md"
        if md.is_file():
            prior = frontmatter(md.read_text())[0].get("continues_from")

    blocks = [
        [f"Session: {session or 'none (run /wip-setup)'}", *phase_part(root)],
        prior_part(root, prior),
        git_part(root),
        brief_part(root, session),
        warnings(root),
    ]
    print("\n\n".join("\n".join(b) for b in blocks if b))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
