---
section: fixes
cases: [1279]
---

### A bulk request resolves each item's identifiers in the item's own namespace

`resolve_bulk_ids`, which every bulk write door uses to turn submitted
identifiers into canonical ids, derived a namespace from a key scoped to one
namespace and let it outrank the namespace each item names. A document in
namespace B, written with a key scoped to A, had its `template_id` resolved in
A. The order is now:
1. a namespace the caller passes for the request;
2. the item's own namespace;
3. the key's single namespace, only for an item that names none.

Only an API key's scope derives a namespace. An OIDC token carrying a
`namespaces` claim no longer does.

A caller that relied on the old order must name a shared template the way the
conventions say: `wip:PATIENT` (or its id) for an item outside `wip`. A bare
value always resolves in the item's own namespace.

### Table view and listings

- **Same id in two namespaces.** "Each document's latest version" (table view,
  `latest_only` listings) grouped rows by `document_id` alone. Across several
  namespaces, one namespace's document could hide another's that shares its
  id. A document is now `(namespace, document_id)`.
- **Too many documents to name.** That filter names by id the documents whose
  latest row is not their one active row, most of them soft-deleted. Past
  200,000 of them, the request is refused, naming `status=active`, instead of
  failing inside MongoDB.
- **CSV export failures.** A table CSV export that fails before its first
  chunk answers an error status instead of 200 with an empty body.
- **Unreadable templates.** A template the caller cannot read answers the
  table routes exactly as a template that does not exist.
- **Row cap and workaround documented.** The table route states its
  10,000-row cap per response. It also says how to read documents on a
  template whose namespace the caller cannot read: `template_id` as the
  template's id or `ns:VALUE`, or `template_value=`. The MCP table tools now
  point to `list_documents(template_value=...)` and `get_template_facets`.
  They used to point to `query_by_template`, which asks the same gate.
- **Mixed-namespace bulk creates.** A bulk create whose items interleave
  namespaces writes one batch per namespace, instead of one per run of
  consecutive items. With `continue_on_error=false` it keeps the runs, so it
  still stops at the first failing item.

### Candidates and CSV

- **Candidates that extend.** `validate-candidate` composes a candidate that
  extends another template with its parent, at its pinned version (fields,
  rules, identity). Documents carrying the parent's fields used to fail as
  unknown fields. An identity path into a nested template with no pinned
  version names no referent, as in the template store.
- **Toolkit `export-csv --json`.** Output schema 1.1 reports each file's
  `header` as written, with column names formula-escaped like cells, beside
  the unescaped `columns`.
- **CSV import unescape documented.** Document CSV import and terminology CSV
  import strip one leading apostrophe before `=`, `+`, `-`, `@`, tab, carriage
  return or another apostrophe. That is the escape WIP's exports write, and
  the import descriptions now say so. To import such a value literally,
  double the apostrophe.
