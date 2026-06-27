Capture a lesson learned as a first-class `LESSON` record in the KB, so future agents (and gene-pool review) can find it. Use whenever Peter or a YAC discovers something future agents should know.

**Lessons live in the KB now** — the `LESSON` doc type, written through the served KB client (same surface as cases). The old flat `lessons.md` staging file is retired; do not append to it.

### Usage

`/wip-lesson <text>` — record a lesson with the given text
`/wip-lesson` — infer the lesson from the current conversation context

### Pre-flight (every time)

1. **Tier check** — `test -f .claude/kb.json`. If missing, tell Peter: "This is a tier-2 repo — the KB isn't enabled, so `/wip-lesson` can't write. Enable with the scaffold's `--enable-kb`." Then STOP.
2. **Cache-ensure** — `test -f ~/.cache/wip-kb-client/kb-client.sh`. If missing, install the served client:
   ```bash
   curl -fsSk -H "X-API-Key: $(cat "$(python3 -c 'import json;print(json.load(open(".claude/kb.json"))["kb_api_key_file"])')")" \
     "$(python3 -c 'import json;print(json.load(open(".claude/kb.json"))["kb_app_url"])')/apps/kb/server-api/kb-client/install" | sh
   ```
3. **Session ID** — read `.claude/.session-id` (`cat "$CLAUDE_PROJECT_DIR/.claude/.session-id"`, fall back to `$PWD/.claude/.session-id`); it is the `authored_by` attribution. **Never type it by hand.** If missing, run `/wip-setup` (fresh) or `/wip-wake` (continuation) first.

### Steps

1. **Determine the lesson.** If the user gave an argument, use it. Otherwise infer from the conversation — what was just discovered, corrected, or decided that a future agent needs to know. A lesson is a durable, actionable fact/rule, not a task log.

2. **Compose `lesson.md`** — frontmatter keys are `LESSON` fields; the markdown after the fence is the body. `lesson_number` is **minted by the gateway** — do NOT set it.
   ```markdown
   ---
   title: <short, searchable lesson title>
   authored_by: <your session ID>
   doc_status: published
   tags: [<category>, ...]      # e.g. dependency, api, testing, tooling, workflow, platform
   ---

   <the lesson — concise and actionable: what to do / not do, and why.>
   ```

3. **Write it** through the served client (the gateway mints `lesson_number` + the `LESSON-<n>` synonym):
   ```bash
   kbc kb-write.py LESSON lesson.md
   # -> created LESSON-<n> (<document_id>)
   ```
   Run from the project root (where `.claude/kb.json` lives). `kb-write.py --list` shows all writable types if you need to confirm the surface. Do not guess the endpoint or fall back to retired mechanisms (the old `lessons.md` append, `add-to-kb.py`) — the served `kb-write.py` is the version-matched write surface.

4. **Confirm** — tell Peter the `LESSON-<n>` and a one-line gist.

### When to use this

- After discovering a dependency pin, version conflict, or install-order issue
- After a bug caused by an assumption that turned out wrong
- After Peter corrects an agent's approach in a way that applies broadly
- After a PoNIF encounter worth documenting
- Whenever Peter says "remember this" / "lesson learned"

### What NOT to record

- Things already in CLAUDE.md (check first)
- One-off task details that won't recur
- Personal preferences (use the memory system for those)

### Gene-pool review

`LESSON` records are the findable staging surface for gene-pool review — periodically the important ones get folded into CLAUDE.md, setup heredocs, or slash commands. That review is a human task; lessons don't auto-propagate. They're queryable in the KB (via the served client / app), not a flat file.
