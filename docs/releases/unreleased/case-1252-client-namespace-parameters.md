---
section: action-required
cases: [1252]
packages: [wip-client, wip-react]
---

### @wip/client: every call whose route takes a namespace can send it

A key that can read several namespaces has to name one wherever a route
resolves a synonym or scopes a read. The client could not do that on these
calls, so they failed (400 or `not_found`) for such keys:

- **`reporting.getRecentActivity`** takes `namespace` in its params, and
  **`getTermDocuments` / `getReferencedBy`** take it as a trailing argument.
  The same goes for `useActivity`.
- **`documents.updateDocument(s)` and `deleteDocument(s)`** take
  `options.namespace`. Creates need none: each document names its namespace
  in the body.
- **Every file call** (`getFile`, `getDownloadUrl`, `downloadFileContent`,
  `updateMetadata`, `deleteFile(s)`, `reactivateFile(s)`, `hardDeleteFile`,
  `getFileDocuments`) takes a trailing `namespace`.

Required namespaces are now typed required:

- **`files.uploadFile`:** `namespace`, the fourth argument, is required. Its
  order is unchanged (`uploadFile(blob, name, undefined, ns)` still works),
  and an empty one throws before sending. `useUploadFile` requires it too.
- **`defStore.getAncestors` / `getDescendants` / `getParents` / `getChildren`**
  require `{ namespace }`.

Two docstrings now match the service. `reporting.search` without a
namespace is refused for a multi-namespace key that is not a platform
admin. A relationship or traverse call without a namespace lists edges in
every namespace the caller can read.

**Action:** calls to `uploadFile`/`useUploadFile` and the four ontology
traversals that may pass no namespace no longer compile. They already
failed at the service, with a 422.
