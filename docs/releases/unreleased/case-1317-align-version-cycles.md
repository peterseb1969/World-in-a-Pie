---
section: fixes
cases: [1317]
---

### `align_templates` maps templates that reference each other's versions

Under `align_templates`, a template version that names another aligned
template's version (a field's `template_ref_version`, an `extends_version`)
could be mapped only once the other template's versions were. When two
templates named each other's versions, or a template named its own version
(a recursive structure), each waited for the other: every version in the
cycle was left unplaceable, and its documents were left out of the merge.
Such templates are legitimate, since drafts that reference each other are
activated together.

Each archive version now starts with every target version its schema matches
with those references left aside, and keeps only the candidates whose
references agree with the other versions' candidates. It then takes its
preferred one (active first, then the highest version), the versions it names
first, and the choice is checked against the target version by the same rule
as before. Where two target versions differ only in a version they pin, the
one that agrees is chosen. Merges whose templates name no other aligned
template's version map exactly as before. The "did not settle" refusal is
gone: the mapping always ends.
