---
section: action-required
cases: [1147]
packages: [wip-client]
---

### A version 1 archive's scope reads land only where their namespace is, and only for the archive's own keys

A system restore of a `version: 1` archive writes a read grant for each
namespace a runtime key's list named and the archive holds no grant for.
These grants are now placed more carefully:

- **The namespace id comes from a reservation or an effective grant.** An
  orphaned grant's id is never used. Before, an orphan on the same prefix
  (made for an earlier namespace of the name) could lend its id, and the read
  was written orphaned against a namespace that never comes.
- **With no id in the archive, the name decides, and a reservation alone is
  not enough for an effective grant.** Where a namespace of the name is here,
  the read is effective, as before. Where only a reservation of the name is
  here, the read is written orphaned under the reservation's id, so it
  becomes read when that namespace is created and never for another one
  taking the name. Where only an install's reservation is here (it holds no
  id yet) or nothing of the name, it is skipped (`scope_namespace_absent`),
  and the skip says to run the system restore again after the namespace's
  restore.
- **Only the archive's own key gets one.** A key of this install that shares
  the name (a config key, or one wip-deploy manages) is skipped with the new
  reason `scope_key_not_restored`.
- **A key in a stack-wide group gets no read.** A key in wip-admins or
  wip-services is never scoped, so the restore skips one that carries a list
  (`scoped_privileged_key`, see CASE-1314) and writes no read for it.
- **`scope_read_grants` lists only the reads written** (or, in a dry run, to
  be written). A skipped one is under `grants.skipped`; one written orphaned
  is also under `grants.orphaned`.

**Not every version 1 archive predates the change.** Develop builds of
2026-11-08 to 2026-11-13, which are in no release, already read only through
grants but still wrote `version: 1`. Restoring their archives also gives back
any read revoked in that window. The guide, the restore warning and the
`version` description now say so.

**Action:** if you branch on `SystemRestoreSkipped.reason_code`, handle
`scope_key_not_restored`. `@wip/client`'s type includes it.
