---
section: fixes
cases: [1287]
---

### A restore checks the files it has no bytes for without stalling or stopping; a store that refuses every file fails the backup

- **A restore's check for objects still held under a file's id is bounded.**
  For an archive made without files it asks the object store about every
  file row: it now asks 16 at a time, reports its progress per 500 files,
  and a store error on one object (anything but "not there") names that file
  as unchecked instead of failing the restore or its dry run.
- **A backup with files fails when the object store refuses every file**
  (store errors, not one file read). It used to complete as an archive that
  recorded every file as without bytes. Files whose objects are merely gone
  still complete the backup, named on the job as before.
- **The startup storage-key repair copies an aliased object only when its
  bytes match the file's checksum.** A row whose alias holds other bytes is
  re-pointed at its own id without them, and logged.
- A restore reads and hashes the archive's bytes for a key it is about to
  write on a worker thread, as every other pass over them does.
- **The startup storage-key repair stops scanning once it is done.** Its
  query can use no index, so it read the whole `files` collection at every
  start. A pass that leaves nothing to repair now records a marker in
  `document_store_migrations` and later starts skip it; a pass that leaves
  rows unrepaired records none and the next start retries.
