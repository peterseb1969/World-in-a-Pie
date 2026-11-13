---
section: fixes
cases: [1264]
---

### A malformed groups claim is no groups, not a 500

A token whose `groups` claim is not a list of names (a number, an object)
authenticates with no groups instead of failing every request of that user
with a 500; a list keeps only its names.
