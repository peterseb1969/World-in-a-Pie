---
section: action-required
cases: [1283]
---

### A namespace name cannot be created again while its deletion has steps left

A namespace deletion whose later steps (stored files, reporting rows) were
still to run, or which stopped on a failed step, did not keep the name: a
`PUT /api/registry/namespaces/{prefix}` or `POST /api/registry/namespaces`
created a new namespace under it, and the resumed deletion then kept the
new namespace's data and with it the old namespace's stored files and
reporting rows, for good. Both creates (dry runs included) now answer 409
while a deletion journal for the prefix is `in_progress` or `failed`. A
deletion that `completed_with_warnings` ran every step and does not hold
the name.

Also, a runtime reservation owner's upsert of a namespace locked for
deletion answered 200 (writing nothing); it now gets 409 like every other
caller.

**Action:** a bootstrap that creates a namespace refused this way resumes
the deletion first (`POST /api/registry/namespaces/{prefix}/resume-delete`),
then creates the namespace again.
