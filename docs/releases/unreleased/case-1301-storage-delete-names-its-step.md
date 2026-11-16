---
section: action-required
cases: [1301]
---

### A namespace deletion's storage delete now names the journal and step its keys come from

`POST /api/document-store/files/storage/delete` removes stored objects on the
Registry's behalf. Until now it authorised any key list that arrived while a
deletion ran under the namespace's name: a key with no file record behind it —
one left by an earlier, degraded deletion — was deleted unverified, and a list
replayed later was authorised by whatever deletion the name carried then. The
request must now name its source, and document-store checks it against the
journal:

- the body carries `journal_id` (the running deletion journal's id) and
  `step_order` (the file-storage step in that journal whose keys these are). A
  request naming no step is refused **422**; one naming a journal other than the
  deletion in progress, a step that journal does not have, or a step whose
  recorded keys can no longer be read is refused **409**. A refusal deletes
  nothing.
- a key the step did not record is refused per key (`refused[].reason`) and left
  untouched, beside the existing cross-namespace refusal. The journal's counts
  therefore say what the deletion actually removed.

`GET /api/registry/namespaces/{prefix}/deletion-status` now answers
`journal_id`, and a new privileged read
`GET /api/registry/namespaces/{prefix}/deletion-steps/{order}/keys` answers the
keys that step recorded — platform admins only, 404 for anyone else and for a
step that is not a file-storage delete, and `keys_available: false` with the
reason when the step's key batch has been dropped.

**Action:** upgrade document-store and registry together. With only one side
upgraded, a deletion's file-storage step is refused — an un-upgraded
document-store rejects the new fields, an un-upgraded Registry sends none — and
its objects stay in storage. The step is recorded as degraded and the journal
completes with warnings; run `resume-delete` after both sides are current to
finish it.
