# Install-Path Drift: `setup.sh` vs `quick-install.sh`

**Status:** Documented; reconciliation designed in `wip-deploy-v2.md`
**Date:** 2026-04-16

## Summary

WIP currently has two independent installation pipelines that have drifted
materially apart. A change tested on the developer path does not exercise the
end-user path, and vice versa. This document records the shape of the drift
so a future reconciliation can be scoped.

## The two paths

### Path A — `scripts/setup.sh` (developer / local)

For someone with a repo checkout who wants to run WIP locally (dev or prod
variant) from source or from a pre-built image registry.

- 2465 lines.
- Modular: combines `docker-compose/base.yml` + `docker-compose/modules/*.yml`
  + `docker-compose/platforms/*.yml` + per-component
  `components/<svc>/docker-compose.yml`.
- Presets (`headless`, `core`, `standard`, `analytics`, `full`) and composable
  modules (`console`, `oidc`, `nats`, `reporting`, `files`, `ingest`,
  `dev-tools`).
- Dev vs prod variant; dev mode writes `docker-compose.override.yml` files
  that mount source for live reload.
- Builds images from source by default; `--registry` switches to pre-built
  images by generating `docker-compose.registry.yml` per component.
- Starts services one by one, each as its own compose project under its
  component directory.
- Generates `.env`, Dex config, Caddyfile, and console nginx.conf inline.
- Seeds namespaces and default group grants via Registry API after start.
- Has `--remote-core` (console-only, backend elsewhere), `--clean` (NATS
  stream reset), `--save-config`/`--config`, platform autodetect (pi4,
  windows).

### Path B — `scripts/quick-install.sh` + `scripts/setup-wip.sh` (turnkey)

For someone running `curl … | bash` on a fresh machine (typically a Pi or a
demo VM) who wants WIP up from pre-built images.

- `quick-install.sh` (413 lines): fetches an "install kit" (compose files +
  templates + `setup-wip.sh` + app chunks) into `~/wip-demo`, runs
  `setup-wip.sh`, then `start-wip.sh -y`.
- `setup-wip.sh` (519 lines): generates configs into the install dir.
- Compose is **monolithic**: `docker-compose.production.yml` (344 lines,
  generated out-of-band by `build-release.sh`) pins all pre-built image
  references and has no `build:` keys.
- App chunks (`docker-compose.app.<name>.yml`) are first-class: they carry
  `wip.app.*` labels that `setup-wip.sh` scans to generate Caddy routes and
  Dex clients.
- Aggressive cleanup of orphan containers / pods / volumes / cached images
  before install.
- No preset / module / variant machinery. One shape only.

## Where they diverge

### 1. Auth architecture — the most load-bearing split

| | Path A (`setup.sh`) | Path B (`quick-install` / `setup-wip`) |
|---|---|---|
| Gateway | None | `wip-auth-gateway` service on :4180 |
| Caddy pattern | `reverse_proxy wip-<svc>:<port>` direct | `forward_auth wip-auth-gateway:4180` then `reverse_proxy` |
| Headers injected | n/a | `X-WIP-User`, `X-WIP-Groups`, `X-API-Key` |
| Extra secrets | none | `WIP_GATEWAY_SECRET`, `WIP_GATEWAY_SESSION_SECRET` |
| Dex clients | `wip-console` only | `wip-console` + `wip-gateway` + one per app |

Theme 7 (commits `ef24f3e`, `be2bc4f`, `01e35e0`, `5034d0d` — April 2026)
introduced the auth gateway on Path B only. Path A's Caddyfile generator has
no awareness of it.

### 2. Compose topology

- **Path A:** layered overlays. Each service is its own compose project
  (`podman-compose up` runs N+1 times, once per component plus
  infrastructure). Dev override files mount source.
- **Path B:** one compose project, one file, one `up`. Images are tagged
  references; no `build:` anywhere.

### 3. Source of compose files

- **Path A:** reads checked-in files from the repo working tree.
- **Path B:** fetches from GitHub raw (`develop` branch by default) or from
  a local `--source` directory. Install kit is a curated subset of the repo.

### 4. App model

- **Path A:** has no concept of "apps". Anything beyond core is a *module*
  (reporting, files, ingest, dev-tools).
