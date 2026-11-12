---
section: action-required
cases: [1251]
packages: [wip-client, wip-react]
---

### @wip/client: eight calls that read failure as success now report it

Each of these resolved quietly when the call had failed:

- **`registry.searchEntries`** throws `WipBulkItemError` when the Registry
  answers the search with a per-item error (an unknown entity type, such as
  the singular `'term'`, or an internal error), where it used to return no hits.
- **`registry.deleteNamespace`** resolves to the deletion's status
  (`NamespaceDeletionResult`), not `void`. `in_progress` means a step was
  deferred and the namespace is **not** gone yet. Two new calls go with it:
  `getDeletionStatus` and `resumeDeletion`. It takes `{ force }`, and no longer
  sends an undeclared `confirm` parameter. `useDeleteNamespace` resolves to
  the same result.
- **`documents.importDocuments` / `previewImport`** throw `WipValidationError`
  when the service answers an unusable file (empty, unparseable, a bad
  mapping) with a 200 `{error}`.
- **`previewImport`** returns `sample_rows` and `total_rows`, as the service
  sends them. `rows` was never filled.
- **`reporting.healthCheck`** asks the routed `/api/reporting-sync/health`
  (the bare `/health` is refused through Caddy, the router and @wip/proxy).
  It is true only for `healthy`, so a `degraded` service (PostgreSQL or NATS
  down) is false. An authentication failure is thrown.
- **`reporting.awaitSync`** needs a `query`. Its form without one counted
  events across every namespace and could not say that a write had synced,
  so it is gone. The query form takes `namespace`, and it retries a missing
  table, a 5xx or a network failure until its deadline. Any other error is
  thrown at once.
- **`defStore.exportTerminology({ format: 'csv' })`** resolves to the CSV text,
  where it used to resolve to `undefined`.

**Action:** callers of `awaitSync()` without a query must pass one. Callers
reading `previewImport(...).rows` read `sample_rows`. Callers of
`deleteNamespace` should check `status`. Code that counted on these calls
not throwing will now see the error.
