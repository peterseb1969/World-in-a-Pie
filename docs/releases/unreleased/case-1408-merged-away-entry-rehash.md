---
section: fixes
cases: [1408]
---

### A merged-away registry entry stays merged away when a merge or restore rehashes its document

The Registry clears the primary key hash of an entry it merges away: the entry claims no key, because its key now belongs to the entry it was merged into. A merge or plain restore that rehashes a document's identity (a term or reference in it mapped onto this install's copy) stamped a hash on that entry again. The dead entry then claimed the survivor's key, and the operator saw "composite key(s) were already claimed by other entries". The rehash now updates the entry's recorded key and leaves its hash empty, as the namespace and id rewrites already did.
