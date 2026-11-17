---
section: fixes
cases: [1413]
---

### A failed compose apply that keeps the old spec puts the old render back

`wip-deploy install`, `add-app`, `redeploy` and the other apply verbs write the new render (`docker-compose.yaml`, the Caddyfile, the key and env files) into the install directory before anything can fail. When the apply then fails without changing the running deployment (a build that fails, a pull that resolves nothing, a container compose never created), the deployer rightly keeps the previous spec. But the install directory still held the new render, and `wip-deploy up`, `rebuild` and `start` deploy from those files: after a refused `add-app`, a later `up` brought up the very app the spec refused, with its route. On such a failure the install directory now gets the previous render back, and files only the new render had are removed. A failure that did change the running deployment keeps the new render, which matches the spec the deployer then saves.

The previous render also comes back when the apply ends any other way without saving the new spec: Ctrl-C during a hanging `compose up`, or an unexpected error.

`rotate-key`, `connector add` and `connector set-secret` change the secret store before they render. When the render refused, or the apply changed nothing, the store kept that change: `rotate-key` had deleted the running key's value, and `connector set-secret` left a secret Dex did not run. Every later mutation then stopped on the secret-drift check until the file was restored by hand. These verbs now put the store files back unless the new spec is saved.

On k8s, a failure of `kubectl apply` itself counted as a change made, whatever kubectl did. An unreachable API server therefore saved the spec, and `remove-app` deleted the removed app's key value from the host while the cluster's key file, which the Registry imports, still held the key. A failed apply step now counts as a change only when kubectl reports it created, configured or pruned an object.
