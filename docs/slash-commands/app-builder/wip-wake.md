Roll the current session over into a fresh, linked one and recover context. Use `/wip-wake` after a `/clear`, a compaction reset, the built-in `/resume` of an old transcript, or any time the operator decides "context lost, continuity matters." It closes the prior session (so kb doesn't accrete zombie `active` records), mints a new session ID whose `continues_from` points back at the prior, then reloads context from durable artifacts. (For a brand-new session with no predecessor, use `/wip-setup`.)

### Step A — Roll the session over (run before context reload)

Identity is **local-first**: `.claude/.session-id` is the single source of truth; kb is a derived mirror. Every control-flow decision here reads local files only — never query kb.

1. **Require a prior session** — read `$CLAUDE_PROJECT_DIR/.claude/.session-id` (fall back to `$PWD/.claude/.session-id`). If absent, **stop**:
   > Error: no prior session found at `.claude/.session-id`. Run `/wip-setup` for a fresh session with no continuation.

   Let `<prior-id>` be the sentinel value.

2. **Close the prior session** (load-bearing). Report paths below are **project-local** to this repo — `reports/<prior-id>/`, never the shared FR-YAC checkout (CASE-467):
   - **Prior dir missing** — if `reports/<prior-id>/` doesn't exist, **stop** (don't fabricate state). **Transition note (CASE-467):** a session staged *before* this change lives in the legacy shared `/Users/peter/Development/FR-YAC/reports/<prior-id>/`. To carry such a session forward cleanly, do ONE of these *before* this `/wip-wake`: run `/wip-report session-end` under the old body (final state mirrors to kb), **or** move that legacy dir into this repo's `reports/`. New sessions never touch the shared path; this note retires once no pre-CASE-467 sessions remain.
     > Error: prior session dir `reports/<prior-id>/` not found. `/wip-wake` won't fabricate state. If this session was staged before CASE-467, see the transition note above. Otherwise restore the dir, or `rm .claude/.session-id` and run `/wip-setup` for a fresh discontinuous start.
   - Read the `status:` field from `reports/<prior-id>/session.md` frontmatter (local read). Missing or malformed frontmatter → treat as `active` (conservative default; the rewrite below regenerates well-formed frontmatter).
   - **Already `status: closed`** (operator ran `/wip-report session-end`, or a previous `/wip-wake` already closed it) → **skip the close phase**: do NOT recompose the body, do NOT touch `ended_at`, do NOT overwrite the hand-written `## Session Summary`. Go to step 3.
   - **Otherwise** (`active` or missing) — compute `<close_ts>` once (`date '+%Y-%m-%dT%H:%M:%S'` — naive, NO timezone suffix), then **atomically rewrite** `reports/<prior-id>/session.md` (read full content, modify, write to a temp file, `mv` over the original — POSIX-atomic; never truncate-in-place): set `status: closed` + `ended_at: <close_ts>` in frontmatter (regenerating `session_id` / `role` / `started_at` from `<prior-id>` if frontmatter was absent — `continues_from` cannot be recovered this way; that loss is acknowledged), preserve the existing body, and append `## Session Summary — auto-closed by /wip-wake (<close_ts>)`. The frontmatter flip and the summary append are **one** atomic write so a partial failure can't leave a half-state.
   - Mirror the now-closed prior to kb (tier 3 only — skip silently if `.claude/kb.json` is absent, CASE-463): `KB_URL="$(python3 -c 'import json;print(json.load(open(".claude/kb.json"))["kb_app_url"])')"; KB_KEYFILE="$(python3 -c 'import json;print(json.load(open(".claude/kb.json"))["kb_api_key_file"])')"; ( cd reports/<prior-id> && python3 -c 'import json,glob; print(json.dumps({"session_id":"<prior-id>","files":{f:open(f).read() for f in sorted(glob.glob("*.md"))}}))' | curl -fsSk -X POST "$KB_URL/apps/kb/server-api/kb/sessions/mirror" -H "X-API-Key: $(cat "$KB_KEYFILE")" -H "Content-Type: application/json" -d @- )`. **kb-unreachable → warn-and-continue** — the local write already flipped `status: closed`, so a later re-run sees `closed` and skips; the manual retry is re-running the same POST.

3. **Mint the new session** — `ROLE="$(cat "$CLAUDE_PROJECT_DIR/.claude/.session-role")"; NEW_ID="$ROLE-$(date '+%Y%m%d-%H%M%S')"`. (Role source is identical to `/wip-setup`. If `.session-role` is missing, stop and re-run `create-app-project.sh --refresh --prefix APP-<X>`.)

4. **Create the new report dir + session.md** — `mkdir "reports/$NEW_ID"` (plain `mkdir`, not `-p`; on collision, surface and retry). Write the frontmatter:
   ```yaml
   ---
   session_id: <NEW_ID>
   role: <ROLE>
   started_at: <NEW_ID's YYYYMMDD-HHMMSS as a naive datetime, YYYY-MM-DDTHH:MM:SS, NO timezone suffix>
   status: active
   continues_from: <prior-id>
   ---
   ```

5. **Overwrite the sentinel atomically** — write `<NEW_ID>` as a single line to a temp file under `.claude/`, then `mv` over `.claude/.session-id`.

