---
section: fixes
cases: [1355]
---

### PATCH reports a file refused at write time with its own code, and only a failed byte delete is reported as unreclaimed

- `PATCH /documents` on a document whose file went inactive between
  validation and the write answered `internal_error` and logged a
  traceback. It now answers `validation_failed` with `details.rule:
  file_deleted`, as `POST /documents` does. Any other typed document-store
  or file refusal raised after validation keeps its own code too.
- A document hard delete that releases a file stamps `reclaim_failed`
  (reported by the integrity check as `unreclaimed_file`, "hard-delete it
  again") only when the object store refused to delete the bytes or could
  not be reached for it. A failure before the delete was tried, such as a
  database error, still leaves a warning on the delete, but no longer
  reports untouched bytes as kept by the store.
