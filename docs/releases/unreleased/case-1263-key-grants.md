---
section: fixes
cases: [1263]
packages: [wip-deploy]
---

### A spec-declared API key that declares no grants keeps the grants the API gives it

`wip-deploy` rendered `grants: {}` for every key in the install's key file,
so the Registry treated every spec-declared key's grants as file-owned. A
grant set through the API was refused (`managed_by_deployer`), or removed at
the next import. A spec key now declares grants only when it says so:

- **`grants` given** (`{}` included): unchanged. The file owns the key's
  grants. It reads each of its `namespaces` and holds the declared levels.
- **`grants` left out**: the key is rendered without `grants`, and its
  grants stay under the API. The file grants it nothing, not even read on
  its `namespaces`. Give it access through the API, or declare `grants`
  (`{}` for read on each of `namespaces`).

Saved install specs keep what they say. Every key a spec saved before this
change carries `grants`, so a redeploy changes nothing for it. The keys the
deployer derives (app keys, the canary, the app edge) always declare their
grants. The warning a service logs for a key whose own config declares
grants no longer says the grants go unread. In the Registry of an install
rendered before the key file moved, that same file is the one it imports.
