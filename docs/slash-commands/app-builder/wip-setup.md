First-run session-identity mint, environment check, guided setup, and **mandatory context loading**. Run this at the start of every fresh APP-YAC session — it mints your session ID and enforces CLAUDE.md's "read before you write" discipline. (After a `/clear`, compaction reset, or built-in `/resume`, use `/wip-wake` instead — it continues the prior session's lineage.)

**Key principle:** stop at the first real problem on environment checks. Don't cascade failures when fixing the first one resolves the rest. But do NOT skip the mandatory reading step on success — loading the baseline context is the point of `/wip-setup`, not just environment verification.

### Step 0 — Session pre-flight (read-only; the mint runs *after* the checks)

`/wip-setup` decides this session's identity here but **does not write it yet** — the mint is deferred until the environment checks pass, so a failed precheck never strands an `active` session that would then block the very re-run the failure message tells you to do. Identity is a **local-first** contract: the sentinel file `.claude/.session-id` is the single source of truth for "who am I"; kb is a derived mirror that catches up later.

The pre-flight is the same state machine as the mint, run read-only — ONE call, not hand-walked file reads:

```bash
python3 .claude/scripts/wake-rollover.py --fresh --dry-run
```

- **exit 0** → identity is mintable (clean fresh start, or discontinuous restart over a closed prior). Ignore the previewed IDs — the real mint below re-computes. Proceed to the checks.
- **exit 5** → an **active** session holds the sentinel; **stop** and relay the script's message (run `/wip-wake` for a linked session, or `/wip-report session-end` first).
- **exit 4** → `.claude/.session-role` is missing (gitignored — fresh checkouts never have it); **stop** and tell the operator to re-run `scripts/create-app-project.sh <app-dir> --prefix APP-<X>` from the WIP clone (repos whose committed `.app-meta` records `ROLE_PREFIX` self-heal without `--prefix`). There is NO `--refresh` flag — the scaffolds auto-detect mode. Do **not** guess the role.

Step 0 writes nothing (`--dry-run` is a pure read). If it exited 0, proceed to the checks; the session is **minted only after they pass** (below).

### Checks (in order)

1. **Node version** — `node --version`. Expect 20.x+ (matches the canonical `node:20-alpine` Dockerfile.dev base). Older versions may work but are off-contract.
2. **Package manager + deps** — `command -v npm` then check `node_modules/` exists and is non-empty. If missing, suggest `npm ci` (or `npm install` if no `package-lock.json` yet).
3. **`.env` file** — `test -f .env`. If missing, point at this app's CLAUDE.md "API Key" section. The runtime key SOURCE is the **live wip-deploy secrets file**, referenced as `WIP_API_KEY_FILE` — not a baked `WIP_API_KEY`. Confirm `WIP_API_KEY_FILE` is set and the file it points at exists and is non-empty: `KF="$(grep ^WIP_API_KEY_FILE .env | cut -d= -f2)"; test -s "$KF"` (don't print the contents). A literal `WIP_API_KEY` is a legacy/local fallback — accept it if present, but prefer the file.
4. **WIP reachable** — resolve the key from the live file, not a baked value: `KEY="$(cat "$(grep ^WIP_API_KEY_FILE .env | cut -d= -f2)" 2>/dev/null || grep ^WIP_API_KEY .env | cut -d= -f2)"; curl -sk -m 3 https://localhost:8443/api/registry/namespaces -H "X-API-Key: $KEY"` (or the install host this app targets). If unreachable, point at `wip-deploy install` or `wip-deploy restart`.
5. **MCP connectivity** — call `get_wip_status` via MCP tools. If MCP tools aren't available, suggest restarting Claude Code and checking `.mcp.json`. If the call fails, suggest checking containers / network.

### Step 5 — Mint the session (only after all checks pass)

The environment is verified, so now write identity. A failed check above left **no** session behind — deferring the mint to here is the fix for the strand-on-failed-precheck bug: the "fix it and re-run `/wip-setup`" instruction works as written. The mint is a deterministic state machine, so it runs as a script, not hand-walked:

```bash
python3 .claude/scripts/wake-rollover.py --fresh
```

It re-enforces Step 0's decision at write time (sentinel absent → clean fresh start, no `continues_from`; prior `status: closed` → discontinuous restart, sentinel overwritten, no `continues_from`; prior still active → refuses and points at `/wip-wake`), mints `<ROLE>-<YYYYMMDD-HHMMSS>` (seconds precision — eliminates the same-minute collision class), creates `reports/<ID>/session.md` with `status: active` frontmatter, atomically swaps the sentinel, and mirrors the session to kb (tier-gated: skipped silently without `.claude/kb.json`; unreachable kb warns and continues — local state is authoritative). Stdout contract: `PRIOR_ID=-` (or the closed prior on a discontinuous restart) and `NEW_ID=<ID>`.

If the mirror warns because the served KB client is missing (`~/.cache/wip-kb-client/kb-client.sh`), install it via the one-liner in the `/wip-case` pre-flight, then retry manually: `kbc kb-write.py SESSION reports/<ID>/session.md`. Add a short body stub (task list, phase) to `session.md` as work begins.

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
