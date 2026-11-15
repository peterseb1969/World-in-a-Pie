---
section: fixes
cases: [1325]
packages: [wip-client]
---

### A hard delete whose Registry entry survives says so; the integrity summary and the rehash say what they count

- **Half-applied deletes are reported.** A hard delete that removed every
  version but could not remove the document's entry on the Registry logged the
  failure and answered `deleted` with an empty `warnings` list. The soft path in
  the same method already reports what its retirement came back with, so the
  asymmetry read as: no warning, nothing to look at. The item now carries a
  warning naming the entry the Registry kept. The delete still stands — the
  document really is gone — and `status` and the `succeeded` count are
  unchanged, so an app only sees more where it previously saw nothing.
- **Template identifiers are escaped into the URL.** When document-store asks
  template-store for a template, the identifier it was given — a document's
  template reference, a value, an id from an archive — is now percent-encoded as
  one path segment. Sent raw, `#` started a fragment (silently dropped) and `?`
  started a query string, so template-store saw only the part before either
  character: a lookup that should be a miss resolved as another template, or as
  none.
- **Integrity summary units.** `total_documents` and `documents_checked` count
  document **versions** (a document with three versions counts three),
  `documents_with_issues` counts **documents**, and each per-type counter counts
  **issues**. Several fields said "Documents" while counting rows, and eight
  carried no description at all; each now names its own unit. The counts
  themselves are unchanged.
- **Rehash outcomes.** `status: "unresolved"` covers two cases, not one: a
  referent identity field with no stored resolution, and an identity field the
  row does not carry at all. `status: "error"` names its second cause too — an
  identity path the template cannot classify, not only an unreadable template
  version. `@wip/client`'s `RehashIdentitiesItem` docs match the served text.
