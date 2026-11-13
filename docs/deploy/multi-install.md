# Several WIP installs on one host

One host can run several WIP stacks side by side on the `compose` and `dev`
targets: typically one per backend clone, so each agent (BE-YAC) works against
its own live stack, with its own source mounted and its own data, without
seeing or disturbing the others.

This page is for the operator setting stacks up. Agents get the short version
in their CLAUDE.md (§7, "Your install, and only yours").

---

## What an install owns

Every runtime name an install holds on the host carries the install's name
(`--name`, `^[a-z][a-z0-9-]*$`):

| | name | notes |
|---|---|---|
| containers | `<install>_wip-<svc>` | `default_wip-registry`, `tc02_wip-caddy` |
| network | `<install>_wip-network` | netavark `isolate: strict` |
| volumes | `<install>_wip-<svc>-<storage>` | the compose project prefix |
| dev images | `<image>:dev-<install>` | built from the install's own checkout |
| install dir | `~/.wip-deploy/<install>/` | spec, secrets, rendered compose |
| ports | `--https-port` | one per install |

Inside its network every service is still reachable as `wip-<svc>`
(`http://wip-registry:8001`): that name is a network alias, so a service's
configuration is the same in every install.

**Installs cannot reach each other.** The network is created with
`isolate: strict`: no other network on the host routes to it, by name or by IP.
netavark 1.x (Debian, the Pi) routes between plain bridge networks by default,
so without this any container on the host could reach an install's databases.
A container that must talk to an install (a probe, an app run by hand) joins
that install's network: `podman run --network <install>_wip-network …`.

---

## Setting up N stacks (one per clone)

Example: three backend clones on one Mac, `~/Development/WIP-TC01..03`.

```bash
for n in 1 2 3; do
  cd ~/Development/WIP-TC0$n
  wip-deploy install --target dev --name tc0$n \
    --hostname wip-tc0$n.localhost \
    --https-port 844$((n+2)) \
    --repo-root ~/Development/WIP-TC0$n
  WIP_INSTALL=tc0$n scripts/setup-backend-agent.sh   # binds the clone
done
```

- **One hostname per install**, not only one port. Browsers keep cookies per
  host, not per port, so two stacks under one hostname overwrite each other's
  login sessions. Names under `.localhost` resolve to the machine without DNS.
  Every install's edge also answers `localhost:<its port>`, which is what
  `.mcp.json` uses.
- **`--repo-root` is the clone.** A dev install bind-mounts its backend source
  from there; the install is stamped with it, and edits in that clone reload in
  that stack only.
- **Ports:** preflight refuses a port another install (or anything else) holds.
  The loop gives `tc01` the default ports 8443/8080: on a host where a
  `default` install still runs, remove or move it first, or start the
  numbering one higher.
- **Binding the clone:** `setup-backend-agent.sh` records the install in the
  clone's `.claude/wip-install` and builds `.mcp.json` from that install's key
  and HTTPS port. On a host with several installs and no binding it refuses
  and lists them, rather than guess. `WIP_INSTALL=<name>` re-binds.

### Sizing

One dev stack with four apps uses about 3 GB of memory (measured on the Mac,
2026-11-09). Three stacks fit comfortably in a 24 GiB podman VM; leave room for
the test containers `wip-test.sh` starts per clone.

---

## Working on one install among several

- **Always name the install:** `wip-deploy <verb> --name <install>`. A bare verb
  targets `default`. A verb run from clone A against clone B's install re-stamps
  B to A's checkout and mounts A's source into B.
- **Containers by their install's name:** `podman restart tc02_wip-registry`,
  `podman logs tc02_wip-def-store`, `podman exec -it tc02_wip-mongodb …`.
- **Status of every install:** `wip-deploy status --name <install>` per install,
  or `/wip-status`, which lists each install on the host and marks the clone's.
- **Remove one:** `wip-deploy nuke --name <install>` (add `--remove-data` and
  `--remove-secrets` to drop its volumes and secrets). `nuke --purge-all`
  removes every install on the host.

---

## Moving an existing install to per-install names (the cutover)

Installs created before per-install naming used fixed names (`wip-registry`,
`wip-network`) on every install. The first `wip-deploy redeploy --name <install>`
on the new deployer moves the install over:

1. it removes this install's containers that the new render does not name
   (found by the install's compose project label, so no other install's
   containers are touched): the old fixed-name ones;
2. it brings up `<install>_wip-<svc>` on `<install>_wip-network`;
3. the volumes are kept: they were already named per install.

Pair the redeploy with the matching `--tag` as for any redeploy. The old
`wip-network` stays while anything else is attached to it; a container you had
joined to it no longer reaches the install (join `<install>_wip-network`
instead), and `nuke --purge-all` removes it once unused.

---

## Limits

- Two installs side by side were run on wip-pi (podman 5.4, podman-compose
  1.3, netavark 1.14). On macOS (podman 6.1, podman-compose 1.6, netavark
  2.1) only the network behaviour was checked, with throwaway containers:
  per-project networks, the `wip-<svc>` alias, `isolate: strict`, a
  published port. No two-install deployment has run on a Mac yet.
- Docker (the `docker compose` fallback) is not checked; its cross-network
  isolation works differently and may ignore `isolate`.
- App projects (APP-YACs) bind to an install through their own `.env`
  (`create-app-project.sh`); a per-app choice of install among several is not
  part of this.
- k8s installs are scoped by their namespace and are unaffected.
