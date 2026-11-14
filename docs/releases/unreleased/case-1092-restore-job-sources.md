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
- **Restoring from a retained job** (`POST /backup/jobs/{job_id}/restore`)
  reads the source job's archive under the same rule as downloading it. It
  used to ask only admin on the namespaces the source job writes, so an admin
  of a fresh restore's target who also held admin on its source namespace
  could restore its input in merge mode (a dry run is enough) into a new job
  of their own, and download that copy, without being its creator.

**Action:** review redirected merges uploaded before this change. They carry
no `source_namespaces`, so their download and delete keep the target-only
check for as long as the job exists: nothing expires a job
record, and with file storage an archive in the bucket is never swept. List
the restore jobs (`GET /backup/jobs`) and find those whose `options.mode` is
`merge`, whose `options.target_namespace` is set and whose `source_namespaces`
is empty; delete any whose archive the target's admins must not read.
Without file storage their inputs were deleted when the jobs finished, and
there is nothing to do.
