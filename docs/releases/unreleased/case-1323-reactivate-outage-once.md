---
section: fixes
cases: [1323]
---

### A reactivate batch meets an object-store outage once

`POST /files/reactivate` checked each inactive file's bytes against the store, so during an outage every item waited out the full connect-and-retry budget: measured live, 12 files answered after 33 s, and a full 1,000-item batch would answer hours after its caller gave up. The outage the first item meets now carries to the later inactive items, which report `dependency_unavailable` (`details.dependency: object-store`) without the store being asked again. The request still answers 200 with a per-item report, and items the store plays no part in — no such file, no read access, already live — keep their own answer. The storage client's timeout and retry settings are unchanged.
