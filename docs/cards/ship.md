<!-- card-sources: what this card condenses; checked by components/mcp-server/tests/test_card_drift.py
docs/wip-deployable-app-contract.md sha256:13eb727c53fdeac8 reviewed:c50094bb
deployer/src/wip_deploy/spec/app.py sha256:fa21d3a8ea1e94ee reviewed:67993302
-->
# WIP ship card — making the app deployable

Before features: healthy under `wip-deploy` on the first try, SPA at its origin's root (`https://<label>.<host>/`), no manual env patching.

## Source repo (the dir holding `package.json`)
- [ ] `Dockerfile.dev` here; runs `npm run dev`; alpine + curl; NO baked `node_modules`.
- [ ] `package.json` has a `dev` script.
- [ ] `docker-entrypoint-dev.sh`: hash-gated `npm ci` at container start.
- [ ] `vite.config.ts`: `server.host: '0.0.0.0'`; dev `proxy` keys at THIS app's Express port; default `base` (`/`): no base path anywhere.
- [ ] Server: routes mounted at `/`; `app.set('trust proxy', 1)`. `/auth/handoff|renew|renewed|logout|userinfo` are the gateway's.
- [ ] `requireAuth()` passes through when `OIDC_ISSUER` is unset.
- [ ] `/api/me` reads gateway headers `X-WIP-User` / `X-WIP-Groups` first, then OIDC session, then anonymous.
- [ ] `NODE_ENV=production`: serve `dist/` with SPA fallback; unmatched `/api`, `/auth`, `/wip`, `/server-api`, `/mcp` (+ yours) return 404 JSON, any method, never `index.html`.
- [ ] `GET /api/app-config` returns allowlisted JSON, at least `{"namespace": <WIP_NAMESPACE or null>}`; no secrets/env dump; `Cache-Control: no-store`. The SPA reads per-deployment values from it at boot, never from `VITE_*`.
- [ ] Every client fetch prefixed with `import.meta.env.BASE_URL`; never bare `/api/...` or `/wip/...` (on an app origin those have no session).
- [ ] `WipProvider` from `@wip/react` >= 0.26.0 (renews an app origin's session); other clients use the app's origin.
- [ ] `wipProxy` (`@wip/proxy` >= 0.6.0) with `onMissingKey: 'unavailable'`, mounted BEFORE `express.json()` or any body parser.
- [ ] `Dockerfile` (prod): multi-stage; builds at base `/`; serves `/api/health`.
- [ ] Prod runtime image has `curl` or `wget` if the manifest declares an HTTP healthcheck (`*-slim` and distroless have neither).
- [ ] Prod runtime stage contains the seed dir (`server/seed/` or `SEED_DIR`), copied explicitly if the server is bundled.
- [ ] `.dockerignore`: `node_modules`, `dist`, `*.log`, `.git`; no unscoped `*.md` (drops runtime prompts).
- [ ] `@wip/*` deps pinned as `file:libs/wip-<lib>-<version>.tgz`; after every scaffold `--refresh` commit `package.json` + `package-lock.json`; never re-pack a tarball.

## Manifest (`apps/<name>/wip-app.yaml` in the WIP repo)
- [ ] `spec.ports`: `{name: http, container_port: <express>}` and `{name: dev, container_port: <vite>}`; pick ports no other `apps/*` manifest uses.
- [ ] `spec.env.required`: `WIP_BASE_URL` `{from_component: router}`; `PORT`, `NODE_ENV` (`production`) as `{literal: ...}`; no `APP_BASE_PATH` / `VITE_BASE_PATH` (refused). No `WIP_API_KEY`: own key, scoped by `spec.namespaces` + `spec.namespace_access`.
- [ ] Uses MCP: optional `MCP_URL` `{literal: "http://wip-router:8080/mcp"}`.
- [ ] Outside-service key: OPTIONAL var, `{from_secret: <name>, supplied_by: operator}`; operator places `~/.wip-deploy/<install>/secrets/<name>`.
- [ ] `spec.routes`: `{path: /, auth_required: true}`; restrict users with `allowed_groups: [...]` on the route, not in-app `ALLOWED_GROUPS`.
- [ ] `spec.healthcheck.endpoint: /api/health`, answerable without WIP reachable; `liveness_endpoint` (k8s) if it checks a dependency.
- [ ] NATS only via `spec.nats_client.roles`, raw subjects rejected. `event-subscriber`, `replay-subscriber`, Mongo `read-all`: `privileged_key` only.
- [ ] `metadata.name`, `spec.image`, `app_metadata` (`label` names the origin): copy an existing `apps/*/wip-app.yaml`.

## Verify
- [ ] `wip-deploy check-app-deployability <app-dir>` (`--manifest <path>` if not auto-found): all checks pass.
- [ ] `wip-deploy install --target dev --app <name> --app-source <name>=<app-dir>`; SPA loads at `https://<label>.wip.localhost:8443/`.
- [ ] `curl -sk` `.../api/health` gives `{"status":"ok",...}`; `.../api/app-config` has a `namespace` key; `.../wip/api/registry/namespaces` lists its own; container healthy within 30 s.

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
- `JSONDecodeError` on `<!DOCTYPE html>` → SPA fallback catches API paths.
- SPA loads, API ECONNREFUSED, `unhealthy`, crash naming a dependency → lockfile changed; `podman restart <container>` (no entrypoint script: remove the `node-modules` volume).
- Build fails `npm error code EINTEGRITY` on `@wip/*` → re-run `--refresh`, commit both files.
- API calls fail after ~15 min open, reload fixes them → `@wip/react` < 0.26 or `appSession={false}`.

## When the card is not enough
- `docs/wip-deployable-app-contract.md`: full contract, invariants, NATS roles.
- `docs/playbooks/wip-deploy-app.md`: `add-app`, dev env, hot-reload, debugging.
