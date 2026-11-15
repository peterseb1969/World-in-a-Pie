---
section: fixes
cases: [1265]
---

### A record removed during a system restore is skipped, not reported replaced

A key, grant or reservation another request deleted between a restore's plan
and its write was reported `replaced` (a write that matched nothing), and a
key was handed to the key provider though it no longer existed. Such a record
is now listed under `skipped` with `concurrent_write` ("removed by another
request during the restore"), as one created meanwhile already was.
