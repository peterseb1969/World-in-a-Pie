---
section: fixes
cases: [1288]
---

### Migrating documents with no namespace picks from what the key can read

`POST /api/document-store/documents/migrate` without `namespace` took the
only namespace the key could *write*, so a key that reads two namespaces and
writes one migrated in a namespace the caller never named. It now picks as
every other door does, from the namespaces the key can read, and then
requires write there: such a key gets 422 and names the namespace. A
single-namespace key is unaffected, as is the earlier note's "keys with
write are unaffected", which holds again.
