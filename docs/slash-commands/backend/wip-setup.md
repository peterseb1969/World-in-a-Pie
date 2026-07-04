First-run session-identity mint, environment check, guided setup, and **mandatory context loading**. Run this at the start of every fresh session — it mints your session ID and is the mechanism that enforces CLAUDE.md §1's mandatory reading. (After a `/clear`, compaction reset, or built-in `/resume`, use `/wip-wake` instead — it continues the prior session's lineage.)

**Key principle:** stop at the first real problem on environment checks. Don't overwhelm the user with cascading failures when fixing the first one would resolve the rest. But do NOT skip the mandatory reading step on success — reading the baseline documents is the point of `/wip-setup` at session start, not just environment verification.

### Step 0 — Session pre-flight (read-only; the mint runs *after* the checks)

`/wip-setup` decides this session's identity here but **does not write it yet** — the mint is deferred until the environment checks pass, so a failed precheck never strands an `active` session that would then block the very re-run the failure message tells you to do. Identity is a **local-first** contract: the sentinel file `.claude/.session-id` is the single source of truth for "who am I"; kb is a derived mirror that catches up later.

The pre-flight is the same state machine as the mint, run read-only — ONE call, not hand-walked file reads (CASE-604):

```bash
python3 .claude/scripts/wake-rollover.py --fresh --dry-run
```

- **exit 0** → identity is mintable (clean fresh start, or discontinuous restart over a closed prior). Ignore the previewed IDs — the real mint below re-computes. Proceed to the checks.
- **exit 5** → an **active** session holds the sentinel; **stop** and relay the script's message (run `/wip-wake` for a linked session, or `/wip-report session-end` first).
- **exit 4** → `.claude/.session-role` is missing; **stop** and tell the operator to re-run the scaffold with `--refresh`. Do **not** guess the role.

Step 0 writes nothing (`--dry-run` is a pure read). If it exited 0, proceed to the checks; the session is **minted only after they pass** (below).

### Checks (in order)

1. **Python venv** — `.venv/bin/python --version`. If missing or broken, offer to create/recreate.
2. **MCP server deps** — `PYTHONPATH=components/mcp-server/src .venv/bin/python -c "import wip_mcp"`. If import fails, offer `pip install -e components/mcp-server/`.
3. **Attached install** — a repo-root `.env` is NOT a thing (retired setup.sh-era artifact; never check for it, never generate it — everything lives in `~/.wip-deploy/<name>/`). Instead, enumerate real installs: `ls -d ~/.wip-deploy/*/deployment.deployer-state 2>/dev/null`. **Zero** → this machine has no WIP install; surface the one operator question — provision one? (`wip-deploy install --preset standard --target dev`, see `docs/deploy/`) — and STOP. **One** → this session is attached to it; report its name. **Several** → prefer the one whose containers are actually running: read the compose project label off a live wip-* container (`podman inspect wip-registry --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'` → the owning `~/.wip-deploy/<name>/`). Exactly one running install → attach to it and surface the pick ("attached to <name> — say the word if you meant a different install"). None running, or more than one running → ask the operator which install this session works against (parallel YACs on one machine are normal), then report the pick. Subsequent checks (containers, MCP) are read against that install.
4. **Container runtime** — `command -v podman || command -v docker`. If neither, suggest `brew install podman` (Mac) or Docker.
5. **WIP containers running** — `podman ps` (or `docker ps`) filtered to `wip-` prefix. If none, point at `wip-deploy install` (fresh) or `wip-deploy restart` (existing install). If some, list and flag any expected-but-missing services.
6. **MCP connectivity** — call `get_wip_status` via MCP tools. If MCP tools aren't available, suggest restarting Claude Code and checking `.mcp.json`. If the call fails, suggest checking containers.

### Step 6 — Mint the session (only after all checks pass)

The environment is verified, so now write identity. A failed check above left **no** session behind — deferring the mint to here is the fix for the strand-on-failed-precheck bug: the "fix it and re-run `/wip-setup`" instruction works as written. The mint is a deterministic state machine, so it runs as a script, not hand-walked (CASE-604):

```bash
python3 .claude/scripts/wake-rollover.py --fresh
```

It re-enforces Step 0's decision at write time (sentinel absent → clean fresh start, no `continues_from`; prior `status: closed` → discontinuous restart, sentinel overwritten, no `continues_from`; prior still active → refuses and points at `/wip-wake`), mints `<ROLE>-<YYYYMMDD-HHMMSS>` (seconds precision — eliminates the same-minute collision class), creates `reports/<ID>/session.md` with `status: active` frontmatter, atomically swaps the sentinel, and mirrors the session to kb (tier-gated: skipped silently without `.claude/kb.json`; unreachable kb warns and continues — local state is authoritative). Stdout contract: `PRIOR_ID=-` (or the closed prior on a discontinuous restart) and `NEW_ID=<ID>`.

If the mirror warns because the served KB client is missing (`~/.cache/wip-kb-client/kb-client.sh`), install it via the one-liner in the `/wip-case` pre-flight, then retry manually: `kbc kb-write.py SESSION reports/<ID>/session.md`. Add a short body stub (task list, phase) to `session.md` as work begins.

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
