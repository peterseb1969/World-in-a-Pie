---
section: action-required
cases: [1314]
packages: [wip-client, wip-auth, wip-deploy]
---

### A key in wip-admins or wip-services is never scoped

A runtime key could be created in `wip-admins` or `wip-services` and still carry a
`namespaces` list. Services did not agree on what that key could reach. The stores
scoped it, through the Registry's per-namespace permission. The Registry's entry
and search doors treated it as unrestricted. Its privileged-only doors (identity
allocation, the stores' service paths) never asked about a namespace at all.

A privileged key now never carries a `namespaces` list, not even an empty one:

- `POST /api/registry/api-keys` refuses the combination with 422. So do
  `PATCH /api-keys/{name}` (a change of `groups` or `namespaces` that would
  leave both) and `PUT /api-keys/{name}/namespaces/{prefix}`.
- The Registry refuses an install key file that holds such a key and imports
  none of it (`key_file_import: "refused"` in `/health`; the log says which key).
  `wip-deploy` refuses `wip-admins` / `wip-services` on a spec key first.
- A system restore skips such a key with reason `scoped_privileged_key`; a key
  of its name already on the stack keeps its record.

A system restore also no longer writes the grants of a key it skips. A grant
follows its key's name, so before this change it went to whichever key held
that name on the target: another key of the name (an upsert that skipped it
as `differs`), or the next key created under it. Such a grant is now skipped
as `key_not_restored`. The exception is a key wip-deploy manages on both
sides: it keeps the grants the API gave it.

**Action:** a key stored with both before this change keeps working as before
and is never rewritten. The Registry names it at startup (a critical log line)
and lists it under `scoped_privileged_keys` in `/health`. Fix it by removing the
group (`PATCH groups`). To keep the group, revoke the key and create it again
without the list. A PATCH that leaves `groups` and `namespaces` untouched
(disable, description, expiry) still works on such a key.

`@wip/client`: `SystemRestoreSkipReason` gains `scoped_privileged_key` and
`key_not_restored`.
