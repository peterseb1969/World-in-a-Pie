---
section: fixes
cases: [1359, 1360]
---

### `/wip-case` says what to do after a claim's transport failure, and names every list flag and `reopen`

- When `CLAIM acquire` fails with a transport error, the command reads the
  case's claims first. A claim held by the agent's own session means the
  claim was taken: it goes on. A claim held by another session means the
  case is theirs: it stops. No claim means none was taken: it acquires
  again. It used to say both "stop" and "read before you retry", and an
  agent could report its own claim as someone else's.
- The `list` section points to the served playbook as the source of
  truth, and names `--unanswered`, `--claimed` / `--unclaimed` and
  `--questions`. `reopen` is listed among the subcommands and write verbs.
- The quality audit says "synced copies not checked" on a machine without
  the KB client cache, instead of "0 stale", and warns when a synced
  playbook copy is stale. The repo's playbook copy is re-synced.

App repos pick up the new command text on their next scaffold refresh.
