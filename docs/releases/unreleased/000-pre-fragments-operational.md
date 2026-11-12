---
section: operational
---

- **Identity-hash migration after restoring an older archive.** Identity
  fields that are term or reference fields now hash the resolved IDs, not the
  submitted spelling. An archive written before that rule restores the old
  hashes; run `POST /api/document-store/maintenance/rehash-identities`
  (dry run first) on each restored namespace. Then rebuild the affected
  reporting tables with `POST /api/reporting-sync/sync/batch/{template}?namespace=…&force=true`
  — a batch sync without `force` keeps existing rows and does not refresh
  their `identity_hash`. On large namespaces the pass can answer 500 from a
  client timeout while the Registry still completes; check a sample of
  Registry entries against document-store before re-running.
