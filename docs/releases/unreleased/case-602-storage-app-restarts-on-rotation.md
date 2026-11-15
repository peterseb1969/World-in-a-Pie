---
section: fixes
cases: [602]
packages: [wip-deploy]
---

### An app with a volume restarts when its key is rotated

The deployer restarts a service when a secret it reads at start changes, by
stamping a hash of those secrets on its workload, and `rotate-key` names the
services it restarts. Both skipped every service that declares storage. That
is right for a database, whose password lives in its volume, but AuthorAssist
now has a small `/data` volume. `wip-deploy rotate-key app-wip-aa` therefore
neither restarted AA nor named it, and AA kept the old key, which the install
refuses as soon as the rotation applies. AA lost access to WIP until someone
restarted it by hand.

An app is now hashed and restarted like any other app, with or without
storage. Its volume is kept across the restart. Storage-bearing platform
components (MongoDB, PostgreSQL, NATS, the object store) are unchanged.

**Action:** none. The first `redeploy` after the upgrade restarts AA once,
because its workload gains the hash.
