---
section: action-required
cases: [1253]
packages: [wip-client, wip-react]
---

### @wip/client: parameters the services take are sendable, and types carry what they send

- **File delete takes `force`.** `files.deleteFile(id, namespace?, { force })` and
  `deleteFiles` delete a file documents still reference. `useDeleteFile` and
  `useDeleteFiles` accept `{ fileId(s), namespace?, force? }` as well as the bare
  id(s). A forced delete keeps the references, so `hardDeleteFile` still
  refuses the file until they are gone.
- **`documents.deleteDocument` refuses `version` or `force` without
  `hardDelete: true`.** Its options type says so too (`DeleteDocumentOptions`).
  A soft delete always retires the newest active version, so a `version`
  was ignored, and the newest one retired.
- **Upload metadata `custom` is stored.** `POST /api/document-store/files`
  takes a `custom` form field (a JSON object; anything else is a 422), and
  `uploadFile` sends it. An empty `description` or `category` is sent rather
  than dropped.
- **`defStore.importTerminology` sends its options as query parameters**,
  where the route reads them: `skip_duplicates`, `update_existing`,
  `batch_size` and `registry_batch_size`. In the body they had no effect.
  Its `terminology` is typed as the summary the import returns
  (`ImportedTerminologySummary`, status `created` / `exists`), and an export's
  `terminology` as the definition it carries (`ExportedTerminology`).
- **Types:** `Namespace.status` includes `locked` and `SyncStrategy` includes
  `disabled`. `SyncStatus` carries `events_dropped`, `recent_drops`,
  `events_skipped_deleted_namespace` and `postgres_service_roles_ok`.
  `BatchSyncJob` carries its timing and `rows_written` / `deduplicated` /
  `dropped_relations`.
- **`registry.addSynonym`, `removeSynonym`, `mergeEntries` and
  `deactivateEntry` throw any refusal** (`forbidden`, `not_found`,
  `target_not_found`, `error`, ...) as a `WipBulkItemError` with `errorCode`
  and `details`, where they used to resolve with it. Their hooks therefore
  surface a refusal as `mutation.error` and invalidate nothing.
  `addSynonym` resolves on `added` and on `already_exists`.

**Action:** code that read `status` from those four registry calls to detect
a refusal now gets the thrown error instead. Callers passing `version` to a
soft `deleteDocument` must drop it, or delete hard.
