---
description: Review open DOC-YAC documentation audit cases — verify accuracy flags against the codebase, propose markdown patches.
---

Doc-review workflow. Subcommands: `/wip-doc-review` (full queue), `/wip-doc-review <number>` (single case).

**Pre-flight (do this first, every time):**

```bash
test -f .claude/kb.json && echo "ok" || echo "missing"
```

If `missing`, tell Peter: "This is a tier-2 repo — cross-agent cases are not enabled. Enable with the scaffold's `--enable-kb`." Then stop — do not read the playbook. (This is the same tier signal `/wip-case` checks; don't gate on a `yac-discussions/` directory.)

If `ok`, you MUST Read `docs/playbooks/backend/doc-review-workflow.md` before taking any action. Do not guess the response format, questionnaire structure, re-review rules, **or the deletion-sweep pre-flight**, from memory — they live in the playbook. Then execute the requested action from `$ARGUMENTS`.

When the queue contains **three or more `type: doc-deletion` cases**, the playbook's §3 (Deletion-sweep pre-flight) is mandatory before any destructive op. Skip it for smaller queues; the per-case §A.2 grep suffices at that scale. It prevents cross-cutting forced-read dependencies being missed when deletions are processed in queue order.
