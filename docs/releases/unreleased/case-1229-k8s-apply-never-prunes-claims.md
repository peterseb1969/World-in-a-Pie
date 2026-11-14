---
section: fixes
cases: [1229]
packages: [wip-deploy]
---

### No k8s apply prunes a volume claim; every whole-install apply names what it kept

`wip-deploy remove-module garage` on k8s deleted the module's volume along with
its workloads — and a removal that kept the claim kept it only for as long as the
next upgrade let it: `redeploy` (the documented upgrade path), `install` and
`add-module` applied the whole tree with prune and `PersistentVolumeClaim` on the
allowlist, so they took any claim the render no longer named, whether or not the
operator had been told it was kept. Whether the bytes survived was the storage
class's `reclaimPolicy`, not the operator's choice.

PersistentVolumeClaim is now off the prune allowlist for good, for every verb. No
apply deletes a volume: dropping a workload — an app, a module — leaves its claim
and its data alone, and a re-run upgrades cleanly around it. Every whole-install
apply then reads the namespace and prints one line per claim the render no longer
names, with the command that removes it:

```
kept volume claim 'wip-garage-data' (and its data): the install no longer uses it.
Delete it with `kubectl delete pvc wip-garage-data -n wip` once the data is not needed.
```

Workloads still prune as before: a removal deletes the dropped Deployment or
StatefulSet and its Service.

**Action:** storage a dropped app or module leaves behind no longer frees itself.
Delete the claims the apply names, or reclaim everything with
`wip-deploy nuke --name <install> --remove-data`, which deletes the namespace and
the volumes bound to its claims.
