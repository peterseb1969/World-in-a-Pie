---
section: action-required
cases: [1308]
packages: [wip-deploy]
---

### add-app waits until the Registry accepts the app's key, and exits 1 if it is not accepted

`add-app` no longer reports an app ready before its key exists at the Registry. It prints how long a declared key takes to arrive (about 40 s on compose/dev, up to three minutes on k8s, where the kubelet refreshes the mounted key file first), then waits for the Registry to accept `app-<name>`; if the key is not accepted by the end of the window — the Registry still refuses it, or the install did not answer — it exits 1, naming the key and the last answer: the app is applied and saved but cannot reach WIP; restart it once accepted. A key it cannot ask about (no trust anchor, a certificate that does not verify, nothing readable in the store) is reported "check not run" and does not fail; an install that does not answer does. Privileged apps (master key) and tag rolls declare no key and are not probed. `rotate-key`'s check is unchanged. **Action:** k8s operators can stop waiting by hand after `add-app`; apps that give up on a startup 401 still need their own retry (filed separately).

**Action:** a script that runs `wip-deploy add-app` and treats exit 0 as "ready" now waits up to the key's arrival time; a key not accepted in the window exits 1 (the app is applied and saved): a last answer of 401 means restart the app once the key is accepted; no response or a 5xx means check that the install is reachable first.
