---
section: fixes
cases: [1333]
---

### Registry: deleting a namespace another deletion just removed answers 404, not "deletion in progress"

- **The race:** two deletes of one namespace both read it while it is unlocked, and the first finishes before the second takes the lock.
- **Before:** the second delete answered 409 "already locked (deletion in progress)" for a namespace that no longer exists.
- **Now:** it answers 404 "Namespace not found", as a delete of a missing namespace always has. A namespace that is still locked keeps answering 409.
- **Message:** every 409 for a locked namespace now uses the wording the other namespace writes use: "Namespace '<prefix>' is locked: its deletion is in progress". This covers the dry run's `refusals[].message` too.
