---
section: fixes
cases: [1275]
---

### A merge with `align_templates` refuses a draft as every other merge does

A merge refuses a template pair where one side is a draft and the other is
not: a draft was never activated, so absorbing it would hide what the two
sides hold. With `align_templates: true`, a version mapped onto an
identical target version skipped that rule, so a draft was absorbed into an
active version without a word. The aligned path now follows the same rule:
a draft refuses the pair, and an inactive-against-active pair is reported
with the target's status kept.
