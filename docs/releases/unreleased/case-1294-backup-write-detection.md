---
section: fixes
cases: [1294]
---

### A backup's write detection covers its last read, and a failed recount no longer discards the archive

- The recount that detects writes during a backup now runs after the backup's
  last read of MongoDB: the referents table, then each namespace's config and
  provenance for the manifest. A create or hard-delete during those reads
  used to go unwarned. If MongoDB cannot be reached for the manifest's reads,
  the archive cannot be finished and the job fails, as before.
- A recount that fails (MongoDB unreachable at the very end) is a job
  warning that the check did not run. It used to fail the job and delete the
  finished archive.
- A `skip_documents` backup records `include_all_versions: false`: the
  archive carries no document versions. It used to claim all of them.
