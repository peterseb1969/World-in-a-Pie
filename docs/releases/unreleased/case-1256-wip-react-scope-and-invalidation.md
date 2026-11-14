---
section: fixes
cases: [1256]
packages: [wip-react, wip-client]
---

### @wip/react hooks take the namespace their client methods take, and refresh caches after a failed bulk import or a namespace delete

- **Scope parameters reach the client.** `useDocument(id, { version?, namespace? })`, `useQueryDocuments(query, { namespace? })`, `useTerms(terminologyId, { …, namespace? })`, `useTerm(id, { namespace?, terminology? })` and `useTerminology(id, { namespace? })` now forward these to the client and key their caches on them. Previously they were dropped, so under a key with several namespaces a value-form identifier could not be scoped: `useQueryDocuments` answered 422. Each scoped key still sits under `wipKeys.<entity>.detail(id)`, so existing invalidations cover every scope.
- **`@wip/client`:** `defStore.getTerminology(id, { namespace? })` takes the namespace the endpoint accepts.
- **`useBulkImport` invalidates when the run settles**, failure included. Previously it invalidated only on success, so a batch that threw left the batches already written out of the cache.
- **`useTriggerBatchSyncAll` takes `namespace`**, which `force` requires. Previously the server refused `force` without a namespace (400) and the vars type had no way to send one.
- **`useDeleteNamespace` invalidates every cache (`wipKeys.all`) on settle**, not only `registry.all`, so the deleted namespace's entities stop rendering.
- **Docs:**
  - The README's `useTerm('COUNTRY:United States')` example (a 2-part shorthand the server refuses with 422) now uses `'wip:COUNTRY:United States'` or `{ terminology: 'COUNTRY' }`.
  - `continueOnError` is documented with its real default, `true`.
  - `useReportQuery` documents the row cap and `truncated`.

**Action:** none required. A caller that passed a second argument the hooks used to ignore now has it applied.
