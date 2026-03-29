First-run environment check and guided setup. Run this the first time you open a WIP repo, or whenever something isn't working.

This command checks each prerequisite in order and stops at the first problem, offering to fix it. It does NOT try to debug — if something is missing, it tells the user what to do.

### Steps

#### 1. Python venv
Check if `.venv/` exists and has a working Python:
```bash
.venv/bin/python --version
```

- **If missing:** "No Python venv found. Should I create one? (`python3 -m venv .venv && source .venv/bin/activate && pip install -e components/mcp-server/`)"
- **If broken (python missing/corrupt):** "Venv exists but Python is broken. Should I recreate it? (`rm -rf .venv && python3 -m venv .venv`)"
- **If working:** report Python version, continue

#### 2. MCP server dependencies
Check if the MCP server module can be imported:
```bash
PYTHONPATH=components/mcp-server/src .venv/bin/python -c "import wip_mcp" 2>&1
```

- **If import fails:** "MCP server dependencies not installed. Should I install them? (`source .venv/bin/activate && pip install -e components/mcp-server/`)"
- **If works:** continue

#### 3. .env file
Check if `.env` exists:
```bash
test -f .env
```

- **If missing:** "No `.env` file found. WIP services haven't been configured yet. Run `./scripts/setup.sh` to set up the infrastructure. For example:\n\n  `./scripts/setup.sh --preset standard --localhost`\n\nSee `docs/development-guide.md` for preset options."
- **If exists:** report key settings (WIP_HOSTNAME, WIP_AUTH_MODE, preset if detectable), continue

#### 4. Container runtime
Check if podman or docker is available:
```bash
command -v podman || command -v docker
```

- **If neither found:** "No container runtime found. Install Podman (`brew install podman` on Mac) or Docker."
- **If found:** report which runtime and version, continue

#### 5. WIP containers running
Check if WIP containers are running:
```bash
podman ps --format "{{.Names}}" 2>/dev/null | grep -c "^wip-" || docker ps --format "{{.Names}}" 2>/dev/null | grep -c "^wip-"
```

- **If no wip- containers:** "WIP containers aren't running. Start them with:\n\n  `./scripts/start.sh`\n\nor if this is a fresh setup:\n\n  `./scripts/setup.sh --preset standard --localhost`"
- **If some running:** list them, note any expected but missing services, continue

#### 6. MCP connectivity
Try calling `get_wip_status` via MCP tools.

- **If MCP tools aren't available:** "MCP server isn't connected. Try restarting Claude Code (`/exit` then `claude`). Check `.mcp.json` exists and points to the right Python."
- **If MCP call fails:** "MCP server started but can't reach WIP services. Are the containers running? Check with `podman ps` or `docker ps`."
- **If works:** report service health, continue

#### 7. Summary
Report status of all checks:

```
Environment Check:

  Python venv:     OK (3.11.9)
  MCP server:      OK (module imports)
  .env config:     OK (localhost, standard preset)
  Container runtime: OK (podman 5.3.1)
  WIP containers:  OK (8 running)
  MCP connectivity: OK (all services healthy)

  All checks passed. You're ready to go!
  Run /wip-status for data state, or /roadmap for priorities.
```

Or with problems:

```
Environment Check:

  Python venv:     OK (3.11.9)
  MCP server:      OK (module imports)
  .env config:     MISSING

  Stopped at: .env configuration

  WIP services haven't been configured yet. Run:
    ./scripts/setup.sh --preset standard --localhost

  Then run /setup again to continue checking.
```

### Key principle

**Stop at the first real problem.** Don't overwhelm the user with 6 failures when fixing the first one would resolve the rest. Each check builds on the previous one — no point checking MCP connectivity if the containers aren't running.

### When to use
- **First time opening the repo** — before anything else
- **After cloning on a new machine**
- **When MCP tools aren't working** — diagnose the problem
- **After running setup.sh** — verify everything is wired up
