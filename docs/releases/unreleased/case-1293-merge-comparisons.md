---
section: fixes
cases: [1293]
---

### A merge compares definitions by what they mean, and judges unique_names as def-store does

A merge into a namespace that already holds data compared some definitions
by how they were stored rather than by what they mean, and refused merges
that agreed:

- A template's id is mapped by its value, so a template whose versions on the
  two sides do not overlap no longer leaves every reference to it unmapped.
  Such a template is refused once, naming `align_templates=true`, which maps
  the archive's versions onto the target's by schema — under `add_missing`
  too, where it was previously imported as new versions.
- A term's names compare as a set, one per (value, type, language); a
  difference is reported and the target's names stand. Under
  `extend_terminologies` the target's term also gains the names only the
  archive has.
- An edge type's endpoint lists and a field's `target_templates` compare as
  the templates they name; the stored `source_ref`/`target_ref` constraint and
  a term's `terminology_value` are no longer compared.
- `unique_names` is judged by its effect: the merge refuses only where the
  terms and names it adds would give one name to two active terms of a
  terminology that holds it, matched and reported as def-store would.

A merge whose document mappings never settle is now refused ("did not
settle") as soon as the mappings repeat, before anything is written.
