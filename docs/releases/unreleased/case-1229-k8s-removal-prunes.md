---
section: fixes
cases: [1229]
packages: [wip-deploy]
---

### On k8s, `remove-app` deletes the app; config-only verbs stop applying the whole install

`remove-app` on a k8s install printed "Removed app" while the app kept running:
its apply was narrowed to the front door, and a narrowed apply never prunes. It
now applies the whole install with prune and says so beforehand. That deletes the
app's Deployment (or StatefulSet) and Service, and waits for every workload of the
install. A volume claim the app declared is never pruned by a removal: it is kept
with its data, and the apply names each claim it kept with the `kubectl delete pvc`
command to remove it. Its key leaves the served key file; the shared `wip-secrets`
Secret may keep the old value until a later apply replaces it.

The config-only verbs (`group-map`, `app-namespace`, and the user verbs when no
Dex is active) used to fall back to that same whole-install apply with prune on
k8s whenever nothing else needed restarting. They now apply the shared
configuration and the front door only, as on compose.
