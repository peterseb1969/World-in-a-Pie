---
section: action-required
cases: [1428]
---

### Command healthchecks no longer fill the container host with zombie processes

**Affected:** compose and dev installs rendered from development builds after v2.1.0, since command healthchecks were bounded with `timeout`. Installs rendered by v2.1.0 or earlier do not leak. The leaking services are the router and nats. k8s installs are not affected: their probes run the command bare.

**What went wrong:** a command healthcheck runs under `timeout`. In the router and nats images, `timeout` is busybox's, which leaves its watchdog process to the container's PID 1 when the check ends. caddy and nats-server, which run as PID 1 there, never reap it. Every check run, once every 10 seconds per container, left a zombie behind. On a podman machine those zombies count against the VM's task limit, which three installs filled in about 30–50 hours of uptime. The VM then refused every new process: healthchecks stopped running, podman crashed, and `podman machine stop` could not stop the containers.

**The fix:** every service whose healthcheck is a command now runs under podman's init (`init: true`, i.e. `podman run --init`), which reaps those processes.

**Action:** redeploy each compose or dev install, so its router and nats are recreated under the init: `wip-deploy redeploy --name <install>`. mongodb and postgres are recreated too, since their checks are commands as well. Until then, `podman restart <install>_wip-router <install>_wip-nats` clears the zombies already there, and buys about a day per restart.
