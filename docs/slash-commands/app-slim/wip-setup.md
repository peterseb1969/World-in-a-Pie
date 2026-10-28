Start a fresh session: check the environment, mint the session ID, read the rules. To continue a previous session after a `/clear`, use `/wip-wake` instead.

Stop at the first failed check, say exactly what failed and the command that fixes it, and tell Peter to re-run `/wip-setup` afterwards. A failed check leaves no session behind.

### 1. Pre-flight (writes nothing)

```bash
python3 .claude/scripts/wake-rollover.py --fresh --dry-run
```

- exit 0 → continue;
- exit 5 → a session is still active: stop, and suggest `/wip-wake` (continue it) or `/wip-report session-end` (close it first);
- exit 4 → `.claude/.session-role` is missing: Peter re-runs `scripts/create-app-project.sh <this app> --prefix APP-<X>` from the WIP clone.

### 2. Checks, in order

1. `node --version` — 20 or newer.
2. `node_modules/` exists and is not empty — else `npm ci` (or `npm install` without a lockfile).
3. `.env` sets `WIP_API_KEY_FILE`, and that file is not empty:
   `KF="$(grep ^WIP_API_KEY_FILE .env | cut -d= -f2)"; test -s "$KF" && echo ok` — never print the key.
4. WIP answers: `curl -sk -m 3 -o /dev/null -w '%{http_code}\n' https://localhost:8443/api/registry/namespaces -H "X-API-Key: $(cat "$KF")"` prints `200` (use the install host this app targets).
5. MCP: call `get_wip_status`. If the tools are missing, Peter restarts Claude Code and checks `.mcp.json`.

### 3. Mint the session — only after every check passed

```bash
python3 .claude/scripts/wake-rollover.py --fresh
```

It prints `PRIOR_ID=…` and `NEW_ID=…`. Then read the brief:

```bash
python3 .claude/scripts/wake-brief.py --prior <PRIOR_ID>    # omit --prior when PRIOR_ID is -
```

### 4. Read the rules

`Read` `docs/cards/core.md`, and the `Phase card:` the brief names, if any.

### 5. Report

Each check's result in one line, the new session ID, the phase, and the next step: `/wip-explore` for a new app, otherwise the command for the recorded phase.
