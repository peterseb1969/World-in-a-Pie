First-run session-identity mint, environment check, guided setup, and **mandatory context loading**. Run this at the start of every fresh session — it mints your session ID and is the mechanism that enforces CLAUDE.md §1's mandatory reading. (After a `/clear`, compaction reset, or built-in `/resume`, use `/wip-wake` instead — it continues the prior session's lineage.)

**Key principle:** stop at the first real problem on environment checks. Don't overwhelm the user with cascading failures when fixing the first one would resolve the rest. But do NOT skip the mandatory reading step on success — reading the baseline documents is the point of `/wip-setup` at session start, not just environment verification.

### Step 0 — Session pre-flight (read-only; the mint runs *after* the checks)

`/wip-setup` decides this session's identity here but **does not write it yet** — the mint is deferred until the environment checks pass, so a failed precheck never strands an `active` session that would then block the very re-run the failure message tells you to do. Identity is a **local-first** contract: the sentinel file `.claude/.session-id` is the single source of truth for "who am I"; kb is a derived mirror that catches up later. Control-flow decisions here read local files only — never query kb (it may be unreachable).

1. **Precondition** — ensure the project-local staging dir exists: `mkdir -p reports`. Sessions stage to `reports/<session-id>/` **inside this repo** (never a shared FR-YAC checkout); the durable record is the kb mirror (tier-3, written via `kb-write.py SESSION`). No external clone is required, and tier-2 (no-KB) repos keep their full session history locally here.

2. **Read the role** — `cat "$CLAUDE_PROJECT_DIR/.claude/.session-role"` (fall back to `$PWD/.claude/.session-role` if `$CLAUDE_PROJECT_DIR` is unset). This file is written at scaffold time — `BE-YAC` by `setup-backend-agent.sh`, `APP-<X>` by `create-app-project.sh --prefix`. If it's missing, stop and tell the operator to re-run the setup script with `--refresh`; do **not** guess the role.

3. **Check for an existing session** — read `$CLAUDE_PROJECT_DIR/.claude/.session-id` and decide the continuation mode (the mint below acts on it):
   - **Absent** → clean fresh start; the mint will create a session with no `continues_from`.
   - **Present** → read `<prior-id>` from it, then read the `status:` field from `reports/<prior-id>/session.md` frontmatter (local read — do NOT query kb):
     - `status: closed` → the operator deliberately ended the prior session; the mint will overwrite the old sentinel and set **no** `continues_from` (discontinuous restart).
     - `status: active` (or any non-closed / missing) → **stop here**; refuse to rotate identity silently:
       > Error: active session `<prior-id>` found at `.claude/.session-id`. Run `/wip-wake` to start a new linked session, or `/wip-report session-end` first, then `/wip-setup` for a clean discontinuous restart.

**Step 0 writes nothing** — it only reads the role and the sentinel and decides the continuation mode. If it didn't stop at step 3, proceed to the checks; the session is **minted only after they pass** (below).

### Checks (in order)

1. **Python venv** — `.venv/bin/python --version`. If missing or broken, offer to create/recreate.
2. **MCP server deps** — `PYTHONPATH=components/mcp-server/src .venv/bin/python -c "import wip_mcp"`. If import fails, offer `pip install -e components/mcp-server/`.
3. **`.env` file** — `test -f .env`. If missing, point at `wip-deploy install --preset standard --target compose --hostname localhost` (see `wip-deploy examples` for the full surface) and `docs/development-guide.md` for preset options. If present, report key settings (WIP_HOSTNAME, WIP_AUTH_MODE, preset).
4. **Container runtime** — `command -v podman || command -v docker`. If neither, suggest `brew install podman` (Mac) or Docker.
5. **WIP containers running** — `podman ps` (or `docker ps`) filtered to `wip-` prefix. If none, point at `wip-deploy install` (fresh) or `wip-deploy restart` (existing install). If some, list and flag any expected-but-missing services.
6. **MCP connectivity** — call `get_wip_status` via MCP tools. If MCP tools aren't available, suggest restarting Claude Code and checking `.mcp.json`. If the call fails, suggest checking containers.

### Step 6 — Mint the session (only after all checks pass)

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

