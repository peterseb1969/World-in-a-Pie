---
section: fixes
cases: [1275]
---

### A pinned template lookup that finds nothing leaves no phantom cache entry

`GET /api/document-store/debug/cache` counted every key in the pinned-version
template cache, and the pinned path stored the result of every read — including
"no such version". Those null entries can never answer a lookup (a null is read as
a miss), carry no namespace for the freshness stamp to evict them by, so they
outlived every eviction the cache has. The reported size grew with every miss for
a template version that does not exist and never came down.

A miss now stores nothing, so the size means cached templates. Lookups are
unchanged: a version that appears after a miss is fetched and found on the next
read, exactly as before.

**Action:** none.
