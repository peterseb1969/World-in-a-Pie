#!/usr/bin/env python3
"""Record the app's current development phase in .claude/.app-phase.

Each phase command runs this as its first step, so the phase is written by
the command that starts it, never inferred from WIP state or remembered by
the model. The file is tracked: it is committed with the phase's work, and
`git log .claude/.app-phase` is the phase history every clone agrees on.

Usage: set-phase.py <phase>      e.g. set-phase.py implement

The file holds one line, "<number> <name>" (e.g. "3 implement"). Setting the
phase it already holds changes nothing. Stdlib only — vendored into apps as
.claude/scripts/set-phase.py.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PHASES = {
    "explore": 1,
    "design-model": 2,
    "implement": 3,
    "build-app": 4,
    "improve": 5,
}


def project_root() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2]


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in PHASES:
        names = ", ".join(PHASES)
        print(f"usage: set-phase.py <phase>   (one of: {names})", file=sys.stderr)
        return 2
    name = argv[0]
    line = f"{PHASES[name]} {name}\n"
    path = project_root() / ".claude" / ".app-phase"
    previous = path.read_text() if path.is_file() else ""
    if previous == line:
        print(f"phase: {line.strip()} (unchanged)")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(line)
    tmp.replace(path)
    was = f" (was: {previous.strip()})" if previous.strip() else ""
    print(f"phase: {line.strip()}{was} — commit .claude/.app-phase with this phase's work")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
