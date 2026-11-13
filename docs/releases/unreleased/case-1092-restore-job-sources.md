---
section: action-required
cases: [1092]
---

### A restore job's retained archive is guarded by the namespaces it holds, not only the ones it writes

A restore job keeps its input archive, which holds the archive's own
namespaces' data. The job's permission check covered only the namespaces the
restore writes, so where those differ from the archive's, an admin of the
target alone could download and delete another namespace's full export.

- **A merge redirected into another namespace** (`target_namespace`) now
  records the archive's namespaces in `source_namespaces`. Seeing, following,
  downloading or deleting the job needs the job's permission on them as well
  as on the target (creating the merge already required admin on them).
- **A fresh restore's input**, whose source namespaces are often from another
  install and have no grants here, can be downloaded and deleted only by the
  key that created the job or a platform admin, on top of admin on its
  targets. Admins of the target can still see the job.

**Action:** none for new jobs. Redirected merges uploaded before this change
carry no `source_namespaces`; their download and delete keep the
target-only check until the job's archive is swept.
