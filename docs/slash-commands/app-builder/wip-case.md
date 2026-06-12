---
description: Cross-agent case management — file/list/read/respond/comment/close/implement bugs and requests for other YACs.
---

Cross-agent case workflow (tier 3). Subcommands: `file`, `list`, `read`, `respond`, `comment`, `close`, `implement`.

This stub is deliberately thin (CASE-463 phase B): it carries only the tier
check, the cache-ensure, and the cheap read paths. Every verb procedure,
gateway endpoint, and format rule lives in the SERVED playbook
(`~/.cache/wip-kb-client/case-workflow.md`), which version-matches the
backend by construction — do not reproduce its content here.

**Pre-flight (do this first, every time):**

1. **Tier check** — `test -f .claude/kb.json`. If missing, tell Peter: "This is a tier-2 repo — cross-agent cases are not enabled. Enable with the scaffold's `--enable-kb` (CASE-463)." Then STOP.
2. **Cache-ensure** — `test -f ~/.cache/wip-kb-client/kb-client.sh`. If missing, install the served client using the two facts in `.claude/kb.json`:
   ```bash
   curl -fsSk -H "X-API-Key: $(cat "$(python3 -c 'import json;print(json.load(open(".claude/kb.json"))["kb_api_key_file"])')")" \
     "$(python3 -c 'import json;print(json.load(open(".claude/kb.json"))["kb_app_url"])')/apps/kb/server-api/kb-client/install" | sh
   ```

**`read` short-circuit (no playbook load):**

If `$ARGUMENTS` starts with `read ` followed by a number (optionally with `--brief`):

1. ```bash
   bash ~/.cache/wip-kb-client/kb-client.sh case-fetch.py case <N>
   ```
2. Present the fetched case to the user.
3. **If `--brief` was passed:** STOP here (raw read only). Otherwise continue to the assessment (the default).
4. **Assess the case** and append the structured block below. Actually look — read the files/code the case cites, and check whether sibling `related:` cases are still open — before writing each line. Do NOT assess from the case prose alone.

   ```
   ## CASE-<N> — assessment
   - Relevance: live | stale (cites X that no longer exists) | superseded by CASE-Y | overtaken by code (detail)
   - Accuracy: verified against <files/commands you actually read or ran> | UNCHECKED: <load-bearing claims you did NOT verify> → deeper verify before implementing? [yes/no]
   - Effort: S | M | L — touches <surfaces you inspected>; depends on <prereq cases / blockers>
   - Recommendation: <one line — act now / needs verification / stale-consider-closing / blocked on X>
   ```

   Assessment rules (the point of the feature — follow them or the block is worse than useless):
   - **Accuracy is two-part and honest.** State only what you actually verified, and against what (`path:line`, a command you ran, a doc you read). List every load-bearing claim you did NOT check under `UNCHECKED:`. Read-time checks are shallow — if a load-bearing claim is unverified, set "deeper verify before implementing? yes". Never write "verified" for something inferred from the case text.
   - **Cross-repo honesty.** If the case targets code not in your repo, say so under `UNCHECKED:` ("targets code not present in this clone") — do not guess its accuracy.
   - **Relevance is cheap — actually check it.** Grep that cited files/paths/APIs still exist; check whether `related:` cases are closed/superseded; note if the code already changed in a way that overtakes the case.
   - **Effort is sized from what you inspected**, not the title. Name the surfaces and any prereq cases/blockers.

Failure handling (pass through, do not fall back to FS glob or to memory): exit 1 (not found) → report "case `<N>` not found" and stop; exit 2 (transport error) → report the underlying error verbatim and stop.

**`list` short-circuit (no playbook load):**

If `$ARGUMENTS` starts with `list`:

1. Extract any filter args after `list` (e.g., `list --status open` → `--status open`). Supported flags: `--status open,responded,closed,implemented`, `--filed-by <session-id>`, `--limit N` (default 50, cap 100), `--format table|json`.
2. ```bash
   bash ~/.cache/wip-kb-client/kb-client.sh case-fetch.py list <filter args>
   ```
3. Present the output as-is. STOP. Exit 0 with an empty table is normal (zero matches); exit 2 is transport error — report verbatim and stop.

**All write verbs** (`file` / `respond` / `comment` / `close` / `implement`):

1. Read your session ID from `.claude/.session-id` (`cat "$CLAUDE_PROJECT_DIR/.claude/.session-id"`, fall back to `$PWD/.claude/.session-id`) — it is the `author`/`filed_by` attribution on every gateway call. **Never type a session ID by hand.** If the file is missing, run `/wip-setup` (fresh) or `/wip-wake` (continuation) first.
2. You MUST Read `~/.cache/wip-kb-client/case-workflow.md` — the served playbook — and execute the verb's gateway flow from there. Do not guess endpoints, payloads, or status transitions from memory, and do not fall back to retired clients (`add-to-kb.py`, `case_allocate.py`, FS claim helpers); the gateway verbs replaced them (CASE-464).
