---
section: fixes
cases: [1414]
packages: [wip-auth]
---

### A refused bulk item names every identifier as submitted

A bulk item can name an entity in a namespace the caller cannot read through a pointer inside it rather than its own id. Examples are a term's `parent_term_id` or `replaced_by_term_id`, a relation's `source_term_id` or `target_term_id`, and a new document's `template_id`. Such an item was refused like one naming nothing, except that the refusal carried the hidden entity's canonical id: in the error text, and for a document's template also in `details.errors[].message`.

Every error item of these bulk doors now names its identifiers as the caller submitted them, whatever its error code and at any depth of its answer. An item that was written keeps the canonical id.
