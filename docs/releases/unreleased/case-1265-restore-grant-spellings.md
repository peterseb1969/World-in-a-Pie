---
section: fixes
cases: [1265]
---

### A system restore keeps the grant spelling that gives access

An archive from a stack that stored key grants under both `apikey:<name>` and
the bare name collapses the two into one grant. The higher permission won
even when it was orphaned or expired, so a restored key could lose the read
grant it actually held. The spelling that gives access (neither orphaned nor
expired) now wins, and between two that both do or both do not, the higher
permission.
