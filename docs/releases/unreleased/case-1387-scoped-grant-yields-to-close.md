---
section: fixes
cases: [1387]
---

### A namespace closed for its deletion is no longer re-granted to a scoped read

The deletion's close step revokes every role's `USAGE` on a namespace's schema,
and marks the schema closed so nothing grants it again. The grant step of a
scoped reporting read honoured that mark, but serialised against the close on a
different lock: a close that committed in the few statements between the grant
step's closed check and its `GRANT`s had its revoked `USAGE` put back, and the
namespace's rows were readable again until the deletion's drop step. The grant
step now takes the same per-schema advisory lock the close and the drop take,
before it checks the mark, so the two exclude each other: either the close
lands first and the grant step leaves the schema out, or the grants land first
and the close revokes them. A scoped read naming a namespace whose deletion has
begun can wait for the close in progress. The reader grants reporting-sync renews at startup take the same lock
per schema, so a replica starting during a rolling update cannot reopen a
schema another replica is closing.

The startup grants wait at most 10 seconds for one schema's lock, each schema in
a transaction of its own. A namespace drop holds that lock while it counts every
table's rows, so an unbounded wait held up reporting-sync's whole PostgreSQL
bring-up behind one deletion: no pool, every endpoint 503, and a rolling update
that could time out. A schema whose lock stays held is now skipped and named in a
warning; the reader keeps the grants it already had there, and the next start
grants them again.
