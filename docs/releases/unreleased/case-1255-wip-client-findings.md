---
section: fixes
cases: [1255]
packages: [wip-client]
---

### `@wip/client`: retries, errors, bulk import and the backup event stream do what their docs say

- **A GET answered 500 is no longer retried.** Only 502, 503 and 504 are.
  Before, every mapped server error went through the retry loop a second
  time.
- **`WipValidationError.statusCode` is the status the service answered: 400
  or 422.** It was always 422, so a check for `statusCode === 400` never
  matched.
- **`bulkImport` keeps its progress when a batch's request fails.** Every
  item of that batch counts as failed, and the import goes on under
  `continueOnError` (the default) or stops after it.
  `BulkImportProgress.rejected` lists each such batch: its offset, its size
  and the error. Before, the exception discarded every count.
- **`streamBackupJobEvents` throws `WipNotFoundError` when the job
  disappears mid-stream.** It used to end as if the job had finished.
- **A single-item write whose response carries no result** (a 204, an empty
  body, no `results`) throws a `WipError` naming the call, instead of a
  `TypeError`.
- **New request options the services already took:**
  - `createDocuments(items, { continueOnError })`;
  - `CreateDocumentRequest.on_synonym_conflict`;
  - `resolveReference(..., namespace)`, which a key that can read several
    namespaces needs for a template given by value.
- **Type changes:**
  - `BaseService` is exported, as the README said.
  - `BackupJobSnapshot.namespaces` is always present, and
    `archive_backend` is added.
  - `AddSynonymRequest` / `RemoveSynonymRequest` scope fields are optional.
  - Peer projections carry `peer_error_code` / `peer_error`, and their
    `status` is `active` or `inactive` only.
  - `Template.referent_identity_paths` and `CascadeResult.error_code` /
    `details` are added.
  - The references methods no longer accept `entityType: 'file'`, which the
    service refuses.
  - `DocumentCreateResponse` is deprecated: no endpoint returns it.
- **Corrected doc comments:** upsert rules for scoped keys, the attachment
  name pattern, the API-key page size, restore `namespace_map`, traversal
  depth, `versioned`, edge-type endpoint lists, template update
  immutables, view registration and view reads, and the deprecated
  `search` `limit`.

**Action:** an app that relied on a 500 being retried, on 422 for every
validation error, or on `bulkImport` throwing when a batch request fails,
checks those paths. Code that passes `entityType: 'file'` to the references
methods no longer compiles; the service refused that call with 400.
