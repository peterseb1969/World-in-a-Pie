---
section: fixes
cases: [1270]
---

### A pinned write never lands beside a live document; a key's PATCHed namespaces carry their grants

- **A restore-shaped write (`document_id` plus `version`) whose identity
  belongs to another, live document is refused** with
  `invalid_request` / `details.rule: "document_id_mismatch"`, naming that
  document. It used to be written as a pinned version of the live document,
  beside its active head: two active rows, and the head's file references
  released. Single and bulk writes alike.
- **`PATCH /api/registry/api-keys/{name}` with `namespaces`** gives each
  namespace it adds a read grant and revokes the grant on each it drops, as
  widening and narrowing one namespace at a time do. It used to change the
  scope only, so an added namespace was unreadable and a dropped one's
  grant stayed, to come back with a later widen.
- **A template field with `array_item_type: "array"` is refused**
  (`validation_failed`, `details.rule: "nested_array_unsupported"`): a nested
  array has no item type to validate its items against, so they passed every
  check. Use an array of objects whose template holds the inner array.
- **A non-admin namespace upsert decides standing first:** a caller without
  it gets the same 403 whatever its body holds (an invalid `id_config` no
  longer answers 422), and standing is decided on the key and reservation
  files as they are now.
- **A dropped edge is keyed by its namespace:** with `drop_unresolved_edges`,
  a document of the same id in another archived namespace (prefixed ids are
  per namespace) is no longer dropped with the edge, and the dropped edge's
  record names the endpoints of its earliest version.
- The CSV/XLSX import reads a field type that is not a name as text instead
  of failing the file; a business-key reference to a retired document
  resolves to its latest version.

**Action:** a hand-written key file entry that lists `namespaces` but no
`grants` imports no grants, so it reads nothing: add `"grants": {}` to read
each listed namespace (every key file wip-deploy renders already does).
