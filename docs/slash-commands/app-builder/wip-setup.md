First-run session-identity mint, environment check, guided setup, and **mandatory context loading**. Run this at the start of every fresh APP-YAC session — it mints your session ID and enforces CLAUDE.md's "read before you write" discipline. (After a `/clear`, compaction reset, or built-in `/resume`, use `/wip-wake` instead — it continues the prior session's lineage.)

**Key principle:** stop at the first real problem on environment checks. Don't cascade failures when fixing the first one resolves the rest. But do NOT skip the mandatory reading step on success — loading the baseline context is the point of `/wip-setup`, not just environment verification.

### Step 0 — Session identity (run before the environment checks)

`/wip-setup` mints this session's identity. Identity is a **local-first** contract: the sentinel file `.claude/.session-id` is the single source of truth for "who am I"; kb is a derived mirror that catches up later. Control-flow decisions here read local files only — never query kb (it may be unreachable).

1. **Precondition** — `test -d /Users/peter/Development/FR-YAC/reports/`. If it doesn't exist, stop with: *"Clone FR-YAC first: `git clone <gitea-or-origin> ~/Development/FR-YAC`. `/wip-setup` writes session reports to `FR-YAC/reports/<session-id>/`."*

2. **Read the role** — `cat "$CLAUDE_PROJECT_DIR/.claude/.session-role"` (fall back to `$PWD/.claude/.session-role` if `$CLAUDE_PROJECT_DIR` is unset). This file is written at scaffold time — `BE-YAC` by `setup-backend-agent.sh`, `APP-<X>` (e.g. `APP-KB`, `APP-RC`) by `create-app-project.sh --prefix`. If it's missing, stop and tell the operator to re-run `create-app-project.sh --refresh --prefix APP-<X>`; do **not** guess the role.

3. **Check for an existing session** — read `$CLAUDE_PROJECT_DIR/.claude/.session-id`:
   - **Absent** → clean fresh start; go to step 4.
   - **Present** → read `<prior-id>` from it, then read the `status:` field from `/Users/peter/Development/FR-YAC/reports/<prior-id>/session.md` frontmatter (local read — do NOT query kb):
     - `status: closed` → the operator deliberately ended the prior session; go to step 4 and mint with **no** `continues_from` (discontinuous restart). The old sentinel is overwritten in step 5.
     - `status: active` (or any non-closed / missing) → **stop**; refuse to rotate identity silently:
       > Error: active session `<prior-id>` found at `.claude/.session-id`. Run `/wip-wake` to start a new linked session, or `/wip-report session-end` first, then `/wip-setup` for a clean discontinuous restart.

4. **Mint** — `ID="$(cat "$CLAUDE_PROJECT_DIR/.claude/.session-role")-$(date '+%Y%m%d-%H%M%S')"`. Seconds precision; the suffix is two hyphen-separated tokens (`YYYYMMDD-HHMMSS`) — this is what eliminates the same-minute collision class.

5. **Write the sentinel atomically** — write `$ID` as a single line (no trailing content) to a temp file under `.claude/`, then `mv` it over `.claude/.session-id`. Truncate-in-place is not atomic; use tempfile + `mv`.

