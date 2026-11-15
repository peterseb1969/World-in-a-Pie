---
section: fixes
cases: [1271]
---

### Deleting an already deleted document answers `deleted`

A soft delete of a document with no active version answered `not_found`
(`Document not found`), although the document still exists and `GET` returns
it. It now answers `deleted`, as the delete that retired it did, and records
nothing again: a soft delete is idempotent, as a file delete already was. A
delete that loses a race to a concurrent one answers `deleted` too. An id no
document has stays `not_found`, and PATCH of a soft-deleted document stays
`not_found`.
