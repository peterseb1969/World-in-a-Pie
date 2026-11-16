---
section: fixes
cases: [1275]
---

### An edge type whose inheritance cannot be composed still lists its edges

`GET /documents/{id}/relationships` and `/traverse` ask template-store which
templates are edge types. When template-store refused to compose a
template's inheritance (409 `inheritance_unresolved`: a missing parent, a
cycle, a chain too deep), that edge type dropped out. A `types` filter naming
it answered an empty page, and an unfiltered listing lost its edges. `usage`
is stored on the template itself, so its unresolved row now answers instead.

An edge type named in a `template` / `types` filter whose documents are
there but which template-store does not hold at all, not even unresolved,
now answers 404 instead of an empty page, which read as "no edges of this
type".
