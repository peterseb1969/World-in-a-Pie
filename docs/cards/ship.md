<!-- card-sources: what this card condenses; checked by components/mcp-server/tests/test_card_drift.py
docs/wip-deployable-app-contract.md sha256:d4c260180420c3ac reviewed:9f8f08fb
deployer/src/wip_deploy/spec/app.py sha256:6a5abab38f41f5e9 reviewed:a38b9c84
-->
# WIP ship card — making the app deployable

Before features: healthy under `wip-deploy` on the first try, SPA at `/apps/<name>/`, no manual env patching.

## Source repo (the dir holding `package.json`)
- [ ] `Dockerfile.dev` here; runs `npm run dev`; alpine + curl; NO baked `node_modules` (the deployer mounts a named volume).
- [ ] `package.json` has a `dev` script.
- [ ] `docker-entrypoint-dev.sh`: hash-gated `npm ci` at container start.
- [ ] `vite.config.ts`: `server.host: '0.0.0.0'`; dev `proxy` keys prefixed with the base path and pointing at THIS app's Express port; `base` = `VITE_BASE_PATH || APP_BASE_PATH || '/'`.
- [ ] Server: every route on an `express.Router()` mounted under `APP_BASE_PATH`; `app.set('trust proxy', 1)`.
- [ ] `requireAuth()` passes through when `OIDC_ISSUER` is unset (`if (!process.env.OIDC_ISSUER) return next()`).
- [ ] `/api/me` reads gateway headers `X-WIP-User` / `X-WIP-Groups` first, then OIDC session, then anonymous.
- [ ] `NODE_ENV=production`: serve `dist/` with SPA fallback; unmatched `/api`, `/auth`, `/wip`, `/server-api`, `/mcp` (+ yours) return 404 JSON, any method, never `index.html`.
- [ ] `GET ${APP_BASE_PATH}/api/app-config` returns allowlisted JSON, at least `{"namespace": <WIP_NAMESPACE or null>}`; no secrets/env dump; `Cache-Control: no-store`. The SPA reads every per-deployment value from it at boot; never bake them into `VITE_*` (only `VITE_BASE_PATH`).
- [ ] Every client fetch prefixed with `import.meta.env.BASE_URL`; never bare `/api/...` or `/wip/...`.
- [ ] `wipProxy` (`@wip/proxy` >= 0.5.0) mounted BEFORE `express.json()` or any body parser on the same paths.
- [ ] `Dockerfile` (prod): multi-stage; build stage takes `VITE_BASE_PATH` as ARG; serves `${APP_BASE_PATH}/api/health`.
- [ ] Prod runtime image has `curl` or `wget` if the manifest declares an HTTP healthcheck (`*-slim` and distroless have neither; alpine has busybox `wget`).
- [ ] Prod runtime stage contains the seed dir (`server/seed/`, or what `SEED_DIR` names), copied explicitly if the server is bundled.
- [ ] `.dockerignore`: `node_modules`, `dist`, `*.log`, `.git`; no unscoped `*.md` (drops runtime markdown such as prompts).
- [ ] `@wip/*` deps pinned as `file:libs/wip-<lib>-<version>.tgz`; after every scaffold `--refresh` commit `package.json` + `package-lock.json`; never re-pack a tarball.

## Manifest (`apps/<name>/wip-app.yaml` in the WIP repo)
- [ ] `spec.ports`: `{name: http, container_port: <express>}` and `{name: dev, container_port: <vite>}`; pick ports no other `apps/*` manifest uses.
- [ ] `spec.env.required`: `WIP_BASE_URL` `{from_component: router}`; `PORT`, `APP_BASE_PATH` (`/apps/<name>`), `NODE_ENV` (`production`) as `{literal: ...}`. No `WIP_API_KEY`: own key, scoped by `spec.namespaces` + `spec.namespace_access`.
- [ ] Uses MCP: optional `MCP_URL` `{literal: "http://wip-router:8080/mcp"}`.
- [ ] Outside-service key: OPTIONAL var, `{from_secret: <name>, supplied_by: operator}`; operator places `~/.wip-deploy/<install>/secrets/<name>`.
- [ ] `spec.routes`: `{path: /apps/<name>, auth_required: true}`; restrict users with `allowed_groups: [...]` on the route, not in-app `ALLOWED_GROUPS`.
- [ ] `spec.healthcheck.endpoint: /apps/<name>/api/health`, answerable without WIP reachable; `liveness_endpoint` (k8s) if it checks a dependency.
- [ ] NATS only via `spec.nats_client.roles`, raw subjects rejected. `event-subscriber`, `replay-subscriber`, Mongo `read-all`: `privileged_key` only.
- [ ] Top-level `metadata.name`, `spec.image`, `app_metadata` (`display_name`, `route_prefix`, `ui_only`): copy the shape of an existing `apps/*/wip-app.yaml`.

## Verify
- [ ] `wip-deploy check-app-deployability <app-dir>` (`--manifest <path>` if not auto-found): all checks pass.
- [ ] `wip-deploy install --target dev --app <name> --app-source <name>=<app-dir>`; SPA loads at `https://localhost:8443/apps/<name>/`.
- [ ] `curl -sk` `.../apps/<name>/api/health` gives `{"status":"ok",...}`; `.../api/app-config` has a `namespace` key; `.../wip/api/registry/namespaces` lists its own; container healthy within 30 s.

## What breaks (symptom → skipped item)
- Healthy container, 404 on SPA root, `NODE_ENV=development` with prod entry-point → no `Dockerfile.dev`.
- Same 404, only http port in `podman inspect` → no `dev` port.
- Caddy `dial tcp ...:5173: connection refused`, browser hangs → no `server.host: '0.0.0.0'`.
- SPA loads, every API call 500/ECONNREFUSED, server log silent → vite proxy targets wrong port.
- SPA loads, 404s on `/api/...` URLs without the base path → bare client fetches.
- Restart loop, `python: command not found` / MCP ECONNREFUSED → no `MCP_URL`.
- API works but `/api/me` says `anonymous: true`, dead login prompt → `/api/me` OIDC-only.
- Browser bounces to a 404 Dex URL → `requireAuth()` not passing through without `OIDC_ISSUER`.
- Prod agent gives generic answers, no errors → `.dockerignore` `*.md` dropped runtime markdown.
- Probe exits 127, permanently unhealthy while app serves → no curl/wget in runtime image.
- Bootstrap fails naming a resolved seed path → seed dir not in runtime stage.
- `JSONDecodeError` on `<!DOCTYPE html>` → SPA fallback catches API paths; clients should check content-type, not 404.
- SPA loads, API ECONNREFUSED, `unhealthy`, crash naming a dependency → lockfile changed; `podman restart <container>` (no entrypoint script: remove the `node-modules` volume).
- Build fails `npm error code EINTEGRITY` on `@wip/*` → re-run `--refresh`, commit both files.

## When the card is not enough
- `docs/wip-deployable-app-contract.md`: full contract, platform invariants, NATS role details.
- `docs/playbooks/wip-deploy-app.md`: getting included (`add-app`), dev runtime env, hot-reload, debugging.
