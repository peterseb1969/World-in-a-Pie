---
section: action-required
cases: [1147]
packages: [wip-client]
---

### System backups are version 2; a version 1 archive's key scopes are restored as read grants

A runtime key's `namespaces` list stopped giving read on its own (it is a
scope; read comes from a grant). Keys that existed then were given the read
grants they relied on, once, at the Registry's start. A system restore of an
archive made before that change brought those keys back without them, so
they lost every read their list had given, and no restart healed it.

System backups now write `version: 2`. Restoring a `version: 1` archive
writes a read grant for each key and listed namespace the archive holds no
grant for, lists them in the report's new `scope_read_grants`, and warns.
They are restored like any archived grant: orphaned where a namespace of
that name here has another id, and not written at all where the archive
records no id and no namespace of that name exists here
(`scope_namespace_absent`). A `version: 2` archive gets none: there a listed
namespace without a grant is access the source ended.

**Action:** review `scope_read_grants` in a version 1 restore's dry run. It
also brings back a read the source revoked between the change and the
backup. A Registry from before this release refuses a version 2 archive;
restore it on a Registry at least this new. `@wip/client`'s `SystemArchive`
type accepts both versions.
