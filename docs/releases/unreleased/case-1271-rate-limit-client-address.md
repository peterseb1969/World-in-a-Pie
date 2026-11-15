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
trusted, and the header a service receives holds one entry its proxy wrote.

The app edge's authentication check (`forward_auth` to auth-gateway's
`/auth/verify`, for every protected attached app) used to append wip-caddy's
address to that header. Keyed on the rightmost entry, every user of every
protected app would share wip-caddy's one bucket off k8s. The edge now hands
the gateway the header as wip-caddy wrote it, so each user keeps their own.

A `WIP_TRUSTED_PROXIES` entry that is not an address or a CIDR range stops the
service at start, whether or not the rate limit is on.

**Action:** roll the services, auth-gateway and app-edge together, so that no
service keys on the rightmost entry while the edge still appends wip-caddy. On a
k8s install, re-render (`wip-deploy redeploy --name <install>`) so every service
receives `WIP_TRUSTED_PROXIES`; until then a service keys on the rightmost entry,
the controller pod, and every client behind one controller pod shares its bucket.
