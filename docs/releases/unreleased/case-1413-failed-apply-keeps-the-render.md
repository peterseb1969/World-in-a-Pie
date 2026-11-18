---
section: fixes
cases: [1413]
---

### A failed compose apply that keeps the old spec puts the old render back

`wip-deploy install`, `add-app`, `redeploy` and the other apply verbs write the new render (`docker-compose.yaml`, the Caddyfile, the key and env files) into the install directory before anything can fail. When the apply then fails without changing the running deployment (a build that fails, a pull that resolves nothing, a container compose never created), the deployer rightly keeps the previous spec. But the install directory still held the new render, and `wip-deploy up`, `rebuild` and `start` deploy from those files: after a refused `add-app`, a later `up` brought up the very app the spec refused, with its route. On such a failure the install directory now gets the previous render back, and files only the new render had are removed. A failure that did change the running deployment keeps the new render, which matches the spec the deployer then saves.

The previous render also comes back when the apply ends any other way without saving the new spec: Ctrl-C during a hanging `compose up`, or an unexpected error.

`rotate-key`, `connector add` and `connector set-secret` change the secret store before they render. When the render refused, or the apply changed nothing, the store kept that change: `rotate-key` had deleted the running key's value, and `connector set-secret` left a secret Dex did not run. Every later mutation then stopped on the secret-drift check until the file was restored by hand. These verbs now put the store files back unless the new spec is saved.

On k8s, a failure of `kubectl apply` itself counted as a change made, whatever kubectl did. An unreachable API server therefore saved the spec, and `remove-app` deleted the removed app's key value from the host while the cluster's key file, which the Registry imports, still held the key. A failed apply step now counts as a change only when kubectl reports it created, configured or pruned an object.

On k8s, the install directory now follows the kept spec too. A k8s apply deletes the previous render and writes the new one before `kubectl apply` runs; when kubectl then refused having changed nothing (a quota, an immutable field, an admission webhook), or the apply was interrupted, the deployer kept the previous spec but the directory held the refused render, so a hand-run `kubectl apply -R -f <install dir>` deployed what the spec refused. The previous render, including files only it had, now comes back on every such exit.

`user add --password-hash-file` and `user import` removed the password hashes they had stored on every failure, also on one that changed the running deployment and so saved the spec. The saved spec then named users whose operator-supplied hash was gone, and every later mutation stopped until the hashes were supplied again. The hashes now stay exactly when the spec is saved, and are removed otherwise.

On podman, a failed `compose up` always counted as a change made, so the deployer saved the new spec even when no container had moved. The container listing that decides it asked podman for a field podman does not have (`{{.Id}}`); podman refused the listing, and an unanswered listing counts as a change. The listing now asks for `{{.ID}}`, which podman and docker share, so a refused `up` keeps the previous spec and puts the previous render back, as intended.
