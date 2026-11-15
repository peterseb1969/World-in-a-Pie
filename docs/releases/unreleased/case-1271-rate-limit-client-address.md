---
section: fixes
cases: [1271]
packages: [wip-auth, wip-deploy]
---

### The rate limit keys on the address the proxies saw, not one a client sent

Behind the k8s ingress, wip-caddy keeps the controller's `X-Forwarded-For` and
appends to it, so the header's first entry is whatever the client put there.
The limit keyed on that entry, and a client choosing a fresh address per
request was never limited. Each service now takes the rightmost entry outside
the ranges wip-caddy trusts (the cluster's pod ranges), handed to it as
`WIP_TRUSTED_PROXIES`, and the leftmost one when every entry is trusted (an
app calling through wip-router is one client, as before). Off k8s nothing is
trusted, the header holds one entry its proxy wrote, and nothing changes.

A `WIP_TRUSTED_PROXIES` entry that is not an address or a CIDR range stops the
service at start.

**Action:** on a k8s install, re-render (`wip-deploy redeploy --name <install>`)
so every service receives `WIP_TRUSTED_PROXIES`; until then a service keys on
the rightmost entry, the controller pod, and every client behind one
controller pod shares its bucket.
