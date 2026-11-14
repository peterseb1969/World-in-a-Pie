---
section: fixes
cases: [1297]
packages: [wip-auth, wip-archive]
---

### Requests waiting on a slow Dex share one key fetch; a restore names a term relation's type and terminologies as referents

- **wip-auth, JWKS:** when one fetch of Dex's signing keys took longer than
  the attempt limit (10 s apart), every request waiting on it fetched again
  in turn, so authentication queued behind a slow Dex. Requests waiting on a
  fetch now use its result, success or failure. And with no usable copy of
  the keys yet (at startup, or after the one-hour limit), a request that
  arrives while a fetch runs now waits for it; it used to answer 503 at once.
  A request that has a usable copy is answered from it while a scheduled
  re-read runs, as before.
- **wip-archive:** `referenced_ids` now names a term relation's
  `relation_type`, `source_terminology_id` and `target_terminology_id`. The
  restore remaps all three, but they were never collected as referents, so a
  restore neither checked them nor resolved them from the target. The drift
  test between the referent slots and the remap rules now runs in both
  directions.

**Corrections to earlier notes in this release:**

- The JWKS note says a bad response "answers 503 instead of 500". A bad
  response is a failed fetch: while a usable copy of the keys exists,
  requests are answered from it. 503 comes only when no copy is usable (never
  fetched, or not re-read for an hour). Its keys are checked for shape only
  (a JWKS document holding at least one key with a `kid`), not for content.
- The restore note says affected documents "can be found exactly" where a
  field's term id in `term_references` differs from the one in
  `references[].resolved`. That finds term fields only. A **terminology**
  reference field writes no `term_references` entry, so a document whose
  terminology reference kept the source's id has no such mismatch. Its
  `references[]` entry with `reference_type: terminology` names, in
  `resolved.terminology_id`, a terminology this install does not hold, or
  (after a fresh restore beside its source) one of the source namespace's.
  Check those entries as well before deciding on a repair. (This
  fingerprint is derived from the code, not checked against affected data.)
