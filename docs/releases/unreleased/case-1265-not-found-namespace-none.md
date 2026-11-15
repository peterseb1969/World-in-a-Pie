---
section: fixes
cases: [1265]
packages: [wip-auth]
---

### A not-found message no longer names a namespace called "None"

An identifier that named nothing, read with no `namespace` and a key that
implies none (one scoped to several namespaces, or an admin key), answered
"Could not resolve … in namespace 'None'". It now names no namespace, as the
term not-found message already did.
