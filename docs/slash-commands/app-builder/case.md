---
description: Cross-agent case management — file/list/read/respond/comment/close/implement bugs and requests for other YACs.
---

Cross-agent case workflow. Subcommands: `file`, `list`, `read`, `respond`, `comment`, `close`, `implement`.

**Pre-flight (do this first, every time):**

```bash
test -d yac-discussions && echo "ok" || echo "missing"
```

If `missing`, tell Peter: "Cross-agent cases are not enabled for this project. To enable, symlink `yac-discussions/` to the shared case store." Then stop — do not read the playbook.

If `ok`, run the case helper for context:

```bash
cd yac-discussions && echo "Next case number: $(bash case-helper.sh next)" && echo "--- Recent cases ---" && bash case-helper.sh last 5 && echo "--- Open cases ---" && bash case-helper.sh open
```

Then you MUST Read `docs/playbooks/app-builder/case-workflow.md` before taking any action. Do not guess the file format, subcommand handlers, or status transitions from memory — they live in the playbook. Use the case number from the helper when filing new cases. Then execute the requested sub-command from `$ARGUMENTS`.
