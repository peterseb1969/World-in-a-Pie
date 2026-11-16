---
section: fixes
cases: [1262]
---

### A hard delete's referrer check fails on a Registry outage; an export leaves locked namespaces out

- **Hard delete, Registry outage.** A hard delete refused because other
  documents reference it lists sample referrer ids for the namespaces the
  caller can read. When the Registry could not answer that read check, the
  namespace was treated as unreadable and its sample ids were silently
  dropped. The item now fails as a Registry outage (`registry_error`,
  `details.rule: registry_unavailable`) and nothing is deleted.
- **Registry export, locked namespace.** `POST /api/registry/entries/export`
  gave a platform administrator the entries of a namespace locked for
  deletion. They now come back `not_found`, as every other Registry surface
  answers for a locked namespace.
- **Docs.** `docs/api-conventions.md` now says when a Registry outage
  answers 503 and when it is a per-item `registry_error`. It is a 503 while
  the request's identifiers are resolved and its permissions checked, before
  any item is written; that covers the ids inside a bulk write's items, which
  every store resolves together first. It is a per-item `registry_error`
  during the Registry work for one item as it is written. The 409s outside
  bulk item writes (an import of something that exists, a locked namespace, a
  backup job not finished) are listed too. It also says that
  `/health`, `/api/<svc>/health` and `/ready` answer 503 although the served
  specs declare only 200.

**Action:** none.
