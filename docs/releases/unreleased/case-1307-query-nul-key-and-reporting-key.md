---
section: fixes
cases: [1307]
---

### A query filter value with a NUL key is refused, and document-store's reporting-sync key falls back to `API_KEY`

- **`POST /api/document-store/documents/query`:** a filter value whose object
  holds a key with a NUL byte, at any depth, is now refused with 422 ("a key
  may not hold a NUL byte"). It used to reach the BSON encoder and fail with
  500. A NUL inside a string value is still plain data. The served
  descriptions of `QueryFilter`, its `field` and its `value` now name every
  refusal: an empty segment, a `$` prefix, a NUL byte.
- **document-store start-up:** the key it presents to reporting-sync is read
  from `REGISTRY_API_KEY`, else `WIP_AUTH_LEGACY_API_KEY`, else `API_KEY`.
  Before, with `WIP_AUTH_LEGACY_API_KEY` set but empty, document-store refused
  to start although `API_KEY` satisfied every other chain it checks.
