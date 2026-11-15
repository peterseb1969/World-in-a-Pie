---
section: deployment
cases: [1265]
packages: [wip-deploy]
---

### wip-deploy warns when a k8s install's clients would share one rate limit

Without the cluster's pod ranges (`--cluster-cidr pods=CIDR`), wip-caddy does
not keep the ingress controller's `X-Forwarded-For`, and the services'
per-client rate limit sees every request as coming from the controller: all
clients share one budget. `install` and `redeploy` on k8s now warn when no
pod ranges are given, whether or not the app edge is on.

**Action:** k8s installs pass their pod ranges, e.g. `wip-deploy redeploy
--name <install> --cluster-cidr pods=10.42.0.0/16`.
