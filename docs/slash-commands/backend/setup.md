First-run environment check, guided setup, and **mandatory context loading**. Run this at the start of every session — it is the mechanism that enforces CLAUDE.md §1's mandatory reading.

**Key principle:** stop at the first real problem on environment checks. Don't overwhelm the user with cascading failures when fixing the first one would resolve the rest. But do NOT skip the mandatory reading step on success — reading the baseline documents is the point of `/setup` at session start, not just environment verification.

### Checks (in order)

1. **Python venv** — `.venv/bin/python --version`. If missing or broken, offer to create/recreate.
2. **MCP server deps** — `PYTHONPATH=components/mcp-server/src .venv/bin/python -c "import wip_mcp"`. If import fails, offer `pip install -e components/mcp-server/`.
3. **`.env` file** — `test -f .env`. If missing, point at `./scripts/setup.sh --preset standard --localhost` and `docs/development-guide.md` for preset options. If present, report key settings (WIP_HOSTNAME, WIP_AUTH_MODE, preset).
4. **Container runtime** — `command -v podman || command -v docker`. If neither, suggest `brew install podman` (Mac) or Docker.
5. **WIP containers running** — `podman ps` (or `docker ps`) filtered to `wip-` prefix. If none, point at `./scripts/start.sh` or `./scripts/setup.sh`. If some, list and flag any expected-but-missing services.
6. **MCP connectivity** — call `get_wip_status` via MCP tools. If MCP tools aren't available, suggest restarting Claude Code and checking `.mcp.json`. If the call fails, suggest checking containers.

### Step 7 — Mandatory context loading (required on all-pass)

After the six environment checks pass, **actually load the baseline context** into the current session. This is not optional. Skipping it or "remembering from training" is the specific failure mode this step exists to prevent.

Perform each of the following as concrete tool calls:

- `Read` `docs/Vision.md` — the theses and design principles that drive every architecture decision. Every design principle in CLAUDE.md §3 traces back here. If any future work feels like it might drift toward a specific use case at the expense of WIP's generic engine, this document is the correction mechanism.
- `ReadMcpResourceTool server=wip uri=wip://ponifs` — the six Powerful, Non-Intuitive Features. Conventional assumptions will cause silent failures against these.
- `ReadMcpResourceTool server=wip uri=wip://data-model` — authoritative data model (field types, reference types, templates, terminologies, documents, ontology relationships).
- `ReadMcpResourceTool server=wip uri=wip://conventions` — bulk-first 200 OK, PATCH semantics, idempotent bootstrap, template cache TTL, namespace/authorization rules.

After each call, output one line confirming the source was loaded. Do not summarise the content at this step — the content is now in context where it belongs; let the subsequent work use it.

### Output

After each environment check: pass/fail with the relevant detail (version, count, error).

On first environment-check failure: stop, show what failed, give the exact next command to run, and tell the user to re-run `/setup` after fixing. **Do NOT perform Step 7** — the environment isn't ready.

On all environment checks passing: proceed to Step 7 (mandatory context loading). After Step 7, report each read OK and suggest `/wip-status` for data state or `/roadmap` for priorities.

### When to use

- **Start of every session** — always, for the mandatory reading step, even when the environment hasn't changed.
- First time opening the repo.
- After cloning on a new machine.
- When MCP tools aren't working — diagnose the problem.
- After running `setup.sh` — verify everything is wired up.

### Why the reading step is part of `/setup`

CLAUDE.md §1 lists Vision.md and the three MCP resources as mandatory first-four-minutes reading. Text in CLAUDE.md is an instruction; it depends on the agent voluntarily reading and following it. `/setup` is something the agent actually runs — the reading happens as a mechanical output of the command, not as a discretionary re-read. The rule moves from "aspirational instruction" to "enforced tool call."

This is the same pattern as WIP's contract tests (`components/mcp-server/tests/test_client_contracts.py`): turn a failure mode (agent skips a document it should have read) into a guard (the command's execution includes the read). Peter's framing: *turn the failure mode into the regression guard.*
