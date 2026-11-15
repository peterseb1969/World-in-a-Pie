---
section: action-required
cases: [1280]
---

### A template or terminology value containing `/` is refused at the write

The by-value routes address a value as one URL path segment, and `%2F` is decoded back to `/` before routing, so a stored `A/B` could never be read by value: it answered 404, or, when the tail named a subroute, `A/raw` was served as template `A`'s raw document.

- template-store refuses `/` in a template value at create (422, naming the reason). Template values already refused `.`, `*`, `>` and whitespace.
- def-store refuses `/` in a terminology value at create (422) and at a rename (`PUT /terminologies`, per item: `validation_failed`, rule `unaddressable_value`). Imports that create terminologies take the same rule.
- Existing records are not changed and stay readable by canonical id. Term values are not covered by this change.

**Action:** an app, import or script that writes template or terminology values containing `/` must use another character; check stored values before upgrading if you create them programmatically.
