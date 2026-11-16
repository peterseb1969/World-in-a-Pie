---
section: fixes
cases: [1353]
---

### A `document_id` refusal stops offering a fix that could not work, and the deletion-status gate says what it covers

- **The advice on `document_id_mismatch` now fits the state it found.** An
  upsert that names a `document_id` the identity does not resolve to was
  refused with "Omit document_id to let the identity decide, or pass version
  as well for a restore." Where the identity already belongs to a document,
  the second option cannot work: adding a `version` makes the write pinned, and
  a pinned write keeps its `document_id`, so the same item is refused again.
  That refusal now says only "Omit document_id to let the identity decide."
  The version offer stays where it does work — an identity no document has
  yet — and the pinned shape's own advice is unchanged. Same text on the
  single and the bulk door alike. No status, error code or `details` field
  changes; only the sentence.
- **`GET /namespaces/{prefix}/deletion-status` explains its gate correctly.**
  The endpoint is for platform admins, and its documentation justified that by
  saying a namespace admin could not exist while a journal does. It can: a
  completed deletion does not hold the prefix, so the name can be created
  again and its owner granted `admin` there. The text now says the namespace
  is closed while the deletion runs and ungranted while it stays deleted, and
  that an admin of a later namespace of the same name is not admitted because
  the journal describes the earlier one. The gate is unchanged.
