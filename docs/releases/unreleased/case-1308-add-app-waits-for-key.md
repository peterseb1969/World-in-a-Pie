---
section: action-required
cases: [1308]
packages: [wip-deploy]
---

### add-app waits until the Registry accepts the app's key, and exits 1 if it is still refused

`add-app` no longer reports an app ready before its key exists at the Registry. It prints how long a declared key takes to arrive (about 40 s on compose/dev, up to three minutes on k8s, where the kubelet refreshes the mounted key file first), then waits for the Registry to accept `app-<name>`; if the key is still refused it exits 1, naming the key — the app is applied and saved but cannot reach WIP; restart it once accepted. A key it cannot probe (no trust anchor, nothing readable in the store) is reported "check not run" and does not fail. Privileged apps (master key) and tag rolls declare no key and are not probed. `rotate-key`'s check is unchanged. **Action:** k8s operators can stop waiting by hand after `add-app`; apps that give up on a startup 401 still need their own retry (filed separately).

**Action:** a script that runs `wip-deploy add-app` and treats exit 0 as "ready" now waits up to the key's arrival time; a key still refused exits 1 (the app is applied and saved) — restart the app once the key is accepted.