6. **Create the report dir** — `mkdir "/Users/peter/Development/FR-YAC/reports/$ID"` (plain `mkdir`, **not** `-p`; with seconds precision a collision is near-zero, and if `mkdir` fails because the dir exists, surface it and let the operator retry). Write the initial `reports/$ID/session.md` with this frontmatter:
   ```yaml
   ---
   session_id: <ID>
   role: <ROLE>
   started_at: <ISO-8601 derived from the ID's YYYYMMDD-HHMMSS>
   status: active
   ---
   ```
   `continues_from` and `ended_at` are absent — `/wip-setup` never sets them (that's `/wip-wake`'s and `/wip-report session-end`'s job). Add a short body stub (task list, phase) as work begins.

7. **Mirror to kb (warn-and-continue)** — `python3 /Users/peter/Development/FR-YAC/tools/add-to-kb.py "/Users/peter/Development/FR-YAC/reports/$ID/session.md"`. If kb is unreachable, log to stderr and **PROCEED** — local state is authoritative; the mirror retries at the next `/wip-wake` or `/wip-report session-end`:
   > Warning: kb mirror failed for `<ID>`; SESSION record not yet in kb. Will retry at next `/wip-wake`, `/wip-report session-end`, or manually via `python3 /Users/peter/Development/FR-YAC/tools/add-to-kb.py /Users/peter/Development/FR-YAC/reports/<ID>/session.md`.

After Step 0, `.claude/.session-id` is the canonical identity for every subsequent `/wip-case`, `/wip-report`, and commit attribution. Proceed to the environment checks below.

### Checks (in order)

1. **Node version** — `node --version`. Expect 20.x+ (matches the canonical `node:20-alpine` Dockerfile.dev base). Older versions may work but are off-contract.
2. **Package manager + deps** — `command -v npm` then check `node_modules/` exists and is non-empty. If missing, suggest `npm ci` (or `npm install` if no `package-lock.json` yet).
3. **`.env` file** — `test -f .env`. If missing, point at this app's CLAUDE.md "API Key" section and ask Peter for the runtime key. Confirm `WIP_API_KEY` is set (don't print the value).
4. **WIP reachable** — `curl -sk -m 3 https://localhost:8443/api/registry/namespaces -H "X-API-Key: $(grep ^WIP_API_KEY .env | cut -d= -f2)"` (or the install host this app targets). If unreachable, point at `wip-deploy install` or `wip-deploy restart`.
5. **MCP connectivity** — call `get_wip_status` via MCP tools. If MCP tools aren't available, suggest restarting Claude Code and checking `.mcp.json`. If the call fails, suggest checking containers / network.

### Step 6 — Mandatory context loading (required on all-pass)

After the five environment checks pass, **actually load the baseline context** into the current session. This is not optional. Skipping it or "remembering from training" is the specific failure mode this step exists to prevent.

Perform each of the following as concrete tool calls:

- `Read` `/Users/peter/Development/World-in-a-Pie/docs/Vision.md` — WIP's theses and design principles. Every architectural decision traces back here. If any work feels like it might drift toward a specific use case at the expense of WIP's generic engine, this is the correction mechanism.
- `ReadMcpResourceTool server=wip uri=wip://ponifs` — the six Powerful, Non-Intuitive Features. Conventional assumptions cause silent failures against these.
- `ReadMcpResourceTool server=wip uri=wip://data-model` — authoritative data model (field types, reference types, templates, terminologies, documents, ontology relations).
- `ReadMcpResourceTool server=wip uri=wip://conventions` — bulk-first 200 OK, PATCH semantics, idempotent bootstrap, template cache TTL, namespace / authorization rules.
- `Read` `/Users/peter/Development/FR-YAC/papers/wip-deployable-app-contract.md` — what your app must satisfy to ship under `wip-deploy install`. Synthesizes the May 2026 cross-host + WIP-KB containerization work into a one-page checklist. Mandatory because skipping the contract is a multi-day retrofit; reading it once is a 30-minute scaffold tax.

After each call, output one line confirming the source was loaded. Do not summarise the content at this step — the content is now in context where the subsequent work can use it.

### Output

After each environment check: pass/fail with the relevant detail (version, count, error).

On first environment-check failure: stop, show what failed, give the exact next command to run, and tell Peter to re-run `/wip-setup` after fixing. **Do NOT perform Step 6** — the environment isn't ready.

On all checks passing: proceed to Step 6 (mandatory context loading). After Step 6, report each read OK and suggest the next action based on context (`/wip-explore` for a new app, `/wip-implement` if a design model exists, `/wip-bootstrap` for namespace seeding).

### When to use

- **Start of every session** — always, for the mandatory reading step, even when the environment hasn't changed.
- First time opening the repo (new APP-YAC after `create-app-project.sh`).
- After cloning on a new machine.
- When MCP tools aren't working — diagnose the problem.
- After `wip-deploy install` brought WIP up — verify everything is wired up.

### Why the reading step is part of `/wip-setup`

CLAUDE.md asks you to read the baseline documents and the wip-deployable contract at session start. Text in CLAUDE.md is an instruction — it depends on you voluntarily reading and following it. `/wip-setup` is something you actually run — the reading happens as a mechanical output of the command, not as a discretionary re-read. The rule moves from "aspirational instruction" to "enforced tool call."

This is the same pattern as WIP's contract tests: turn a failure mode (agent skips a document it should have read) into a guard (the command's execution includes the read). Peter's framing: *turn the failure mode into the regression guard.*
