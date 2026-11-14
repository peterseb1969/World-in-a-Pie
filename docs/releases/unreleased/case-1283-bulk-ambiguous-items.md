---
section: fixes
cases: [1283]
packages: [wip-auth]
---

### A bulk write answers an identifier several entities bear as ambiguous

A bulk write that named an entity by a value several entities bear (a term
relation's endpoint or a deprecation's replacement given with its
terminology, a template, terminology, document or file value) left it
unresolved, and the item was answered as not found. Each such item is now
answered as an error with `error_code: ambiguous_reference`, naming the
field and the candidates in `details` (`field`, `identifier`, `candidates`,
`total`, `truncated`), and is not written; the other items are written as
before, and the response is still HTTP 200. A document create with
`continue_on_error=false` stops at such an item as at any failing one.
This covers every bulk door that resolves identifiers: term relations
(create, delete), terms (create, update, deprecate, delete, merge),
terminologies and templates (update, delete), documents (create, patch,
delete) and files (update, delete, reactivate).
