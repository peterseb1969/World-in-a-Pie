Recover context after a context window compaction or at the start of a new session. Use this when you have lost track of where you were, or when continuing work started in a previous session.

### Why this exists

Every long session hits context compaction. Every new session starts cold. Without a defined recovery process, every Claude instance reinvents context recovery — reading random files, guessing at progress, repeating completed work. This command codifies what recovery looks like.

### Key principle

This command relies ONLY on durable artifacts — files on disk, git history, WIP state. It never assumes anything from a previous conversation. If it's not written down, it doesn't exist.

### Steps

#### 1. Check git state
```
git log --oneline -20    # What was committed recently?
git status               # Any uncommitted work?
git diff --stat          # What's changed but not committed?
```

Uncommitted changes are the most fragile state — they survived compaction only because they're on disk, but they haven't been saved to git yet. Note them carefully.

#### 2. Identify the active component
From git log and uncommitted changes, determine which component or library was being worked on. Look at file paths in recent commits and diffs:
- `components/registry/` → Registry service
- `components/def-store/` → Def-Store service
- `components/template-store/` → Template-Store service
- `components/document-store/` → Document-Store service
- `components/reporting-sync/` → Reporting-Sync service
- `components/ingest-gateway/` → Ingest Gateway
- `components/mcp-server/` → MCP Server
- `libs/wip-auth/` → Shared auth library
- `libs/wip-client/` → TypeScript client
- `libs/wip-react/` → React hooks
- `ui/wip-console/` → Vue 3 Console UI
- `scripts/` → Setup/tooling scripts

#### 3. Check roadmap for priorities
Read `docs/roadmap.md` to understand current priorities and what was likely being worked on.

#### 4. Check for design documents
If recent commits reference a feature, check `docs/design/` for the relevant design document. This gives context on intent and scope.

#### 5. Report to user
Present a concise recovery summary:

```
Context Recovery Summary:

Branch: develop
Last commit: "Fix reporting-sync template metadata sync" (3 hours ago)
Uncommitted: changes to components/reporting-sync/src/sync.py

Active component: reporting-sync
Related design doc: docs/design/... (if applicable)
Roadmap context: [relevant roadmap item]

Suggested next step: [based on evidence]
```

Ask the user to confirm before proceeding. They may have context you can't recover from artifacts alone.

### When to use this

- **After context compaction** — you notice gaps in your understanding of the current work
- **At the start of any session** — especially if you're not sure what was done previously
- **When confused** — if something doesn't make sense, recover context before guessing

### What this is NOT

This is not a substitute for committing work and writing documentation. If the previous session didn't commit and didn't document, recovery will be incomplete.
