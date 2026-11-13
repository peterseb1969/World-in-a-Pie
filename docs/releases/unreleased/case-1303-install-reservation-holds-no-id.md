---
section: action-required
cases: [1303]
packages: [wip-client]
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
- The stored reservation records (MongoDB `namespace_reservations`; the
  reservations listing serves no id) hold no namespace id for an install
  reservation whose namespace does not exist. The next key-file import
  (within seconds of the upgrade) clears the ids such reservations were given
  before.
- A system backup leaves such a reservation out: it carries no id, and the
  target's own deployer reserves its names. The archive's shape is
  unchanged, so a Registry from before this change reads it. An archive that
  does carry one (`namespace_id: null`, written by develop builds between
  this change and its fix) is read, and the reservation is skipped
  (`managed_on_source`).
- A grant archived without a namespace id (given while no namespace of its
  name existed on the source: a key-file grant, or a key created listing a
  namespace not yet created) is written effective for the namespace that
  takes the name later, and is skipped (`unrelated_namespace_here`) where a
  namespace of the name exists on the target already. Such grants now
  include those on a deleted, install-reserved name, which used to carry the
  reservation's id.

This narrows two statements of the earlier CASE-1084 note in these unreleased
notes: a reservation that outlives its namespace gets a new id only when a
runtime key made it, and the startup migration gives an id to every
namespace and runtime reservation (an install's reservation of an absent
namespace keeps none). The CASE-1084 guarantee that an unrelated namespace of
the same name does not receive the archive's grants holds, now also for
grants archived without an id.

**Action:** if you branch on a system restore's skip reasons, handle
`unrelated_namespace_here`. To restore an app's namespace: delete it, then restore; the
app can stay installed. Restore before the app re-creates the namespace (its
bootstrap does on start): a plain restore refuses a namespace that is not
empty.
