---
section: fixes
cases: [1265]
---

### The app edge refuses an upstream on port 0

The Registry refuses an attachment upstream naming port 0, but the app edge
read a stored one as "no port" and served the scheme's default port instead.
It now skips such a record, as it skips any upstream it cannot serve, and
names the reason.
