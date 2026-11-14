---
section: action-required
cases: [1283]
---

### One restore or merge runs at a time

Every restore checks, before it uploads, that each file key it would write
is free, and then writes it. Two restores running at once could both find a
key free and both write it, the later one replacing the earlier one's
bytes. Now one restore or merge, of any mode (`restore`, `merge`, `fresh`),
runs at a time on an instance: `POST
/api/document-store/backup/namespaces/{namespace}/restore` and `POST
/api/document-store/backup/jobs/{job_id}/restore` answer 409, naming the
running job, while another one is running, and keep nothing of the refused
request. The claim is taken atomically when the job record is created and
released when the job finishes or fails; a restore a crashed process left
running is failed at the next start, which releases it. Dry runs, backups
and validations are not held back.

**Action:** a script that starts several restores at once starts them one
after another, waiting for each job to finish
(`GET /api/document-store/backup/jobs/{job_id}`).

### A restore checks its file keys before it writes anything, dry runs too

A plain restore created its target namespaces before it checked the file
keys, so a refused key left them created by a restore that wrote nothing
else. The keys are now checked first. An object store that cannot answer
for a key (unreachable, or refusing the request) refuses the restore naming
the key, instead of failing it with a raw storage error. A dry run, of a
plain restore or a merge, now checks the keys as the real run does, so it
refuses what the real run would refuse. The blob phase's messages count the
blobs it uploads, and say how many keys already holding the same bytes it
leaves as they are.
