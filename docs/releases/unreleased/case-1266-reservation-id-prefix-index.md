---
section: fixes
cases: [1266]
---

### One ID prefix is held by one namespace reservation, kept by the database

- **A reservation's claimed ID prefixes are a stored, uniquely indexed field**
  (`id_prefixes` on the reservation record, as on a namespace). Two
  reservations can no longer claim one prefix, whichever process or path
  writes them: a key create or widen, a re-reservation that changes the
  settings, the install's reservations file, or a system restore. The API
  refuses such a write with 409 (with `managed_by_deployer` when the install's
  reservation holds the prefix); the reservations-file import logs and skips
  it; a system restore skips it as `id_prefix_taken`. Reservations stored
  before the field get it at the Registry's next start; two that already
  claim one prefix are named in its log, to release one of them.
- **A namespace widen that does not land leaves the key as it was**: the
  namespace it added to the key's scope is taken back, as is the reservation
  it made.
- A restore's endpoint-repair pass that the Registry stops part-way no longer
  leaves "resolved half cleared" warnings beside its "Nothing was cleared"
  error; the warnings are said once the pass has completed.
- Docs: how an API door resolves an identifier (no UUID bypass with namespace
  context, no best-effort pass-through), and every error the resolvers raise.
