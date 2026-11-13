---
section: fixes
cases: [1279]
packages: [wip-client]
---

### Document writes that race report a conflict, and a delete is never undone by one

- **A delete stays a delete.** A soft delete changes a document's status without
  moving its `revision`. A PATCH or POST that read the row before the delete
  could still write it back as active, while its Registry entry stayed retired.
  An in-place write now also requires the status it read, so it fails with
  `concurrency_conflict` instead. A POST retry that finds the document deleted
  or revived meanwhile is refused the same way and must be sent again.
- **A delete keeps a concurrent write.** The delete rewrote the whole row from
  its own earlier read, which could drop an in-place write that landed in
  between. It now changes only the status of the row as it is now.
- **A single-item `POST /documents` that loses a write race** answered 500. It
  now answers 200 with a `concurrency_conflict` item, as the bulk path does. This
  covers both an in-place overwrite that kept losing and a version slot that
  stayed taken after the retries.
- **A restore merge onto a `versioned: false` document** now replaces it only if
  the document still holds the revision the merge read. A document written
  meanwhile keeps its copy, and the merge warns about it instead of counting
  it as replaced.
- **A bulk create's auto-synonym stage** (after the documents are written) no
  longer turns an unexpected failure into a whole-batch 500: each new
  document's row carries a warning. An unreadable Registry answer refuses the
  synonyms like any other Registry failure. A document the stage rolls back is
  no longer announced by an in-place overwrite earlier in the same batch.

### Smaller document-store corrections

- `GET /documents/{id}/versions` carries each row's `revision`, the `if_match`
  token. `@wip/client`'s `DocumentVersionSummary` has the field.
- A PATCH result row carries `template_version`, as its description promised.
- `GET /documents/by-identity/{hash}` answers an ambiguous hash with the
  platform's 409 shape: `error_code: ambiguous_reference`, with
  `detail.candidates`, `detail.total` and `detail.identifier`. It used to answer
  `error_code: ambiguous` with only the candidates.
- A PATCH of a term or reference identity field on a version stored without
  that field's resolution is still refused. Its message now says only the
  spelling could be compared, not that the change names another entity.
- `api-conventions.md` lists `concurrency_conflict` among document create's
  codes. The `@wip/client` README shows the by-identity call with its namespace
  and `templateId`.
