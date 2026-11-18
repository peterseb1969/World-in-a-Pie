---
section: fixes
cases: [1427]
packages: [wip-client]
---

### A clean parity answer no longer covers surfaces it never compared

`GET /api/reporting-sync/parity` documents `verified` as "every check ran and
found nothing missing". Two surfaces did not meet that.

A template with `sync_strategy: all_versions` has no expected row count
derivable from one query — its row says `counts_comparable: false` and its rows
are never compared against its documents. An all-clear that leans on one does
not show that the sync has written them, but it used to settle a running sync: a
merge or a restore whose batch sync was still rewriting rows was recorded as
`verified` on the first poll, and if reporting-sync restarted mid-job the
half-written tables kept serving under a clean record. Such a template now keeps
the verdict `unverified` with `sync_running` while its sync is in flight. A
namespace already at parity, where the rows were counted and match, still
verifies while its sync re-runs over them.

And the `terminologies` table was the one def-store-derived table the check
never compared: active `terms`, `term_relations`, `term_names` and
`term_closure` rows were, the vocabularies those terms live in were not. A
namespace whose terminology sweep never ran, or stopped at its first page,
answered `verified`. Its `derived_tables` now carry a
`terminologies_mismatch` row with the expected and actual count, and the standing
parity check closes that gap with the namespace's terminology sync, like the
other derived tables.

**Action:** none required. A script that branches on `verdict` may see
`unverified` where a namespace with `all_versions` templates used to answer
`verified` while its sync ran; `unverified` is the accurate answer there, and the
batch sync it advises is the plain one, not `force=true`.
