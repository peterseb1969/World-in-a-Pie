---
section: action-required
cases: [1328]
packages: [wip-client, wip-toolkit]
---

### A restore never stops on reporting; the verdict's values changed

A restore no longer halts because the reporting layer is not ready. The
structural gate between templates and documents is gone, along with the merge's
version of it: the documents are what a restore protects, and the reporting
tables are derived from them, so a namespace whose tables were missing the whole
restore still ends up correct once its batch sync lands. What the gate used to
stop — a reporting-sync outage, a slow sync, a table that failed to build — now
runs after the documents as one verification phase: trigger the namespace batch
sync, wait while reporting-sync says it is still working, and record the result
on the job.

Reporting-sync's parity check now carries the verdict itself, so there is one
copy of the judgement instead of two:

    GET /api/reporting-sync/parity?namespace=kb
    { "verdict": "failed",
      "reasons": [ { "kind": "template", "name": "BOOK", "issue": "count_mismatch",
                     "detail": "BOOK: 3 of 10 live documents have rows",
                     "expected": 10, "actual": 3 } ], … }

`result.reporting_verification.status` and its per-namespace `status` use the
same three values, computed by reporting-sync and recorded as given:

- `verified` — every check ran and found nothing missing.
- `failed` — a check proved rows or tables missing. `reasons` names each one.
- `unverified` — nothing could be settled: reporting-sync or template-store did
  not answer, the wait gave out while the sync was still running, there was
  nothing to check (documents skipped), or the run did not verify at all (a
  fresh restore, whose sync starts when the job completes).

`incomplete`, `unavailable` and `not_checked` are gone, and the per-namespace
`gaps` list is now `reasons`, carrying reporting-sync's findings with a
`detail` line each. An answer that cannot be trusted is never reported as a
clean one: a namespace whose template list could not be read, or whose batch
sync is still running, is `unverified` rather than a zero-finding pass.

**Action:** scripts reading `reporting_verification.status` (or the `gaps`
field) must map `incomplete`/`unavailable`/`not_checked` onto `failed` or
`unverified`. `@wip/client`'s `NamespaceParityResult` gained `verdict` and
`reasons` (with the new `ParityReason` type); reading `ok` still works and
means the same as before. `wip-toolkit restore` now prints the verdict and the re-sync to
run when it is not `verified`; the same information is on the job record, and
`wip restore` exits 0 for a completed job whatever the verdict — decide on
`reporting_verification`, not on the exit code. To rebuild a namespace's tables
from MongoDB: `POST /api/reporting-sync/sync/batch?namespace=kb&force=true`,
then confirm with `GET /api/reporting-sync/parity?namespace=kb`.
