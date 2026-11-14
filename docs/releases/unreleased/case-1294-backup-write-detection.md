---
section: fixes
cases: [1294]
---

### A backup's write detection covers its last read, and a failed recount no longer discards the archive

- The recount that detects writes during a backup now runs after the backup's
  last read of MongoDB (the referents table). A create or hard-delete while
  the referents were read used to go unwarned.
- A recount that fails (MongoDB unreachable at the very end) is a job
  warning that the check did not run. It used to fail the job and delete the
  finished archive.
- A `skip_documents` backup records `include_all_versions: false`: the
  archive carries no document versions. It used to claim all of them.
