---
section: fixes
cases: [1241]
---

### `unregister-app` refuses a path-like name; `validate-manifest` applies the install's rules

- `wip-deploy unregister-app ../<install>/docker-compose`, or an absolute path, deleted that `.yaml` file. The name is now checked as `register-app` checks it, and a bad one exits 2.
- `validate-manifest` now refuses an app named like a component or the front door, as an install does.
- `validate-manifest` now accepts an app as a `from_component` or `depends_on` target, as an install does.
- Its pass line names the checks that only an install can make: namespace reservations and OIDC client ids against the other enabled apps.
