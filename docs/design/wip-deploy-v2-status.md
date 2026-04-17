# `wip-deploy` v2 — Implementation Status

**Last updated:** 2026-04-16

Living companion to `wip-deploy-v2.md`. Tracks what's built, what's
verified, and what's on the follow-up list.

## Snapshot

- **Scope complete:** steps 1–6 of the plan in `wip-deploy-v2.md`
  ("The right order" section).
- **Target working:** compose. Full end-to-end verified on a real
  machine.
- **Target pending:** k8s (step 8), dev renderer (step 7).
- **v1 surfaces:** still present, deletion is step 12.

## What's proven working (compose)

Real install, real Podman, real OIDC flow:

```
wip-deploy install --preset standard --target compose \
  --hostname localhost \
  --registry ghcr.io/peterseb1969 --tag v1.1.0 \
  --remove console --app react-console --no-wait
```

Produces:

- 10 containers up (mongodb, registry, def-store, template-store,
  document-store, auth-gateway, dex, caddy, mcp-server, react-console)
- Caddy serving HTTPS on :8443 with internal self-signed cert
- Dex issuing OIDC tokens at `/dex/*`
- auth-gateway protecting `/api/*` and `/apps/*` routes via Caddy
  `forward_auth`
- Login flow: `/apps/rc/` → 302 `/auth/login` → Dex → callback →
  session cookie set → proxied to react-console
- react-console's SSR proxy (`@wip/proxy`) reaching services via
  Caddy's internal `:8080` listener with its own `X-API-Key` header —
  all service indicators green in the UI
- Post-install hook: Registry's `initialize-wip-namespaces` runs
  successfully, seeds the `wip` namespace

## Follow-up work (priority order)

### Priority 1 — DONE

1. **Probe-tool abstraction for healthchecks.** ✅ `HealthcheckSpec.probe:
   curl | wget | auto` (default `auto`). `auto` emits a shell-chained
   `curl -fsS || wget -qO-` so images with either tool work. Apps
   (dnd/clintrial/react-console) have their healthchecks back. Dex
   stays without a healthcheck (distroless, no shell).

2. **Optional `from_secret` activation-skip.** ✅ `UncollectedSecretRef`
   mirrors `InactiveComponentRef`. Optional env vars whose `from_secret`
   name isn't in the collected set are omitted rather than emitting
   `${VAR}` → empty string. Example: react-console's `ANTHROPIC_API_KEY`
   is absent from the env when the user didn't supply an Anthropic key.

3. **Remove the uvicorn command heuristic.** ✅ Every Python-service
   component manifest now specifies `command` explicitly. Renderer's
   only remaining name-based overrides are Dex (config-file path) and
   MinIO (console-address flag). Apps always use their image CMD.

### Priority 2 — convenience

4. **Pre-install port-conflict check.**
   `wip-deploy install` should detect existing `wip-*` containers (not
   from this install) and refuse-or-prompt. Avoids the confusing half-
   install state when v1 and v2 collide.

5. **`wip-deploy status` verb.**
   Read compose ps + health probes, print a table. Today users
   fall back to `podman ps`.

6. **Compose local-build error-guard.**
   When `--target compose` runs without `--registry`, detect the
   absence of pre-built images up front and emit a helpful error
   referencing the dev renderer, rather than failing on `compose up`.

### Priority 3 — external (image bugs)

7. **mcp-server `/health` auth.** ✅ Fixed in source: unauthenticated
   `/health` Starlette route registered before `ApiKeyMiddleware`; the
   middleware exempts `/health` via a path-skip list. Takes effect in
   the next mcp-server image build (v1.2.0+). v1.1.0 images still
   return 401 on `/health`.
8. **App images ship curl (or wget).** ⚠️ Half-resolved by (1). The
   probe-tool abstraction now emits a shell-chained curl-or-wget, so
   any image with either tool works. Separately fixing the images to
   ship curl is no longer required.

### Priority 4 — k8s renderer quality-of-life (deferred from step 8 MVP)

These are not blocking the first working k8s deploy but are tracked
here so they don't fall through the cracks:

