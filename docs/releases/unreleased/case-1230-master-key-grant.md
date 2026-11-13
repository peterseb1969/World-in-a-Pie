---
section: action-required
cases: [1230, 1239]
---

### The master key is the operator's to grant: an app declaring `privileged_key` needs `--privileged-app`

`privileged_key: true` lets an app read the install's master key, which is admin on every namespace. It was a line in the app's manifest and nothing else, so the app's author granted it, not the operator. The manifest now only asks: the install grants it per app, and refuses an app that asks without the grant (and a grant to an app that does not ask).

**Action:** an install running React Console (the one shipped app that declares it) is refused on its next apply until you grant it once:

```bash
wip-deploy add-app react-console --privileged --name <install>
```

A new install passes `--privileged-app react-console` beside `--app react-console`.

Also:

- **Component manifests** may no longer read another owner's secret: an app's key, another service's NATS or MongoDB password, the canary's or the app edge's key, MongoDB's root password outside MongoDB. The shared platform secrets (the master key, Postgres, Garage, Dex client secrets) stay allowed, and so do operator-supplied ones.
- **An app may read its own derived key** (`app-<name>-api-key`) under a name of its own; it was refused as another owner's.
- **A component's operator-supplied secret** no longer counts as the component's own, so an app naming the same operator secret (such as `anthropic-api-key`) is not refused.
- **`validate-manifest`** refuses an operator-supplied name that the deployer or another app or component in the repo already uses, as the install does.
