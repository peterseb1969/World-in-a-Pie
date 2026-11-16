---
section: fixes
cases: [1413]
---

### A failed compose apply that keeps the old spec puts the old render back

`wip-deploy install`, `add-app`, `redeploy` and the other apply verbs write the new render (`docker-compose.yaml`, the Caddyfile, the key and env files) into the install directory before anything can fail. When the apply then fails without changing the running deployment (a build that fails, a pull that resolves nothing, a container compose never created), the deployer rightly keeps the previous spec. But the install directory still held the new render, and `wip-deploy up`, `rebuild` and `start` deploy from those files: after a refused `add-app`, a later `up` brought up the very app the spec refused, with its route. On such a failure the install directory now gets the previous render back, and files only the new render had are removed. A failure that did change the running deployment keeps the new render, which matches the spec the deployer then saves.
