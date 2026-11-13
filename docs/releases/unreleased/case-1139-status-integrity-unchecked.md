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