- **Path B:** first-class app chunks with labels
  (`wip.app.name`, `wip.app.route`, `wip.app.port`,
  `wip.app.oidc.client_id`, `wip.app.oidc.client_secret`). The `setup-wip.sh`
  scanner uses these labels to emit Caddy routes and Dex clients
  dynamically. See also `docs/design/pluggable-apps.md` and
  `docs/design/app-gateway.md`.

### 5. Password hashing

- **Path A:** `htpasswd -nbBC 10` (with a hardcoded fallback set of known
  hashes when htpasswd is missing, dev only).
- **Path B:** `python3 -c "import bcrypt; ..."` (requires
  `python3-bcrypt`).

Two different dependency expectations for the same artefact (a bcrypt hash
in Dex config).

### 6. Config generation

| File | Path A source | Path B source |
|---|---|---|
| `.env` | Inline heredoc in `setup.sh` with `WIP_*` / `VITE_*` values | `sed`-substituted from `.env.production.example`, passwords appended |
| Caddyfile | Inline heredoc | Template at `config/production/Caddyfile.template` + dynamic app-route injection |
| Dex `config.yaml` | Inline heredoc | Template at `config/production/dex-config.template` + dynamic client injection |
| Console nginx.conf | Inline heredoc (proxies to each wip-* service) | Not generated — gateway owns routing |

### 7. Post-install bootstrap

- **Path A:** calls Registry's `/namespaces/initialize-wip`, then seeds
  `wip-editors` / `wip-viewers` group grants on the `wip` namespace.
- **Path B:** neither. Services are expected to handle their own defaults.

### 8. Platform handling

- **Path A:** detects Raspberry Pi 4 and pins MongoDB 4.4; has a Windows
  platform overlay.
- **Path B:** hardcodes `mongo:7`.

### 9. Cleanup

- **Path A:** none. Trusts the user / expects a clean tree.
- **Path B:** removes orphan containers, pods, volumes, cached WIP images
  before install — required for repeat demo installs to actually refresh.

### 10. Console build

- **Path A:** builds console from `ui/wip-console/` (or pulls
  `wip-console:<tag>`).
- **Path B:** pulls `wip-console` image. Assumes OIDC authority / API base
  are either baked at build time or picked up via env.

## Practical consequences

1. **Gateway auth is untested on the dev path.** Any regression in Caddy
   `forward_auth`, gateway session handling, or header injection is not
   caught locally with `setup.sh`. It is only exercised on a full install
   via `quick-install.sh`.

2. **`setup.sh` cannot install a current-model WIP.** If an app relies on
   `X-WIP-User` / `X-WIP-Groups` headers from the gateway, it won't work
   under `setup.sh` — the headers are never injected.

3. **Config templates are the source of truth for one path only.**
   Changes to `config/production/Caddyfile.template` or
   `config/production/dex-config.template` have no effect on `setup.sh`.
   Changes to `setup.sh`'s inline generators have no effect on the turnkey
   install.

4. **Two dependency surfaces.** `htpasswd` (apache2-utils / httpd) vs
   `python3-bcrypt` — different packages on different distros, neither one
   universal.

5. **Two cleanup policies.** Repeat installs on `setup.sh` can silently
   reuse stale MongoDB/Postgres volumes whose passwords no longer match
   regenerated secrets.

6. **Two app models.** The app-chunk label convention
   (`wip.app.*`) doesn't exist on Path A, so a contributor developing a
   new app locally has no way to wire it in without inventing
   setup.sh-specific glue.

## Reconciliation

See `wip-deploy-v2.md` for the approved v2 design: a declarative
deployment system (Pydantic spec + component manifests + shared config
generators + per-target renderers) that replaces all three paths with a
single source of truth.

## References

- `scripts/setup.sh`
- `scripts/quick-install.sh`
- `scripts/setup-wip.sh`
- `docker-compose.production.yml` (generated by `scripts/build-release.sh`)
- `docker-compose/base.yml` + `docker-compose/modules/*.yml`
- `config/production/Caddyfile.template`
- `config/production/dex-config.template`
- `docs/design/app-gateway.md` — gateway design
- `docs/design/pluggable-apps.md` — app chunk model
- `docs/design/distributed-deployment.md` — multi-host scenarios (Path A only)
- Recent commits introducing Theme 7 gateway auth: `ef24f3e`, `be2bc4f`,
  `01e35e0`, `5034d0d`.
