---
section: action-required
cases: [1287]
packages: [wip-archive]
---

### Backups and restores say which files they carry without their bytes, once per reason

- **A backup whose object store cannot be reached fails.** It used to
  complete, naming every file as unreadable, as an archive of file records
  without their files. A file the store cannot serve (missing, refused) or
  whose bytes do not match its checksum is still left out of an archive that
  completes, as before.
- **A failed write to the backup's scratch space** (a full disk) stops the
  backup and says so. It used to be reported, file by file, as the object
  store failing to read.
- **One warning per reason, not one per file.** The job names how many files
  a backup left without bytes, or a restore wrote without them, and the
  first 20. A namespace-wide problem used to write one warning per file into
  the job record.
- **The archive records the files it carries without bytes.** Each
  namespace's manifest entry lists them in `files_without_bytes` (absent when
  there are none, and in archives made before this change).
- **A restore names every file it writes without content**, including from
  a backup made without files (the default), where it used to say nothing.
  A fresh restore from such an archive writes each file under a new id with
  nothing there; a plain restore or merge does not name a file whose object
  this install still holds under its id.
- **A restore's dry run checks the bytes** as the real run does: "would
  upload N file blob(s)" counts only bytes that match their checksum, and
  the files it would restore without content are named.
- A restore reads and hashes each blob on a worker thread, so a large file
  no longer stalls the service's other requests and health checks.

**Action:** a script that reads a backup or restore job's per-file warnings
reads the per-reason ones instead.
