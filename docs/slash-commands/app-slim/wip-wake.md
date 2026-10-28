Continue the work in a new session linked to the previous one. Use after a `/clear`, or when Peter asks. Not after a compaction: a compaction keeps the session — the re-anchor hook has already printed your rules and phase.

### 1. Roll over and read the brief — one call

```bash
python3 .claude/scripts/wake-rollover.py && python3 .claude/scripts/wake-brief.py
```

`wake-rollover.py` closes the previous session if it is still open, mints your new session ID into `.claude/.session-id`, and saves the project brief (tier 3). `wake-brief.py` then prints, from files and git only: the session, the recorded phase and its card, the previous session's summary, the last commits, uncommitted files, open cases and decisions, and any warning.

If `wake-rollover.py` exits non-zero, stop and relay its message:
- exit 2 (no session) → run `/wip-setup` instead;
- exit 3 (the previous session's report folder is missing) → do not recreate it; ask Peter;
- exit 4 (no `.claude/.session-role`) → Peter re-runs `scripts/create-app-project.sh <this app> --prefix APP-<X>` from the WIP clone.

If the brief says the previous summary is a **stub**, note it in your report and move on. Do not reconstruct it.

### 2. Read the core card

`Read` `docs/cards/core.md`.

### 3. Read the phase card

If the brief names a `Phase card:`, `Read` it. If the phase is not recorded, the next phase command records it — read nothing more now.

### 4. Check WIP — only if the next task touches WIP data

Call `get_wip_status`. Skip it for code-only work.

### 5. Report and ask

Report in a few lines: session ID, phase, what the previous session finished and left open, uncommitted files, warnings, and the next step you suggest. Then ask Peter to confirm before you act — he may know things the files do not.
