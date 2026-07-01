First-run session-identity mint, environment check, guided setup, and **mandatory context loading**. Run this at the start of every fresh APP-YAC session — it mints your session ID and enforces CLAUDE.md's "read before you write" discipline. (After a `/clear`, compaction reset, or built-in `/resume`, use `/wip-wake` instead — it continues the prior session's lineage.)

**Key principle:** stop at the first real problem on environment checks. Don't cascade failures when fixing the first one resolves the rest. But do NOT skip the mandatory reading step on success — loading the baseline context is the point of `/wip-setup`, not just environment verification.

### Step 0 — Session pre-flight (read-only; the mint runs *after* the checks)

`/wip-setup` decides this session's identity here but **does not write it yet** — the mint is deferred until the environment checks pass, so a failed precheck never strands an `active` session that would then block the very re-run the failure message tells you to do. Identity is a **local-first** contract: the sentinel file `.claude/.session-id` is the single source of truth for "who am I"; kb is a derived mirror that catches up later. Control-flow decisions here read local files only — never query kb (it may be unreachable).

1. **Precondition** — ensure the project-local staging dir exists: `mkdir -p reports`. Sessions stage to `reports/<session-id>/` **inside this repo** (never a shared FR-YAC checkout); the durable record is the kb mirror (tier-3, written via `kb-write.py SESSION`). No external clone is required, and tier-2 (no-KB) repos keep their full session history locally here.

2. **Read the role** — `cat "$CLAUDE_PROJECT_DIR/.claude/.session-role"` (fall back to `$PWD/.claude/.session-role` if `$CLAUDE_PROJECT_DIR` is unset). This file is written at scaffold time — `BE-YAC` by `setup-backend-agent.sh`, `APP-<X>` (e.g. `APP-KB`, `APP-RC`) by `create-app-project.sh --prefix`. If it's missing, stop and tell the operator to re-run `create-app-project.sh --refresh --prefix APP-<X>`; do **not** guess the role.

3. **Check for an existing session** — read `$CLAUDE_PROJECT_DIR/.claude/.session-id` and decide the continuation mode (the mint below acts on it):
   - **Absent** → clean fresh start; the mint will create a session with no `continues_from`.
   - **Present** → read `<prior-id>` from it, then read the `status:` field from `reports/<prior-id>/session.md` frontmatter (local read — do NOT query kb):
     - `status: closed` → the operator deliberately ended the prior session; the mint will overwrite the old sentinel and set **no** `continues_from` (discontinuous restart).
     - `status: active` (or any non-closed / missing) → **stop here**; refuse to rotate identity silently:
       > Error: active session `<prior-id>` found at `.claude/.session-id`. Run `/wip-wake` to start a new linked session, or `/wip-report session-end` first, then `/wip-setup` for a clean discontinuous restart.

**Step 0 writes nothing** — it only reads the role and the sentinel and decides the continuation mode. If it didn't stop at step 3, proceed to the checks; the session is **minted only after they pass** (below).

### Checks (in order)

1. **Node version** — `node --version`. Expect 20.x+ (matches the canonical `node:20-alpine` Dockerfile.dev base). Older versions may work but are off-contract.
2. **Package manager + deps** — `command -v npm` then check `node_modules/` exists and is non-empty. If missing, suggest `npm ci` (or `npm install` if no `package-lock.json` yet).
3. **`.env` file** — `test -f .env`. If missing, point at this app's CLAUDE.md "API Key" section. The runtime key SOURCE is the **live wip-deploy secrets file** (CASE-495), referenced as `WIP_API_KEY_FILE` — not a baked `WIP_API_KEY`. Confirm `WIP_API_KEY_FILE` is set and the file it points at exists and is non-empty: `KF="$(grep ^WIP_API_KEY_FILE .env | cut -d= -f2)"; test -s "$KF"` (don't print the contents). A literal `WIP_API_KEY` is a legacy/local fallback — accept it if present, but prefer the file.
4. **WIP reachable** — resolve the key from the live file, not a baked value: `KEY="$(cat "$(grep ^WIP_API_KEY_FILE .env | cut -d= -f2)" 2>/dev/null || grep ^WIP_API_KEY .env | cut -d= -f2)"; curl -sk -m 3 https://localhost:8443/api/registry/namespaces -H "X-API-Key: $KEY"` (or the install host this app targets). If unreachable, point at `wip-deploy install` or `wip-deploy restart`.
5. **MCP connectivity** — call `get_wip_status` via MCP tools. If MCP tools aren't available, suggest restarting Claude Code and checking `.mcp.json`. If the call fails, suggest checking containers / network.

### Step 5 — Mint the session (only after all checks pass)

The environment is verified, so now write identity. A failed check above left **no** session behind — deferring the mint to here is the fix for the strand-on-failed-precheck bug: the "fix it and re-run `/wip-setup`" instruction works as written.

1. **Mint** — `ID="$(cat "$CLAUDE_PROJECT_DIR/.claude/.session-role")-$(date '+%Y%m%d-%H%M%S')"`. Seconds precision; the suffix is two hyphen-separated tokens (`YYYYMMDD-HHMMSS`) — this is what eliminates the same-minute collision class.

