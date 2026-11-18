---
section: fixes
cases: [1427]
---

### A namespace still being backfilled now says "still syncing"

`GET /api/reporting-sync/parity` can only answer whether reporting caught up if
it knows its own writes are still in flight. The definitions sweeps —
terminologies, terms, term relations, template metadata — were recorded as in
flight only when the whole-namespace batch sync launched them. Run by service
startup, by `POST /sync/batch/terminologies|terms|term_relations|templates`, or
by the standing parity check's own repair, they ran invisibly: right after a
restart `/parity` proved `terms_mismatch` and `term_relations_mismatch` against
term tables the backfill was still filling, and answered `failed` — the one
verdict that advises re-running the batch sync with `force=true`, which drops
the tables a running sync is writing into.

Every sweep now records its own scope, and a backfill step holds that scope
across its waits as well as its writes — the wait for its source and the backoff
after a listing that came back short, which is where most of a slow start goes. A
check that lands mid-backfill answers `unverified` with `sync_running`, and the
restore engine waits on that answer instead of recording a gap that was never
one. The claim is bounded: after five failed attempts in a row a step stops
reporting itself as progress and simply keeps retrying, because a step that has
not reached its source is waiting on an outage rather than writing, and the
instance-wide scope it would otherwise hold answers `sync_running` for every
namespace and leaves the standing parity check nothing to do until the source
returns. A term-closure rebuild waiting on its debounce counts as in flight too —
that is exactly the window in which `term_closure` legitimately lags its terms —
and the service and the event worker now share one rebuild queue, so the rebuild
a term event queued is the one parity knows about.

No action needed. A script that branches on `verdict` may see `unverified` where
it used to see `failed` during a restart; `unverified` is the accurate answer
there, and the batch sync it advises is the plain one, not `force=true`.
