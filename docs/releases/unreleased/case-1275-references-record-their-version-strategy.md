---
section: fixes
cases: [1275]
---

### A stored document's `references[].version_strategy` states the strategy applied

A reference field that declares no `version_strategy` resolves the latest active
version of its target — but the resolved reference recorded on the document came
out `null`, because the stored template row carries the key with a null value and
the default never applied. The record contradicted the documented shape, which
shows `"latest"`, and left a reader to guess which of the two meanings `null` had
for that document.

`references[].version_strategy` now reads `"latest"` for a field that declared
none, and `"pinned"` where it declared pinned. The template field's own
`version_strategy` is unchanged: a null there still means the field declares no
strategy, and `latest` is the default.

**Action:** none. Documents stored before this answer `null`, which keeps
meaning latest; re-writing a document refreshes its record.
