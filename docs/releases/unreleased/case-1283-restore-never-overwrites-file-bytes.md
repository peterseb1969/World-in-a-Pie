---
section: fixes
cases: [1283]
---

### A restore never writes file bytes over another file's bytes (security)

File bytes are stored under their file id, and every restore mode (plain,
merge and fresh) uploaded each archived blob with an unconditional write. An
archive naming the id of a file in another namespace, with bytes matching
its own recorded checksum, therefore replaced that file's content; a caller
needed admin only on the namespaces the archive carries. A plain restore
also uploaded the bytes before its namespace step, so a restore the
Registry then refused had already written them.

Now every restore asks the object store, before it uploads anything,
whether each key it would write is free (a HEAD request, not a conditional
PUT). A free key is written. A key no file record uses that already holds
the same bytes, as after a failed run of the same restore, is left as it is.
A key holding other bytes, or one a file record in any namespace uses (two
records on one object would each delete the other's bytes), refuses the
restore, naming the keys, with nothing uploaded. A plain restore checks
the keys before it creates its namespaces and before it drops a stale
reporting schema (`drop_stale_reporting`), so such a refusal leaves both as
they were; it then creates or updates all its target namespaces before it
uploads any bytes, and still uploads the bytes before the records that need
them. A fresh restore checks after it has created its target namespaces: its
file ids are newly minted, so a refusal there means the store answered for a
key it should not hold.

**Action:** restoring an archive whose file ids are held by other files on
the install now fails; restore such an archive with `mode: fresh`, which
gives every file a new id.
