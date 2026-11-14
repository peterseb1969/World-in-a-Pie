---
section: fixes
cases: [1266]
packages: [wip-auth]
---

### Key reservations, term-resolution batching and key-file loading close their remaining gaps

- **A key create whose key is not stored keeps no reservation it made**, for
  any failure, not only a name clash; a namespace widen that does not land
  drops the reservation it made too.
- **Two concurrent reservations cannot claim one ID prefix.** Each writer looks
  again once its record is stored, and the later one is refused (409) and
  removed.
- **A bulk write asks def-store once per term value per batch** even when two
  documents plan the same value while one of them awaits the Registry; the
  later one waits on the first answer instead of asking again.
- Wording: the resolve docs (UUIDs are verified, a 30-second cache, 200 ids per
  Registry call, every error `resolve_entity_ids` raises) and
  `docs/api-conventions.md` (a `skipped` row may carry an `error_code`: branch
  on `status`).