2. **Write the sentinel atomically** — write `$ID` as a single line (no trailing content) to a temp file under `.claude/`, then `mv` it over `.claude/.session-id`. Truncate-in-place is not atomic; use tempfile + `mv`.

3. **Create the report dir** — `mkdir "reports/$ID"` (plain `mkdir`, **not** `-p`; with seconds precision a collision is near-zero, and if `mkdir` fails because the dir exists, surface it and let the operator retry). Write the initial `reports/$ID/session.md` with this frontmatter:
   ```yaml
   ---
   session_id: <ID>
   role: <ROLE>
   started_at: <the ID's YYYYMMDD-HHMMSS as a naive datetime, YYYY-MM-DDTHH:MM:SS, NO timezone suffix>
   status: active
   ---
   ```
   `continues_from` and `ended_at` are absent — `/wip-setup` never sets them (that's `/wip-wake`'s and `/wip-report session-end`'s job). Add a short body stub (task list, phase) as work begins.

4. **Mirror to kb (tier 3 only, warn-and-continue)** — **Tier gate:** kb mirrors run only in tier-3 repos — if `.claude/kb.json` is absent, skip this step silently and continue (tier-2 solo mode is by design; nothing to warn about). Otherwise ensure the served KB client is present, then write the SESSION record through it (the gateway upserts by `session_id`): `test -f ~/.cache/wip-kb-client/kb-client.sh || curl -fsSk -H "X-API-Key: $(cat "$(python3 -c 'import json;print(json.load(open(".claude/kb.json"))["kb_api_key_file"])')")" "$(python3 -c 'import json;print(json.load(open(".claude/kb.json"))["kb_app_url"])')/apps/kb/server-api/kb-client/install" | sh; kbc kb-write.py SESSION reports/$ID/session.md`. If kb is unreachable, log to stderr and **PROCEED** — local state is authoritative; the mirror retries at the next `/wip-wake` or `/wip-report session-end`:
   > Warning: kb mirror failed for `<ID>`; SESSION record not yet in kb. Will retry at next `/wip-wake`, `/wip-report session-end`, or manually via `kbc kb-write.py SESSION reports/<ID>/session.md`.

After the mint, `.claude/.session-id` is the canonical identity for every subsequent `/wip-case`, `/wip-report`, and commit attribution.

### Step 6 — Mandatory context loading (required on all-pass)

After the five environment checks pass, **actually load the baseline context** into the current session. This is not optional. Skipping it or "remembering from training" is the specific failure mode this step exists to prevent.

Perform each of the following as concrete tool calls:

- `Read` `docs/Vision.md` — WIP's theses and design principles (bundled into this project by the scaffold, in `docs/`). Every architectural decision traces back here. If any work feels like it might drift toward a specific use case at the expense of WIP's generic engine, this is the correction mechanism.
- `ReadMcpResourceTool server=wip uri=wip://ponifs` — the six Powerful, Non-Intuitive Features. Conventional assumptions cause silent failures against these.
- `ReadMcpResourceTool server=wip uri=wip://data-model` — authoritative data model (field types, reference types, templates, terminologies, documents, ontology relations).
- `ReadMcpResourceTool server=wip uri=wip://conventions` — bulk-first 200 OK, PATCH semantics, idempotent bootstrap, template cache TTL, namespace / authorization rules.
- `Read` `docs/wip-deployable-app-contract.md` — what your app must satisfy to ship under `wip-deploy install`. Synthesizes the May 2026 cross-host + WIP-KB containerization work into a one-page checklist. Bundled into this project by the scaffold (in `docs/`). Mandatory because skipping the contract is a multi-day retrofit; reading it once is a 30-minute scaffold tax.

After each call, output one line confirming the source was loaded. Do not summarise the content at this step — the content is now in context where the subsequent work can use it.

### Output

After each environment check: pass/fail with the relevant detail (version, count, error).

On first environment-check failure: stop, show what failed, give the exact next command to run, and tell Peter to re-run `/wip-setup` after fixing. **Do NOT mint (Step 5) and do NOT perform Step 6** — the environment isn't ready. Because the mint runs only after the checks pass, a failed check leaves **no** session behind, so the re-run works exactly as the failure message instructs.

On all checks passing: **mint the session (Step 5)**, then proceed to Step 6 (mandatory context loading). After Step 6, report each read OK and suggest the next action based on context (`/wip-explore` for a new app, `/wip-implement` if a design model exists, `/wip-bootstrap` for namespace seeding).

### When to use

- **Start of every session** — always, for the mandatory reading step, even when the environment hasn't changed.
- First time opening the repo (new APP-YAC after `create-app-project.sh`).
- After cloning on a new machine.
- When MCP tools aren't working — diagnose the problem.
- After `wip-deploy install` brought WIP up — verify everything is wired up.

### Why the reading step is part of `/wip-setup`

CLAUDE.md asks you to read the baseline documents and the wip-deployable contract at session start. Text in CLAUDE.md is an instruction — it depends on you voluntarily reading and following it. `/wip-setup` is something you actually run — the reading happens as a mechanical output of the command, not as a discretionary re-read. The rule moves from "aspirational instruction" to "enforced tool call."

This is the same pattern as WIP's contract tests: turn a failure mode (agent skips a document it should have read) into a guard (the command's execution includes the read). Peter's framing: *turn the failure mode into the regression guard.*
