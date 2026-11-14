---
section: fixes
cases: [1287]
---

### A platform admin keeps the backup jobs that span a namespace locked for deletion

A job needs its permission on every namespace whose data it holds, and a
namespace locked for deletion is closed to everyone. So a backup spanning one
(an instance-wide backup taken before the deletion) could not be read,
downloaded or deleted by anyone until the deletion ended. A platform admin
now can; nobody else gets more than before. A namespace already deleted was
never closed to a platform admin. A backup's download still needs read on
every namespace the backup covers.

### Listing jobs by namespace finds the jobs that span it

`GET /backup/jobs?namespace=B` matched only a job's anchor, so a backup of
`[A, B]` was not listed under `B`. It now lists every job holding `B`'s data:
anchored there, spanning it, or a merge whose archive came from it.
