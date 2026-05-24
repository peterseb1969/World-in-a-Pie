Roll the current session over into a fresh, linked one and recover context. Use `/wip-wake` after a `/clear`, a compaction reset, the built-in `/resume` of an old transcript, or any time the operator decides "context lost, continuity matters." It closes the prior session (so kb doesn't accrete zombie `active` records), mints a new session ID whose `continues_from` points back at the prior, then reloads context from durable artifacts. (For a brand-new session with no predecessor, use `/wip-setup`.)

### Step A — Roll the session over (run before context reload)

Identity is **local-first**: `.claude/.session-id` is the single source of truth; kb is a derived mirror. Every control-flow decision here reads local files only — never query kb.

1. **Require a prior session** — read `$CLAUDE_PROJECT_DIR/.claude/.session-id` (fall back to `$PWD/.claude/.session-id`). If absent, **stop**:
   > Error: no prior session found at `.claude/.session-id`. Run `/wip-setup` for a fresh session with no continuation.

   Let `<prior-id>` be the sentinel value.

2. **Close the prior session** (load-bearing). All report paths below are under `/Users/peter/Development/FR-YAC/`:
   - **Prior dir missing** — if `reports/<prior-id>/` doesn't exist, **stop** (don't fabricate state):
     > Error: prior session dir `reports/<prior-id>/` not found. `/wip-wake` won't fabricate state. Restore the dir, or `rm .claude/.session-id` and run `/wip-setup` for a fresh discontinuous start.
   - Read the `status:` field from `reports/<prior-id>/session.md` frontmatter (local read). Missing or malformed frontmatter → treat as `active` (conservative default; the rewrite below regenerates well-formed frontmatter).
   - **Already `status: closed`** (operator ran `/wip-report session-end`, or a previous `/wip-wake` already closed it) → **skip the close phase**: do NOT recompose the body, do NOT touch `ended_at`, do NOT overwrite the hand-written `## Session Summary`. Go to step 3.
   - **Otherwise** (`active` or missing) — compute `<close_ts>` once, then **atomically rewrite** `reports/<prior-id>/session.md` (read full content, modify, write to a temp file, `mv` over the original — POSIX-atomic; never truncate-in-place): set `status: closed` + `ended_at: <close_ts>` in frontmatter (regenerating `session_id` / `role` / `started_at` from `<prior-id>` if frontmatter was absent — `continues_from` cannot be recovered this way; that loss is acknowledged), preserve the existing body, and append `## Session Summary — auto-closed by /wip-wake (<close_ts>)`. The frontmatter flip and the summary append are **one** atomic write so a partial failure can't leave a half-state.
   - Mirror the now-closed prior to kb: `python3 /Users/peter/Development/FR-YAC/tools/add-to-kb.py "reports/<prior-id>/session.md"`. **kb-unreachable → warn-and-continue** — the local write already flipped `status: closed`, so a later re-run sees `closed` and skips; the manual retry is the same `add-to-kb.py` command.

3. **Mint the new session** — `ROLE="$(cat "$CLAUDE_PROJECT_DIR/.claude/.session-role")"; NEW_ID="$ROLE-$(date '+%Y%m%d-%H%M%S')"`. (Role source is identical to `/wip-setup`. If `.session-role` is missing, stop and re-run the scaffold script with `--refresh`.)

4. **Create the new report dir + session.md** — `mkdir "reports/$NEW_ID"` (plain `mkdir`, not `-p`; on collision, surface and retry). Write the frontmatter:
   ```yaml
   ---
   session_id: <NEW_ID>
   role: <ROLE>
   started_at: <ISO-8601 derived from NEW_ID's YYYYMMDD-HHMMSS>
   status: active
   continues_from: <prior-id>
   ---
   ```

5. **Overwrite the sentinel atomically** — write `<NEW_ID>` as a single line to a temp file under `.claude/`, then `mv` over `.claude/.session-id`.

