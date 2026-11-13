---
section: fixes
cases: [1229]
packages: [wip-deploy]
---

### Re-running `install` on compose serves a new app's route

A full compose apply (re-running `install` with an extra app, `redeploy`) never
reloaded the Caddy proxies, and `compose up` does not recreate them for a routing
change, so the new app's container ran while its route was not served until the
proxies were restarted by hand. Every compose apply, and every scoped dev apply,
now reloads each proxy that is not yet serving its rendered Caddyfile.

The deployer records which Caddyfile each proxy took (`.proxy-config-delivered.json`
in the install directory), so a config that reached disk but not the proxy — the
apply stopped before the reload, or the reload failed — is reloaded by the next
apply instead of being missed. A proxy container the apply itself started is not
reloaded: it already runs the new file.

A reload that fails — most often a Caddyfile Caddy refuses — is no longer followed
by a restart of the proxy. Caddy keeps serving its old config, where the restart
booted it on the refused file and left the front door down. The apply prints a
warning naming the proxy and goes on; the next apply tries the reload again.

**Action:** none. The first apply after upgrading reloads each proxy once.
