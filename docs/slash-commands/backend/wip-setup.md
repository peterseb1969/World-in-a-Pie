First-run session-identity mint, environment check, guided setup, and **mandatory context loading**. Run this at the start of every fresh session — it mints your session ID and is the mechanism that enforces CLAUDE.md §1's mandatory reading. (After a `/clear`, compaction reset, or built-in `/resume`, use `/wip-wake` instead — it continues the prior session's lineage.)

**Key principle:** stop at the first real problem on environment checks. Don't overwhelm the user with cascading failures when fixing the first one would resolve the rest. But do NOT skip the mandatory reading step on success — reading the baseline documents is the point of `/wip-setup` at session start, not just environment verification.

### Step 0 — Session identity (run before the environment checks)

`/wip-setup` mints this session's identity. Identity is a **local-first** contract: the sentinel file `.claude/.session-id` is the single source of truth for "who am I"; kb is a derived mirror that catches up later. Control-flow decisions here read local files only — never query kb (it may be unreachable).

1. **Precondition** — `test -d /Users/peter/Development/FR-YAC/reports/`. If it doesn't exist, stop with: *"Clone FR-YAC first: `git clone <gitea-or-origin> ~/Development/FR-YAC`. `/wip-setup` writes session reports to `FR-YAC/reports/<session-id>/`."*

2. **Read the role** — `cat "$CLAUDE_PROJECT_DIR/.claude/.session-role"` (fall back to `$PWD/.claude/.session-role` if `$CLAUDE_PROJECT_DIR` is unset). This file is written at scaffold time — `BE-YAC` by `setup-backend-agent.sh`, `APP-<X>` by `create-app-project.sh --prefix`. If it's missing, stop and tell the operator to re-run the setup script with `--refresh`; do **not** guess the role.

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
   started_at: <the ID's YYYYMMDD-HHMMSS as a naive datetime, YYYY-MM-DDTHH:MM:SS, NO timezone suffix>
   status: active
   ---
   ```
   `continues_from` and `ended_at` are absent — `/wip-setup` never sets them (that's `/wip-wake`'s and `/wip-report session-end`'s job). Add a short body stub (task list, phase) as work begins.

7. **Mirror to kb (warn-and-continue)** — `python3 /Users/peter/Development/FR-YAC/tools/add-to-kb.py "/Users/peter/Development/FR-YAC/reports/$ID/session.md"`. If kb is unreachable, log to stderr and **PROCEED** — local state is authoritative; the mirror retries at the next `/wip-wake` or `/wip-report session-end`:
   > Warning: kb mirror failed for `<ID>`; SESSION record not yet in kb. Will retry at next `/wip-wake`, `/wip-report session-end`, or manually via `python3 /Users/peter/Development/FR-YAC/tools/add-to-kb.py /Users/peter/Development/FR-YAC/reports/<ID>/session.md`.

After Step 0, `.claude/.session-id` is the canonical identity for every subsequent `/wip-case`, `/wip-report`, and commit attribution. Proceed to the environment checks below.

### Checks (in order)

1. **Python venv** — `.venv/bin/python --version`. If missing or broken, offer to create/recreate.
2. **MCP server deps** — `PYTHONPATH=components/mcp-server/src .venv/bin/python -c "import wip_mcp"`. If import fails, offer `pip install -e components/mcp-server/`.
3. **`.env` file** — `test -f .env`. If missing, point at `wip-deploy install --preset standard --target compose --hostname localhost` (see `wip-deploy examples` for the full surface) and `docs/development-guide.md` for preset options. If present, report key settings (WIP_HOSTNAME, WIP_AUTH_MODE, preset).
4. **Container runtime** — `command -v podman || command -v docker`. If neither, suggest `brew install podman` (Mac) or Docker.
5. **WIP containers running** — `podman ps` (or `docker ps`) filtered to `wip-` prefix. If none, point at `wip-deploy install` (fresh) or `wip-deploy restart` (existing install). If some, list and flag any expected-but-missing services.
6. **MCP connectivity** — call `get_wip_status` via MCP tools. If MCP tools aren't available, suggest restarting Claude Code and checking `.mcp.json`. If the call fails, suggest checking containers.

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

On first environment-check failure: stop, show what failed, give the exact next command to run, and tell the user to re-run `/wip-setup` after fixing. **Do NOT perform Step 7** — the environment isn't ready.

On all environment checks passing: proceed to Step 7 (mandatory context loading). After Step 7, report each read OK and suggest `/wip-status` for data state or `/wip-roadmap` for priorities.

### When to use

- **Start of every session** — always, for the mandatory reading step, even when the environment hasn't changed.
- First time opening the repo.
- After cloning on a new machine.
- When MCP tools aren't working — diagnose the problem.
- After running `wip-deploy install` — verify everything is wired up.

### Why the reading step is part of `/wip-setup`

CLAUDE.md §1 lists Vision.md and the three MCP resources as mandatory first-four-minutes reading. Text in CLAUDE.md is an instruction; it depends on the agent voluntarily reading and following it. `/wip-setup` is something the agent actually runs — the reading happens as a mechanical output of the command, not as a discretionary re-read. The rule moves from "aspirational instruction" to "enforced tool call."

This is the same pattern as WIP's contract tests (`components/mcp-server/tests/test_client_contracts.py`): turn a failure mode (agent skips a document it should have read) into a guard (the command's execution includes the read). Peter's framing: *turn the failure mode into the regression guard.*
