---
section: fixes
cases: [1264]
---

### A malformed groups claim is no groups, not a 500

A token whose `groups` claim is not a list of names (a number, an object)
authenticates with no groups instead of failing every request of that user
with a 500; a list keeps only its names. The deployer guide now says the key
file owns the grants of every key the deployer renders (`grants: {}` when
none are declared), so a grant set through the API on such a key is refused.
