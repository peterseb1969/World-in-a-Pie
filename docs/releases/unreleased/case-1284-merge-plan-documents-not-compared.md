---
section: fixes
cases: [1284]
---

### A merge's dry run no longer reports every matched document as a clash

The merge plan reads the target's documents without their content, so it
cannot tell an identical document from a changed one. It used to count every
document the target already holds as a `clash` (and none as `unchanged`), so
the dry run, the operator's only preview of a merge, claimed differences it
never looked for. The document counts now report those as `not_compared`
(`result.namespaces.<ns>.entities.documents`, and the dry run's log line),
in place of `unchanged` and `clash`. Other entity types keep `unchanged` and
`clash`. A real run still compares each document when it writes it and leaves
an identical copy alone, as before.
