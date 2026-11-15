---
section: fixes
cases: [845]
---

### Document-store's `/debug/*` routes need a wip-admins key

`GET /debug/timing`, `POST /debug/timing/reset`, `GET /debug/cache` and
`POST /debug/cache/clear` on document-store accepted any valid API key, so
an app key scoped to one namespace could read service-wide timing, reset it,
or clear the template and def-store client caches for every caller. They now
answer 403 to every key outside `wip-admins`, wip-services keys included, and
401 without a key. No platform component calls them.
