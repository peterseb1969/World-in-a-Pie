---
section: fixes
cases: [1310]
packages: [wip-client]
---

### Namespace validation reports a retired template pin only on a document's current version

`validate_namespace`, and the validation every restore starts, checked every document *version* for a pin to a template version that is no longer active. A superseded version keeps the pin it was written with, so any namespace whose templates had ever gained a version reported thousands of findings after each restore — 7,294 in `kb` — and read `warning` though nothing was broken, training the reader to ignore it. Now only a document's **current** version on a retired template version is a finding: it cannot be updated as it stands. History keeps its pins, so an evolved namespace validates clean. A reference to a template that is missing entirely is still reported on every version, current or not, and `inactive_template_refs` now counts the current-version findings.