6. **Mirror the new session to kb (warn-and-continue)** — `python3 /Users/peter/Development/FR-YAC/tools/add-to-kb.py "reports/$NEW_ID/session.md"`. The loader derives the `CONTINUES_FROM` edge (new → prior) from the `continues_from:` frontmatter; if the prior isn't in kb yet, the edge silently skips and lands on a re-run. Both kb writes (step 2 close + step 6 create) are idempotent — re-running `/wip-wake` after a partial failure converges.

After Step A, `.claude/.session-id` holds `<NEW_ID>`. Every **write** from here on goes to the new session's dir; the continuity **reads** in Step B target the **prior** session's reports.

### Step B — Recover context

The rollover is done. Now rebuild working memory from durable artifacts — reading the **prior** session (`<prior-id>`) for continuity, since the new session's dir is still empty.

#### Why this exists

Every long session hits context compaction. Every new session starts cold. Without a defined recovery process, every Claude instance reinvents context recovery — reading random files, guessing at progress, repeating completed work. This command codifies what recovery looks like.

#### Key principle

This command relies ONLY on durable artifacts — files on disk, git history, WIP state. It never assumes anything from a previous conversation. If it's not written down, it doesn't exist.

#### Recovery steps

#### 1. Reload baseline context (mandatory)

Compaction wipes prior reads. The same baseline that `/wip-setup` enforces at session start must be reloaded here as concrete tool calls — do not substitute "I remember from training" for actually running the reads:

- `Read` `docs/Vision.md` — the theses; without them, drift toward use-case-specific solutions becomes invisible.
- `ReadMcpResourceTool server=wip uri=wip://ponifs` — the eight PoNIFs (#7 Edge Types and #8 `versioned: false` added 2026-04-25). Conventional assumptions cause silent failures against these.
- `ReadMcpResourceTool server=wip uri=wip://data-model` — what entities exist in WIP and how they're shaped.
- `ReadMcpResourceTool server=wip uri=wip://conventions` — bulk-first 200 OK, PATCH semantics, idempotent bootstrap, template cache, namespace/authorization rules.

Output one line per source confirming it was loaded. This step is non-optional; recovery without baseline context is recovery into the same drift the previous session ended in.

#### 2. Check session reports

Read the **prior** session's report dir at `/Users/peter/Development/FR-YAC/reports/<prior-id>/` (the session you just closed in Step A — that's where the continuity lives; the new session's dir is still empty). Three files together rebuild the session's working memory:

- `session.md` — current state (last `/wip-report session-end` snapshot or initial frontmatter).
- `commits.md` — append-only commit log since session start.
- `session-updates.md` — append-only running log of session-meaningful work that didn't have a commit anchor or fireside (discoveries during reading, scope-trim rationale, pre-compaction snapshots, block/unblock state). Written by `/wip-report update-session`, one file per session.

These are newer than git history (they capture in-progress reasoning that hasn't been committed) and richer than chat (they survived compaction).

#### 3. Check git state
```
git log --oneline -20    # What was committed recently?
git status               # Any uncommitted work?
git diff --stat          # What's changed but not committed?
```

Uncommitted changes are the most fragile state — they survived compaction only because they're on disk, but they haven't been saved to git yet. Note them carefully.

#### 4. Identify the active component
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

#### 5. Check for design documents
If recent commits reference a feature, check `docs/design/` for the relevant design document. This gives context on intent and scope.

#### 6. Report to user
Present a concise recovery summary:

```
Context Recovery Summary:

Branch: develop
Last commit: "Fix reporting-sync template metadata sync" (3 hours ago)
Uncommitted: changes to components/reporting-sync/src/sync.py

Active component: reporting-sync
Related design doc: docs/design/... (if applicable)

Suggested next step: [based on evidence]
```

Ask the user to confirm before proceeding. They may have context you can't recover from artifacts alone.

### When to use this

- **After context compaction** — you notice gaps in your understanding of the current work
- **At the start of any session** — especially if you're not sure what was done previously
- **When confused** — if something doesn't make sense, recover context before guessing

### What this is NOT

This is not a substitute for committing work and writing documentation. If the previous session didn't commit and didn't document, recovery will be incomplete.
