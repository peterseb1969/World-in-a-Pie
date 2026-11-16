---
section: fixes
cases: [1267]
---

### The `wip` namespace records who created it

The Registry's startup and `POST /api/registry/namespaces/initialize-wip`
created the `wip` namespace with no `created_by`. Startup now records
`system:bootstrap` (the actor its default grants already carry) and the route
records the authenticated caller, as every other namespace create does. An
existing `wip` namespace is left as it is.