4. **Mirror to kb (tier 3 only, warn-and-continue)** — **Tier gate:** kb mirrors run only in tier-3 repos — if `.claude/kb.json` is absent, skip this step silently and continue (tier-2 solo mode is by design; nothing to warn about). Otherwise ensure the served KB client is present, then write the SESSION record through it (the gateway upserts by `session_id`): `test -f ~/.cache/wip-kb-client/kb-client.sh || curl -fsSk -H "X-API-Key: $(cat "$(python3 -c 'import json;print(json.load(open(".claude/kb.json"))["kb_api_key_file"])')")" "$(python3 -c 'import json;print(json.load(open(".claude/kb.json"))["kb_app_url"])')/apps/kb/server-api/kb-client/install" | sh; bash ~/.cache/wip-kb-client/kb-client.sh kb-write.py SESSION reports/$ID/session.md`. If kb is unreachable, log to stderr and **PROCEED** — local state is authoritative; the mirror retries at the next `/wip-wake` or `/wip-report session-end`:
   > Warning: kb mirror failed for `<ID>`; SESSION record not yet in kb. Will retry at next `/wip-wake`, `/wip-report session-end`, or manually via `bash ~/.cache/wip-kb-client/kb-client.sh kb-write.py SESSION reports/<ID>/session.md`.

After the mint, `.claude/.session-id` is the canonical identity for every subsequent `/wip-case`, `/wip-report`, and commit attribution.

### Step 7 — Mandatory context loading (required on all-pass)

After the six environment checks pass, **actually load the baseline context** into the current session. This is not optional. Skipping it or "remembering from training" is the specific failure mode this step exists to prevent.

Perform each of the following as concrete tool calls:

- `Read` `docs/Vision.md` — the theses and design principles that drive every architecture decision. Every design principle in CLAUDE.md §3 traces back here. If any future work feels like it might drift toward a specific use case at the expense of WIP's generic engine, this document is the correction mechanism.
- `ReadMcpResourceTool server=wip uri=wip://ponifs` — the six Powerful, Non-Intuitive Features. Conventional assumptions will cause silent failures against these.
- `ReadMcpResourceTool server=wip uri=wip://data-model` — authoritative data model (field types, reference types, templates, terminologies, documents, ontology relations).
- `ReadMcpResourceTool server=wip uri=wip://conventions` — bulk-first 200 OK, PATCH semantics, idempotent bootstrap, template cache TTL, namespace/authorization rules.

After each call, output one line confirming the source was loaded. Do not summarise the content at this step — the content is now in context where it belongs; let the subsequent work use it.

### Output

After each environment check: pass/fail with the relevant detail (version, count, error).

On first environment-check failure: stop, show what failed, give the exact next command to run, and tell the user to re-run `/wip-setup` after fixing. **Do NOT mint (Step 6) and do NOT perform Step 7** — the environment isn't ready. Because the mint runs only after the checks pass, a failed check leaves **no** session behind, so the re-run works exactly as the failure message instructs.

On all environment checks passing: **mint the session (Step 6)**, then proceed to Step 7 (mandatory context loading). After Step 7, report each read OK and suggest `/wip-status` for data state or `/wip-roadmap` for priorities.

### When to use

- **Start of every session** — always, for the mandatory reading step, even when the environment hasn't changed.
- First time opening the repo.
- After cloning on a new machine.
- When MCP tools aren't working — diagnose the problem.
- After running `wip-deploy install` — verify everything is wired up.

### Why the reading step is part of `/wip-setup`

CLAUDE.md §1 lists Vision.md and the three MCP resources as mandatory first-four-minutes reading. Text in CLAUDE.md is an instruction; it depends on the agent voluntarily reading and following it. `/wip-setup` is something the agent actually runs — the reading happens as a mechanical output of the command, not as a discretionary re-read. The rule moves from "aspirational instruction" to "enforced tool call."

This is the same pattern as WIP's contract tests (`components/mcp-server/tests/test_client_contracts.py`): turn a failure mode (agent skips a document it should have read) into a guard (the command's execution includes the read). Peter's framing: *turn the failure mode into the regression guard.*
