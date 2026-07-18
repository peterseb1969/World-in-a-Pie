Roll the current session over into a fresh, linked one and recover context. Use `/wip-wake` after a `/clear`, a compaction reset, the built-in `/resume` of an old transcript, or any time the operator decides "context lost, continuity matters." It closes the prior session (so kb doesn't accrete zombie `active` records), mints a new session ID whose `continues_from` points back at the prior, then reloads context from durable artifacts. (For a brand-new session with no predecessor, use `/wip-setup`.)

### Step A — Roll the session over (deterministic — run the script)

Identity is **local-first**: `.claude/.session-id` is the single source of truth; kb is a derived mirror. Step A is a fully mechanical state machine, so it is executed by a script, not hand-walked step by step. Run it once:

```bash
python3 .claude/scripts/wake-rollover.py
```

It closes the prior session if still active (atomic frontmatter flip + auto-close summary, skipped when already `closed`), mints `<ROLE>-<YYYYMMDD-HHMMSS>` from `.claude/.session-role`, creates `reports/<NEW_ID>/session.md` with `continues_from`, atomically swaps the sentinel, and mirrors both sessions to kb (tier-gated: skipped silently without `.claude/kb.json`; kb-unreachable warns and continues — local writes are authoritative and a re-run converges). It prints the machine-readable contract on stdout:

```
PRIOR_ID=<prior-id>
NEW_ID=<NEW_ID>
```

**Do not re-implement the rollover by hand** — the full state machine (every edge case: missing sentinel, missing prior dir + the legacy shared-path transition note, malformed frontmatter regeneration, collision retry, partial-failure convergence) lives in the script's docstring and its test suite (`agent-scripts/`, `./scripts/wip-test.sh agent-scripts`). On a non-zero exit, read the script's error message: missing sentinel → run `/wip-setup`; missing `reports/<prior-id>/` → resolve per the message (never fabricate state); missing `.session-role` → re-run `scripts/setup-backend-agent.sh` on this clone (there is NO `--refresh` flag; the scaffolds auto-detect mode).

After Step A, `.claude/.session-id` holds `<NEW_ID>`. Every **write** from here on goes to the new session's dir; the continuity **reads** in Step B target the **prior** session's reports (use the `PRIOR_ID` the script printed).

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

Read the **prior** session's report dir at `reports/<prior-id>/` (the session you just closed in Step A — that's where the continuity lives; the new session's dir is still empty). Three files together rebuild the session's working memory:

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
