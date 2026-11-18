---
section: fixes
cases: [1427]
---

### A term-closure rebuild that will not land stops reporting itself as progress

`GET /api/reporting-sync/parity` holds its verdict open — `unverified`,
`sync_running` — while reporting-sync has a closure rebuild queued for the
namespace, because that debounce window is exactly when `term_closure`
legitimately lags its terms. Two rebuilds went unreported and one reported too
much:

- a repair that calls the rebuild directly, which is how the standing parity
  check closes a `term_closure` gap, sits in no queue, so `/parity` queried
  during it still proved the closure stale and advised `force=true` while the
  repair was writing;
- a namespace whose rebuild keeps failing (a permission or schema fault of its
  own) retried forever, and every retry re-armed that claim: `term_closure_stale`
  could never be named, and the standing check skipped the namespace on every
  pass, so its "not healed after N repairs" error never fired either.

A rebuild now counts as in flight while it writes, however it was started, and
a rebuild that has failed five times in a row counts as what it is — a gap an
operator has to clear. The retry loop keeps going; only the claim of progress
ends, and a rebuild that lands clears the report.

No action needed. A namespace with a broken closure now shows up as `failed`
with `term_closure_stale` instead of disappearing from every check; that is the
gap the batch sync (or a schema fix, if the sync cannot write the tables) is for.
