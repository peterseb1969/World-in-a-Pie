---
section: fixes
cases: [1229]
packages: [wip-deploy]
---

### Apply failures name what failed; hooks have deadlines; a refusing Registry stops the apply

- A k8s rollout that does not complete names each workload in kubectl's words
  (`rollout did not complete: deployment/wip-registry: …`). A workload that fails
  no longer ends the wait: every workload is asked, and those the wait's budget
  never reached are listed as such.
- Post-install hooks (compose and k8s) and Garage provisioning calls are bounded
  by `spec.apply.timeout_seconds` (`install --wait-timeout`), each from its own
  start. A hook that runs out fails the apply, naming the hook; the deployer stops
  waiting, but the hook may still be running in its container. Garage's readiness
  wait keeps its own 60 s window, probing every few seconds with a short bound per
  probe.
- A 401, 403 or 5xx from the Registry when the apply checks runtime reservations
  or app attachments stops the apply with the Registry's answer, instead of being
  noted and skipped. A 404 (an older Registry) and an unreachable Registry (a
  first install) are still notes.

**Action:** a lowered `--wait-timeout` now also limits hooks and Garage
provisioning; raise it if they need longer.
