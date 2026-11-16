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
references agree with the other versions' candidates. It then takes one of
them, preferring the target's own order (active first, then the highest
version), and backs out of a choice that leaves another version without a
candidate — so a mapping that exists is found even when the preferred choice
for one version blocks it. Where two target versions differ only in a version
they pin, the one that agrees is chosen. A version that no candidate can take
is reported unplaceable, naming whether its references do not line up with the
other templates or what differs from the target's preferred version; it is
never mapped onto a target version whose number only happens to match its
archived one. Merges whose templates name no other aligned template's version
map exactly as before. The "did not settle" refusal is gone: the mapping
always ends.
