First-run environment check and guided setup. Run this the first time you open a WIP repo, or whenever something isn't working.

**Key principle:** stop at the first real problem. Don't overwhelm the user with cascading failures when fixing the first one would resolve the rest. Each check builds on the previous.

### Checks (in order)

1. **Python venv** — `.venv/bin/python --version`. If missing or broken, offer to create/recreate.
2. **MCP server deps** — `PYTHONPATH=components/mcp-server/src .venv/bin/python -c "import wip_mcp"`. If import fails, offer `pip install -e components/mcp-server/`.
3. **`.env` file** — `test -f .env`. If missing, point at `./scripts/setup.sh --preset standard --localhost` and `docs/development-guide.md` for preset options. If present, report key settings (WIP_HOSTNAME, WIP_AUTH_MODE, preset).
4. **Container runtime** — `command -v podman || command -v docker`. If neither, suggest `brew install podman` (Mac) or Docker.
5. **WIP containers running** — `podman ps` (or `docker ps`) filtered to `wip-` prefix. If none, point at `./scripts/start.sh` or `./scripts/setup.sh`. If some, list and flag any expected-but-missing services.
6. **MCP connectivity** — call `get_wip_status` via MCP tools. If MCP tools aren't available, suggest restarting Claude Code and checking `.mcp.json`. If the call fails, suggest checking containers.

### Output

After each check: pass/fail with the relevant detail (version, count, error).

On first failure: stop, show what failed, give the exact next command to run, and tell the user to re-run `/setup` after fixing.

On all pass: report each check OK with detail, and suggest `/wip-status` for data state or `/roadmap` for priorities.

### When to use

- First time opening the repo
- After cloning on a new machine
- When MCP tools aren't working — diagnose the problem
- After running `setup.sh` — verify everything is wired up