6. **Mirror the new session to kb (tier 3 only, warn-and-continue)** — **Tier gate (CASE-463):** kb mirrors run only in tier-3 repos — if `.claude/kb.json` is absent, skip this step silently and continue (tier-2 solo mode is by design; nothing to warn about). Then: `KB_URL="$(python3 -c 'import json;print(json.load(open(".claude/kb.json"))["kb_app_url"])')"; KB_KEYFILE="$(python3 -c 'import json;print(json.load(open(".claude/kb.json"))["kb_api_key_file"])')"; ( cd reports/$NEW_ID && python3 -c 'import json,glob; print(json.dumps({"session_id":"$NEW_ID","files":{f:open(f).read() for f in sorted(glob.glob("*.md"))}}))' | curl -fsSk -X POST "$KB_URL/apps/kb/server-api/kb/sessions/mirror" -H "X-API-Key: $(cat "$KB_KEYFILE")" -H "Content-Type: application/json" -d @- )`. The gateway derives the `CONTINUES_FROM` edge (new → prior) from the `continues_from:` frontmatter; if the prior isn't in kb yet, the edge silently skips and lands on a re-run. Both kb writes (step 2 close + step 6 create) are idempotent — re-running `/wip-wake` after a partial failure converges.

After Step A, `.claude/.session-id` holds `<NEW_ID>`. Every **write** from here on goes to the new session's dir; the continuity **reads** in Step B target the **prior** session's reports.

### Step B — Recover context

The rollover is done. Now rebuild working memory from durable artifacts — reading the **prior** session (`<prior-id>`) for continuity, since the new session's dir is still empty.

#### Why this exists

Every long session hits context compaction. Every new session starts cold. Without a defined recovery process, every Claude instance reinvents context recovery — reading random files, guessing at progress, repeating completed work. This command codifies what recovery looks like.

#### Key principle

This command relies ONLY on durable artifacts — files on disk, git history, WIP state. It never assumes anything from a previous conversation. If it's not written down, it doesn't exist.

#### Recovery steps

#### 1. Reload baseline context (mandatory)

Compaction wipes prior reads. As an APP-YAC, you must reload baseline context as concrete tool calls — do not substitute "I remember from training" for actually running the reads:

- `ReadMcpResourceTool server=wip uri=wip://development-guide` — the four-phase process for building on WIP. **Golden Rule: Never modify WIP. Only consume its APIs.** This is the single most important boundary for an APP-YAC; reload it.
- `ReadMcpResourceTool server=wip uri=wip://ponifs` — the eight PoNIFs (#7 Edge Types and #8 `versioned: false` added 2026-04-25). Conventional assumptions cause silent failures.
- `ReadMcpResourceTool server=wip uri=wip://data-model` — entity shapes in WIP.
- `ReadMcpResourceTool server=wip uri=wip://conventions` — bulk-first 200 OK, PATCH semantics, idempotent bootstrap, template cache, namespace/authorization rules.

Output one line per source confirming it was loaded. This step is non-optional; recovery without baseline context is recovery into the same drift the previous session ended in.

#### 2. Check session reports

Read the **prior** session's report dir at `reports/<prior-id>/` (the session you just closed in Step A — that's where the continuity lives; the new session's dir is still empty). Three files together rebuild the session's working memory:

- `session.md` — current state (last `/wip-report session-end` snapshot or initial frontmatter).
- `commits.md` — append-only commit log since session start.
- `session-updates.md` — append-only running log of session-meaningful work that didn't have a commit anchor or fireside (discoveries during reading, scope-trim rationale, pre-compaction snapshots, block/unblock state). Written by `/wip-report update-session`, one file per session.

These are newer than git history (they capture in-progress reasoning that hasn't been committed) and richer than chat (they survived compaction).

#### 3. Check durable documentation
Read the app's documentation files (if they exist):
- `README.md` — what this app does
- `ARCHITECTURE.md` — how it's structured, key decisions
- `WIP_DEPENDENCIES.md` — which WIP entities it uses
- `KNOWN_ISSUES.md` — what's broken or deferred
- `CHANGELOG.md` — what changed recently

If none of these exist, you're likely in early phases (before Phase 4).

#### 4. Check git state
```
git log --oneline -20    # What was committed recently?
git status               # Any uncommitted work?
git diff --stat          # What's changed but not committed?
```

Uncommitted changes are the most fragile state — they survived compaction only because they're on disk, but they haven't been saved to git yet. Note them carefully.

#### 5. Check WIP state
Run the same checks as `/wip-status`:
- `get_wip_status` — are services healthy?
- `list_terminologies` — what vocabularies exist?
- `list_templates` — what document schemas exist?
- `query_by_template(template_value)` for each active template — how many documents?

#### 6. Check seed files
If `data-model/` exists:
- Compare seed files against WIP state
- If they match: Phases 2-3 are complete
- If WIP has entities not in seed files: either Phase 3 was done without export, or work is in progress

#### 7. Determine current phase
Use the evidence to determine where you are:

| Evidence | Phase |
|---|---|
| No terminologies/templates in WIP beyond defaults | Phase 1 (Exploratory) or not started |
| Terminologies exist but no templates | Phase 2 (Design) in progress or Phase 3 (Implementation) started |
| Templates and test documents exist | Phase 3 complete or in progress |
| App scaffold exists in `src/` | Phase 4 (Application Layer) in progress |
| App has multiple committed features, tests pass | Phase 4 complete, now in `/wip-improve` mode |

#### 8. Reconstruct task state
Based on all of the above, determine:
- What phase you're in
- What's been completed (committed work, data in WIP)
- What's in progress (uncommitted changes)
- What's next (the logical next step in the current phase)

#### 9. Report to user
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