9. **Kustomize overlays for presets.** MVP renders a flat directory of
    active components; `kubectl apply -f` against it. A proper
    `base/` + `overlays/<preset>/` structure with strategic merge
    patches would allow toggling presets without re-rendering — matters
    once a GitOps workflow exists. **Why deferred:** Peter hasn't asked
    for GitOps; rendering only active components achieves the same end
    today.

10. **`k8s-secret` backend.** File backend generates secrets to disk,
    then renders them inline into the k8s Secret yaml. A native k8s
    backend would read/write secrets as k8s Secret objects via kubectl,
    enabling `wip-deploy rotate-secrets` against a live cluster.
    **Why deferred:** first deploy doesn't need rotation; file backend
    covers bootstrap.

11. **NetworkPolicies.** The hand-written v1 policies had a fundamental
    bug (deny-all-default blocked cross-namespace ingress controller
    traffic). Proper policies need the renderer to model the actual
    communication graph from component manifests' `depends_on` +
    `routes`. **Why deferred:** security hardening, not functional
    requirement; cluster works without them.

12. **`apply_k8s` with rollout-status wait.** Compose apply polls
    `podman-compose ps` for health. k8s equivalent polls `kubectl
    rollout status` per Deployment/StatefulSet. **Why deferred:** shell
    plumbing; renderer's job is correct YAML; manual `kubectl apply`
    works for now.

### Priority 5 — post-step 10

13. **Delete v1 surfaces** (step 12 in `wip-deploy-v2.md`): setup.sh,
    quick-install.sh, setup-wip.sh, docker-compose/, docker-compose.
    production.yml, components/\*/docker-compose\*.yml, k8s/. Rename
    scripts/ → tools/. Rewrite quick-install.sh to the bootstrap form.
14. Kill the default `auto_https disable_redirects` in Caddyfile for
    `letsencrypt` mode — currently hardcoded regardless of `tls`
    setting. Works but would confuse a future reader.

## Design calls we made and are now certain about

These were choices during implementation that the smoke test validated:

- **Module names == component names.** Users type `reporting-sync`,
  not an abstract `reporting`. Clean, no naming layer.
- **`depends_on` is hard-dep only.** Soft runtime features (AI query,
  reporting tab) are optional env vars, not depends_on. Verified with
  react-console (was mis-declaring `reporting-sync` as hard dep) and
  dnd (was mis-declaring `mcp-server`). Both corrected.
- **`CMD-SHELL` + `shlex.join` for all healthchecks.** Arrays passed
  as `["CMD", ...]` get re-shell-parsed by podman-compose, breaking
  mongosh's `db.runCommand('ping').ok`. Using CMD-SHELL with
  shell-safe quoting avoids the whole class.
- **Caddyfile must specify explicit `:8443`.** Without it Caddy binds
  to container port 443 — the compose port map expects 8443. Every
  site block lists the full `host:port`.
- **Internal Caddy `:8080` listener is mandatory.** Not decorative —
  react-console's server-side proxy needs plain HTTP to reach
  services without TLS complications.
- **Fresh-install mongo-user/mongo-password are optional env vars.**
  Collection filter is "required env vars only + Dex clients +
  explicit OIDC users" — we don't auto-generate mongo auth credentials.
  Deployments without mongo auth work; deployments that want mongo
  auth supply the secrets manually.

## How to reproduce the smoke test

```bash
# One-time (first machine):
cd World-in-a-Pie
pip install -e ./deployer

# Register auth-gateway under ghcr (pull from gitea, push to ghcr):
podman pull --tls-verify=false gitea.local:3000/peter/auth-gateway:v1.2-rc1
podman tag  gitea.local:3000/peter/auth-gateway:v1.2-rc1 \
            ghcr.io/peterseb1969/auth-gateway:v1.1.0
podman login ghcr.io
podman push ghcr.io/peterseb1969/auth-gateway:v1.1.0

# Install:
wip-deploy install \
  --preset standard --target compose --hostname localhost \
  --registry ghcr.io/peterseb1969 --tag v1.1.0 \
  --remove console --app react-console --no-wait

# Browser: https://localhost:8443/apps/rc/ (trailing slash matters)
# Login creds: cat ~/.wip-deploy/default/.env
# Tear down:   wip-deploy nuke --purge-all --remove-data -y
```

## See also

- `wip-deploy-v2.md` — full architecture + design decisions
- `install-path-drift.md` — the problem v2 solves (historical)
