---
description: Read firesides (design-chat transcripts) from kb — list and read. Tier 3, read-only.
---

Read-only access to firesides (`FIRESIDE` — design-chat / fireside transcripts). Subcommands: `list`, `read <document_id>`.

Firesides are **written** by `/wip-report` (bare, Mode 1), which writes them to kb via `kb-write.py FIRESIDE`. This command only **reads** them — there is no file/edit/respond verb. The runner reads through the kb gateway (`GET /firesides[/:id]`), never the backend store directly.

**Pre-flight (do this first, every time):**

1. **Tier check** — `test -f .claude/kb.json`. If missing, tell the user: "This is a tier-2 repo — kb firesides are not enabled. Enable with the scaffold's `--enable-kb`." Then STOP.
2. **Cache-ensure** — `test -f ~/.cache/wip-kb-client/kb-client.sh`. If missing, install the served client using the two facts in `.claude/kb.json`:
   ```bash
   curl -fsSk -H "X-API-Key: $(cat "$(python3 -c 'import json;print(json.load(open(".claude/kb.json"))["kb_api_key_file"])')")" \
     "$(python3 -c 'import json;print(json.load(open(".claude/kb.json"))["kb_app_url"])')/apps/kb/server-api/kb-client/install" | sh
   ```

**`list` short-circuit:**

If `$ARGUMENTS` starts with `list`:

1. Extract any filter args after `list`. Supported flags: `--topic <exact topic>`, `--author <exact author>`, `--limit N` (default 50, cap 100), `--format table|json`. (Topic/author are exact-match; for fuzzy discovery use the platform `search` — title and topic are FTS weight-A.)
2. ```bash
   kbc case-fetch.py fireside list <filter args>
   ```
3. Present the output as-is. The table gives `title | topic | authored_by | chat_date | document_id` — the `document_id` is what `read` takes. STOP. An empty table is normal (zero matches); exit 2 is a transport error — report it verbatim and stop.

**`read` short-circuit:**

If `$ARGUMENTS` starts with `read ` followed by a fireside `document_id` (discover ids via `list`):

1. ```bash
   kbc case-fetch.py fireside <document_id>
   ```
2. Present the fireside body to the user. A fireside is a design transcript, not an actionable case — present it as-is; no assessment block.
3. If the runner prints `fireside <id> not found in kb`, relay that and stop. Exit 2 (transport error) → report it verbatim and stop.
