---
description: Review open DOC-YAC documentation audit cases — verify accuracy flags against the codebase, propose markdown patches.
---

Doc-review workflow. Subcommands: `/doc-review` (full queue), `/doc-review <number>` (single case).

**Pre-flight (do this first, every time):**

```bash
test -d yac-discussions && echo "ok" || echo "missing"
```

If `missing`, tell Peter: "Cross-agent cases are not enabled for this project. To enable, symlink `yac-discussions/` to the shared case store." Then stop — do not read the playbook.

If `ok`, you MUST Read `docs/playbooks/backend/doc-review-workflow.md` before taking any action. Do not guess the response format, questionnaire structure, re-review rules, **or the deletion-sweep pre-flight**, from memory — they live in the playbook. Then execute the requested action from `$ARGUMENTS`.

When the queue contains **three or more `type: doc-deletion` cases**, the playbook's §3 (Deletion-sweep pre-flight) is mandatory before any destructive op. Skip it for smaller queues; the per-case §A.2 grep suffices at that scale. CASE-281 names the failure mode it prevents (cross-cutting forced-read dependencies missed when deletions are processed in queue order).
