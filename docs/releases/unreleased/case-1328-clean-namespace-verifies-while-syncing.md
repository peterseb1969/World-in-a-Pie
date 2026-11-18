---
section: fixes
cases: [1328]
---

### A restore into a namespace whose reporting tables already match is recorded as verified

After its documents, a restore or merge triggers the namespace's batch sync.
It then records the verdict reporting-sync gives. That sync registers its jobs
at once, and the parity verdict treated any running sync as "still syncing",
even when every table, row count, term table and view already matched. This
is the usual state of a merge into a live namespace.

The wait therefore ran until its 60-second stall window ran out. The verdict
was recorded as `unverified`, with a warning to re-run the sync. The wait's
progress signal made it worse: it counted only document-table rows, so the
definitions sweep, which writes only term tables, also looked stalled.

Now:

- A counted parity check that finds nothing missing is `verified` while a
  sync is still running. Such a sync can only write rows that are already
  there. Over the surfaces it compared, and only those: a template whose
  rows cannot be counted against its documents (`sync_strategy: all_versions`,
  `counts_comparable: false`) keeps the verdict open while its sync runs.
- A check that found a gap, or a structure-only check (counts not compared),
  still waits on the running sync.
- Term-table rows count as progress in the restore's wait.
