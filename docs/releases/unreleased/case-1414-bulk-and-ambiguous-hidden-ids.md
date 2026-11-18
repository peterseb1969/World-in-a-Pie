---
section: fixes
cases: [1414]
packages: [wip-auth]
---

### A hidden entity named in a bulk item, or by an ambiguous value, answers as an unknown one

Two more ways a caller could learn about an entity in a namespace it cannot read:

- **Bulk doors.** A bulk item naming a hidden entity by a qualified id (`ns:VALUE`) was answered `not_found` with the entity's canonical id, while an id naming nothing was echoed as submitted. This affected 13 bulk doors:
  - template-store: update and delete templates;
  - def-store: update and delete terminologies; update, deprecate, delete and merge terms;
  - document-store: patch and delete documents; patch, delete and reactivate files.

  A `not_found` item now names its ids as submitted, in its id fields and its error text.
- **Ambiguous values.** A value several entities of a hidden namespace bear was answered `ambiguous_reference` (409 on a single door, per item on a bulk door), listing every candidate's canonical id, namespace and the key that carried the value. Candidates in namespaces the caller cannot read are now dropped, and an ambiguity left with none answers as a miss, like a value nothing bears.

Callers who can read the namespace see no change.
