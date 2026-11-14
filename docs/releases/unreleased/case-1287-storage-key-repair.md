---
section: first-start
cases: [1287]
---

### File rows a fresh restore left pointing at another file's object get their own copy

The startup repair of file rows whose `storage_key` is not their `file_id`
left alone the rows with no object under their own id: the ones a fresh
restore wrote from an archive without bytes. They kept serving the source
file's object, and deleting one deleted the source's bytes. At the first
start after the upgrade, the object such a row names is copied under the
row's own id and the row is re-pointed there, so it keeps its content and
shares nothing. A row whose old key names no object is re-pointed as it is.
One row the object store fails on no longer stops the rest: it is named in
the log and retried at the next start. The repair now runs after the bucket
is ensured. The start-up line counts the rows repaired, copied and left.
