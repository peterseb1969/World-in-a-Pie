Recover context after a context window compaction or at the start of a new session. Use this when you have lost track of where you were, or when continuing work started in a previous session.

### Why this exists

Every long session hits context compaction. Every new session starts cold. Without a defined recovery process, every Claude instance reinvents context recovery — reading random files, guessing at progress, repeating completed work. This command codifies what recovery looks like.

### Key principle

This command relies ONLY on durable artifacts — files on disk, git history, WIP state. It never assumes anything from a previous conversation. If it's not written down, it doesn't exist.

### Steps

#### 1. Check durable documentation
Read the app's documentation files (if they exist):
- `README.md` — what this app does
- `ARCHITECTURE.md` — how it's structured, key decisions
- `WIP_DEPENDENCIES.md` — which WIP entities it uses
- `KNOWN_ISSUES.md` — what's broken or deferred
- `CHANGELOG.md` — what changed recently

If none of these exist, you're likely in early phases (before Phase 4).

#### 2. Check git state
```
git log --oneline -20    # What was committed recently?
git status               # Any uncommitted work?
git diff --stat          # What's changed but not committed?
```

Uncommitted changes are the most fragile state — they survived compaction only because they're on disk, but they haven't been saved to git yet. Note them carefully.

#### 3. Check WIP state
Run the same checks as `/wip-status`:
- `get_wip_status` — are services healthy?
- `list_terminologies` — what vocabularies exist?
- `list_templates` — what document schemas exist?
- `query_by_template(template_value)` for each active template — how many documents?

#### 4. Check seed files
If `data-model/` exists:
- Compare seed files against WIP state
- If they match: Phases 2-3 are complete
- If WIP has entities not in seed files: either Phase 3 was done without export, or work is in progress

#### 5. Determine current phase
Use the evidence to determine where you are:

| Evidence | Phase |
|---|---|
| No terminologies/templates in WIP beyond defaults | Phase 1 (Exploratory) or not started |
| Terminologies exist but no templates | Phase 2 (Design) in progress or Phase 3 (Implementation) started |
| Templates and test documents exist | Phase 3 complete or in progress |
| App scaffold exists in `src/` | Phase 4 (Application Layer) in progress |
| App has multiple committed features, tests pass | Phase 4 complete, now in `/improve` mode |

#### 6. Reconstruct task state
Based on all of the above, determine:
- What phase you're in
- What's been completed (committed work, data in WIP)
- What's in progress (uncommitted changes)
- What's next (the logical next step in the current phase)

#### 7. Report to user
Present a concise recovery summary:

```
Context Recovery Summary:

Phase: 4 (Application Layer) — in progress
Last commit: "feat: add transaction list page" (2 hours ago)
Uncommitted: changes to src/pages/ImportPage.tsx (import flow in progress)

WIP state:
- 5 terminologies (all active)
- 4 templates (all active, v1)
- 127 documents (42 accounts, 85 transactions)

Seed files: present and matching WIP state

Suggested next step: Complete the import flow in ImportPage.tsx, then commit.
```

Ask the user to confirm before proceeding. They may have context you can't recover from artifacts alone.

### When to use this

- **After context compaction** — you notice gaps in your understanding of the current work
- **At the start of any session** — especially if you're not sure what was done previously
- **When confused** — if something doesn't make sense, recover context before guessing
- **Proactively** — if a session is getting long and you want to checkpoint your understanding

### What this is NOT

This is not a substitute for committing work and writing documentation. If the previous session didn't commit and didn't document, recovery will be incomplete. That's by design — it reinforces the discipline of committing early and often.
