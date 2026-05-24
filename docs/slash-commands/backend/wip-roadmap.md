Show the project backlog and recent momentum so you can decide what to work on next.

Priorities live in the **case system**, not a static doc. The old `docs/roadmap.md` was retired by the DOC-YAC audit; the live backlog is the set of open cross-agent cases. This command reads that, plus git momentum and in-progress work.

### Steps

#### 1. The open-case backlog (the live priority list)

Cross-agent cases ARE the backlog. List the open ones:

```bash
test -d yac-discussions && \
  python3 "$(dirname "$(realpath yac-discussions)")/tools/case-fetch.py" list --status open \
  || echo "(no yac-discussions/ symlink here — skip to step 2)"
```

(REST-canonical, path-independent via the `yac-discussions` symlink — the `realpath` derivation works whether it was linked relative or absolute; same pattern as `/wip-case`. CASE-393/403.) Each row carries the case number, status, and — where populated — severity / type / component / app from the CASE_RECORD structured fields.

**Read the list output; do not summarize the backlog from memory.** Failure handling: exit 0 with an empty table means zero open cases (not an error); exit 2 is a transport error (kb unreachable) — report it verbatim and continue with git momentum below. The view degrades gracefully when kb is down.

#### 2. Recent momentum

```bash
git log --oneline -20 --date=short --format="%h %ad %s"
```

Highlight which components/areas have been active recently — context on what's in flight.

#### 3. In-progress work

```bash
git status
git diff --stat
```

Uncommitted changes are the strongest signal of what was being worked on before this session — surface them.

#### 4. Present and ask

Synthesize the three inputs and ask the operator what to work on:

- **Backlog** — from the open cases. Flag the ones that look *ready* (clear scope, no blocker) and any high-severity / backend-relevant items.
- **Momentum** — what the recent commits touched.
- **In-progress** — uncommitted work, or "clean working tree."

```
Project backlog (open cases):
  - CASE-NNN <title> (severity · component) — <one-line>
  - ...

Recent activity (last commits):
  - <commit summaries>

In-progress: <uncommitted changes, or "clean">

What would you like to work on? (Suggested ready items: ...)
```

### When to use

- Start of a session, after `/wip-setup` (or `/wip-wake`) has passed, to pick up the highest-priority ready work.
- Any time you've finished a task and want the next one.
- When unsure what's actionable — the open-case list is the authoritative backlog, not chat memory.
