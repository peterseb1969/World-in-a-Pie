---
section: fixes
cases: [1409]
---

### An edge left out by drop_unresolved_edges no longer decides an aligned version

A merge with `align_templates=true` and `drop_unresolved_edges=true` decided the unresolved-edge drops after the alignment. An edge the merge then left out still counted as arriving. If it filled a field the aligned target version lacks, its archive version was unplaced, and every other edge pinned to that version was left out with it. The drops are now decided before the alignment: a dropped edge pins no version, fills no field and counts as neither arriving nor held.
