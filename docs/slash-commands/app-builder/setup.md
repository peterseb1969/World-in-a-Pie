First-run environment check, guided setup, and **mandatory context loading**. Run this at the start of every APP-YAC session — it enforces CLAUDE.md's "read before you write" discipline.

**Key principle:** stop at the first real problem on environment checks. Don't cascade failures when fixing the first one resolves the rest. But do NOT skip the mandatory reading step on success — loading the baseline context is the point of `/setup`, not just environment verification.

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

On first environment-check failure: stop, show what failed, give the exact next command to run, and tell Peter to re-run `/setup` after fixing. **Do NOT perform Step 6** — the environment isn't ready.

On all checks passing: proceed to Step 6 (mandatory context loading). After Step 6, report each read OK and suggest the next action based on context (`/explore` for a new app, `/implement` if a design model exists, `/bootstrap` for namespace seeding).

### When to use

- **Start of every session** — always, for the mandatory reading step, even when the environment hasn't changed.
- First time opening the repo (new APP-YAC after `create-app-project.sh`).
- After cloning on a new machine.
- When MCP tools aren't working — diagnose the problem.
- After `wip-deploy install` brought WIP up — verify everything is wired up.

### Why the reading step is part of `/setup`

CLAUDE.md asks you to read the baseline documents and the wip-deployable contract at session start. Text in CLAUDE.md is an instruction — it depends on you voluntarily reading and following it. `/setup` is something you actually run — the reading happens as a mechanical output of the command, not as a discretionary re-read. The rule moves from "aspirational instruction" to "enforced tool call."

This is the same pattern as WIP's contract tests: turn a failure mode (agent skips a document it should have read) into a guard (the command's execution includes the read). Peter's framing: *turn the failure mode into the regression guard.*
