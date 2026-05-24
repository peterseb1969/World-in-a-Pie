---
description: Cross-agent case management — file/list/read/respond/comment/close/implement bugs and requests for other YACs.
---

Cross-agent case workflow. Subcommands: `file`, `list`, `read`, `respond`, `comment`, `close`, `implement`.

**Pre-flight (do this first, every time):**

```bash
test -d yac-discussions && echo "ok" || echo "missing"
```

If `missing`, tell Peter: "Cross-agent cases are not enabled for this project. To enable, symlink `yac-discussions/` to the shared case store." Then stop — do not read the playbook.

**`read` short-circuit (do this BEFORE the playbook load when applicable):**

If `$ARGUMENTS` starts with `read ` followed by a number:

1. Resolve the FR-YAC root via the `yac-discussions` symlink, then run case-fetch.py:
   ```bash
   python3 "$(dirname "$(realpath yac-discussions)")/tools/case-fetch.py" case <N>
   ```
   (REST-canonical retrieval helper, CASE-393. The `realpath` derivation works whether `yac-discussions` was symlinked relative (`../FR-YAC/yac-discussions`) or absolute.)
2. Present the output to the user. STOP.
3. Do NOT load the playbook; the read flow does not need it.

Failure handling (pass through, do not fall back to FS glob or to memory):
- Exit 1 (not found): report "case `<N>` not found" and stop.
- Exit 2 (transport error): report the underlying error verbatim and stop.

**`list` short-circuit (do this BEFORE the playbook load when applicable):**

If `$ARGUMENTS` starts with `list`:

1. Extract any filter args after `list` (e.g., `list --status open` → `--status open`). Supported flags: `--status open,responded,closed,implemented`, `--filed-by <session-id>`, `--limit N` (default 50, cap 100), `--format table|json`.
2. Run case-fetch.py via the same `realpath` derivation as `read`:
   ```bash
   python3 "$(dirname "$(realpath yac-discussions)")/tools/case-fetch.py" list <filter args>
   ```
   (CASE-403; reduced filter set — severity/type/component not available until the kb schema extension lands as a follow-up case.)
3. Present the output as-is. STOP.
4. Do NOT load the playbook; the list flow does not need it.

Failure handling: exit 0 with empty table is normal (zero matches is not a failure); exit 2 is transport error — report verbatim and stop.

**Otherwise** (`file` / `list` / `respond` / `comment` / `close` / `implement`), run the case helper for context:

```bash
cd yac-discussions && echo "Next case number: $(bash case-helper.sh next)" && echo "--- Recent cases ---" && bash case-helper.sh last 5 && echo "--- Open cases ---" && bash case-helper.sh open
```

**Session attribution (write verbs only — `file` / `respond` / `comment` / `close` / `implement`):** read your session ID from `.claude/.session-id` — `cat "$CLAUDE_PROJECT_DIR/.claude/.session-id"` (fall back to `$PWD/.claude/.session-id`). Use that exact value for the attribution the playbook writes — `filed_by:` (on `file`), `responded_by:` (on `respond`), and the session ID stamped into `comment` / `implement` / `close` block headers. **Never type the session ID by hand.** If `.claude/.session-id` is missing, run `/wip-setup` (fresh) or `/wip-wake` (continuation) first — a case record must attribute to a real minted session.

Then you MUST Read `docs/playbooks/case-workflow.md` before taking any action. Do not guess the file format, subcommand handlers, or status transitions from memory — they live in the playbook. Use the case number from the helper when filing new cases. Then execute the requested sub-command from `$ARGUMENTS`.
