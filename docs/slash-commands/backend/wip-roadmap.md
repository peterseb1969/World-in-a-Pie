Show project state and priorities. Use this to decide what to work on next.

### Steps

#### 1. Read roadmap
Read `docs/roadmap.md` and summarize the current priorities, grouped by:
- **v1.1** — flagship features
- **Near-Term** — bugs, required features, infrastructure
- **Medium-Term** — larger initiatives
- **Longer-Term** — ideas and future plans

#### 2. Show recent momentum
```bash
git log --oneline -20 --date=short --format="%h %ad %s"
```

Highlight which areas have been active recently — this gives context on what was being worked on.

#### 3. List design documents with status
Read the design document status table from `docs/roadmap.md` and display it. Highlight any that are "Design complete, not started" — these are ready to implement.

#### 4. Check for in-progress work
```bash
git status
git diff --stat
```

If there's uncommitted work, note it — it may indicate what was being worked on before this session.

#### 5. Present and ask
Show the summary and ask the user what they'd like to work on. Suggest the highest-priority items that are ready to implement.

```
Project State:

v1.1 Priorities:
  - MCP Read-Only Mode (not started, ~1 hour)
  - NL Query Scaffold (design complete, depends on read-only mode)

Near-Term:
  - [list items with status]

Recent activity (last 5 commits):
  - [commit summaries]

In-progress: [uncommitted changes or "clean working tree"]

What would you like to work on?
```
