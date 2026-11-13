---
section: action-required
cases: [1303]
---

### An app's namespace can be restored without removing the app first

A namespace an install reserves for one of its apps (wip-deploy's reservations
file) used to pin a namespace id even while no namespace of that name existed,
and deleting the namespace minted a new one. A platform admin's ID-preserving
restore of that namespace, from another install or after deleting it here, then
carried the archive's id onto a name "reserved here for another namespace" and
was refused with 409. The only way out was `wip-deploy remove-app`, the
restore, and `add-app`, which also removes the app's key in between.

Now the install's reservation holds the namespace's id while the namespace
exists, and none otherwise. The namespace created under it gives it its id: a
restore keeps the archive's, and the app's own bootstrap gets a new one.
Reservations made through the API by a runtime key (`reserve`) keep their ids
and the 409.

**Behaviour changes:**
- `GET /api/registry/reservations` and the stored records show no namespace id
  for an install reservation whose namespace does not exist. The next key-file
  import (within seconds of the upgrade) clears the ids such reservations were
  given before.
- A system backup archives such a reservation with `namespace_id: null`
  (`ArchiveReservation.namespace_id` may be null only for a reservation
  managed by wip-deploy). A system restore writes it as a runtime reservation
  with a new id.
- A grant the system backup carries for a deleted, install-reserved namespace
  is archived without an id, and a system restore writes it effective for
  whichever namespace takes the name. Before, it carried the reservation's id.

**Action:** none. To restore an app's namespace: delete it, then restore; the
app can stay installed. Restore before the app re-creates the namespace (its
bootstrap does on start): a plain restore refuses a namespace that is not
empty.
