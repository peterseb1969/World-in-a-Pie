---
section: fixes
cases: [1264]
---

### Document reads answer a hidden id as a missing one for every key; integrity runs keep their own record

- **Hidden vs missing**: document and file reads answer an id in a namespace
  the caller cannot read exactly as an unknown id, also for a key scoped to
  several namespaces (it got "Could not resolve … in namespace 'None'" where
  an unknown id got "Document not found").
- **Integrity**: the checks a run could not perform are recorded per run, so
  integrity runs in flight at once (one validation job per restored
  namespace, the startup sweep, `/health/integrity`) no longer erase each
  other's record and report `healthy` for data never checked. The startup
  log says how many documents it checked of how many, and no longer prints
  "found 0 issues" for an incomplete run.
- **Storage cleanup**: an object already gone counts as deleted on
  `POST /files/storage/delete`, as its description says.
