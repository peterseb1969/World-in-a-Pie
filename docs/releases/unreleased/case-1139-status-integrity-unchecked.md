---
section: fixes
cases: [1139]
packages: [wip-toolkit, wip-client]
---

### `wip-toolkit status --integrity` no longer reports `ok` when a store could not check

The integrity check reported `ok` ("no orphaned references") whenever the
orphan counts were zero, even when reporting-sync's aggregate said a store
did not answer or answered without checking everything (a def-store or
template-store outage). It now reports `unknown` (exit code 3), naming the
services in `services_unavailable` / `services_incomplete`.

To tell an outage from a deliberately limited scan, reporting-sync's
`GET /health/integrity` now lists a store whose scan the request's limit cut
short in a new `services_truncated`, and keeps `services_incomplete` for a
store that could not run every check. Before, both went into
`services_incomplete`. The overall `status` is unchanged: `incomplete` for
either. A truncated scan keeps the toolkit at `ok` ("no orphaned references
in scanned window"). `@wip/client`'s `IntegrityCheckResult` type
carries `services_truncated`.

`status --integrity` also reported `ok` for a scan whose findings the three
orphan counters do not count: an orphaned document or file reference, or an
identity hash mismatch, left the aggregate at `error` and the toolkit at
`ok`. Every error-severity finding now makes the check `critical`, and a
warning-severity finding (a retired but present referent) makes it
`warning`. And a store that found something and also could not run every
check answered `warning` or `error`, which hid the outage: reporting-sync
now lists it in `services_incomplete` from its skipped-check counters,
whatever its status, and the toolkit names those services beside the
finding.
