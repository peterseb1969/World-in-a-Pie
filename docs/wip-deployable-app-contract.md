---
title: "The wip-deployable app contract"
kind: paper
related: CASE-379 (synthesis), CASE-375 (origin / WIP-KB containerization gap), CASE-358/CASE-359/CASE-360/CASE-361 (cross-host plumbing), CASE-374 (auth preset), CASE-377 (MCP_ALLOWED_HOST manifest gap), CASE-378 (wip-router /mcp route gap), CASE-366 (operator-stdout discipline), CASE-373 (bootstrap-bundle UX layer above this contract), CASE-55 / CASE-57 / CASE-58 (--app-source dev-mode origin), CASE-480 (companion modelling-discipline note)
supersedes: WIP-KB/papers/k8s-from-start-app-yac-recipe.md
---

# The wip-deployable app contract

**Audience:** every YAC about to scaffold or maintain a WIP app — backend, frontend, or full-stack — regardless of whether the target install runs on compose, k8s, or apps-only against a remote WIP.
**Goal:** ship the app so that `wip-deploy install --target <anything> --app <name> --app-source <path>` brings it up healthy on the first try, and the SPA loads at `https://<host>:<port>/apps/<name>/` without manual env patching or container massaging.
**Provenance:** synthesized 2026-05-14 from the May 13–14 cross-host validation arc and the WIP-KB / WIP-AA containerization work. Supersedes `WIP-KB/papers/k8s-from-start-app-yac-recipe.md` — title and scope generalized; the contract isn't k8s-specific.

---

## Why this paper exists

WIP-KB was built standalone for ~10 days before being made wip-deployable. The integration was one afternoon for the YAC who did it, but it touched **nine surfaces** in the source repo and required two **platform-side** fixes (the wip-router `/mcp` route and the mcp-server host-allowlist). Each of the nine was a one-line scaffold decision at day 1 — and each of them became diff archaeology once the app was running standalone.

The same lesson surfaced again 24 hours later with WIP-AA: same nine surfaces, same retrofit. The pattern is now stable enough to name:

> **The contract is small. The cost of skipping it scales with how much of the app you wrote first.** Read this before you scaffold; pay the 30-minute tax up front; never pay the multi-day retrofit tax later.

The contract has two halves — what the **source repo** must look like, and what the **WIP repo's `apps/<name>/wip-app.yaml` manifest** must declare. Both halves are required; either alone is insufficient.

---

## The contract

### Source repo (`~/Development/WIP-<name>/`)

| File | Requirement |
|---|---|
| `Dockerfile.dev` | At the path containing `package.json`. Runs `npm run dev`. **No baked `node_modules`** — the deployer mounts a named volume so dev rebuilds are warm. Alpine + curl. Pattern verbatim from `apps/react-console` and `apps/clintrial-explorer`. |
| `vite.config.ts` (frontend apps) | `server.host: '0.0.0.0'` (without it Caddy → Vite → ECONNREFUSED inside the container). Dev `proxy` keys prefixed with `BASE_PATH` and pointing at **this app's Express port** (not 3001 by clintrial-copy-paste reflex). `base` derived from `VITE_BASE_PATH || APP_BASE_PATH || '/'`. |
| `docker-entrypoint-dev.sh` (optional, recommended) | Hash-gated `npm ci` populate so dev container restarts don't re-run `ci` unnecessarily. See `apps/react-console/docker-entrypoint-dev.sh` for the canonical pattern. |
| `server/index.ts` (Express apps) | All routes mounted on an `express.Router()` under `APP_BASE_PATH`. `app.set('trust proxy', 1)` for HTTPS termination. `requireAuth()` middleware passes through when `OIDC_ISSUER` is unset (apps-only / dev mode). In `NODE_ENV=production`, serve `dist/` statically with a SPA fallback. |
| Runtime app-config route (CASE-551) | `GET ${APP_BASE_PATH}/api/app-config` returns the deployment's client-visible config as JSON — at minimum `{"namespace": <WIP_NAMESPACE or null>}`; multi-namespace apps add their extra keys (e.g. `library_namespace`). Allowlist only: never an env dump, never secrets (the response reaches every browser); `Cache-Control: no-store`. The SPA fetches this at boot (scaffold: `src/lib/app-config.ts`) for **every** client-visible per-deployment value — never mirror such values into `VITE_*` build bakes; a bake and runtime env are two sources that drift silently (`VITE_BASE_PATH` is the one deliberate exception — consumed at bundle-emit time, loud when wrong). The scaffold emits the route inline; apps that vendor `@wip/proxy` ≥ 0.4.0 can mount its `appConfigHandler` at the same path. |
| Client fetches | Every URL prefixed with `import.meta.env.BASE_URL` — *never* bare `/api/...` or `/wip/...`. |
| `wipProxy` mount order (`@wip/proxy` ≥ 0.5.0) | The proxy streams both directions (O(1) memory in payload size — large archive uploads/downloads pass through flat; no bespoke streaming routes needed). Consequence: it consumes the request as a raw stream, so it must be mounted **before** any body-parsing middleware (`express.json()`, …) that would match the same paths — a parser ahead of the proxy drains the body and an empty stream is forwarded. The deprecated `bodyLimit` option is a no-op. |
| `Dockerfile` (production) | Multi-stage. Build stage takes `VITE_BASE_PATH` as ARG and bakes it into the static bundle. Production stage runs the server via `tsx server/index.ts` (or your app's entry), exposes the app's port, healthchecks `${APP_BASE_PATH}/api/health`. **If the manifest declares an HTTP healthcheck, the runtime image MUST contain `curl` or `wget`** — the rendered probe is a shell `curl \|\| wget` chain; an image with neither (e.g. `node:*-slim`, distroless — alpine bases get busybox `wget` for free) exits 127 on every probe and reports permanently unhealthy while the app serves fine, and the install's health-wait fails pointing at the app. Fix: install `wget` in the runtime stage (~2 MB), use an alpine base, or drop `spec.healthcheck` — the container then shows plain `Up` with no health status, like the platform router. `wip-deploy check-app-deployability` pre-flights this. |
| `.dockerignore` | Excludes `node_modules`, `dist`, `*.log`, `.git`. Be careful with `*.md` — narrow it to `/*.md` and add explicit excludes for `papers/` / `docs/`, **not** an unscoped `*.md` that catches runtime files like `server/prompts/assistant.md`. |
| Readme | A `## Development with wip-deploy` section documenting the contract surface for human readers. Two paragraphs. Pointers at this paper. |

### WIP repo (`apps/<name>/wip-app.yaml`)

```yaml
spec:
  ports:
    - {name: http, container_port: <your-express-port>}
    - {name: dev,  container_port: <your-vite-port>}   # Vite, used with --app-source
  env:
    required:
      - {name: WIP_BASE_URL,   source: {from_component: router}}
      - {name: WIP_API_KEY,    source: {from_secret: api-key}}
      - {name: PORT,           source: {literal: "<your-express-port>"}}
      - {name: APP_BASE_PATH,  source: {literal: "/apps/<name>"}}
      - {name: NODE_ENV,       source: {literal: "production"}}
    optional:
      - {name: MCP_URL,            source: {literal: "http://wip-router:8080/mcp"}}  # if app uses MCP
      - {name: ANTHROPIC_API_KEY,  source: {from_secret: anthropic-api-key}}         # if app uses Claude
  routes:
    - {path: /apps/<name>, auth_required: true}
  healthcheck:
    endpoint: /apps/<name>/api/health   # MUST be locally answerable, must not require WIP reachable
```

Port choice: pick one Express port and one Vite port from the `apps/` directory's existing range. RC = 3001/5173, CT = 3002/5174, KB = 3012/5173 (KB collides with RC on Vite; pick fresh), AA = 3014/5180. Coordinate via case-helper if the range gets crowded.

### Verification (the one-line acceptance test)

```bash
wip-deploy install --target dev --app <name> --app-source <name>=~/Development/WIP-<name>
open https://localhost:8443/apps/<name>/
```

The SPA must load. `curl -sk https://localhost:8443/apps/<name>/api/health` must return `{"status":"ok",...}`. `curl -sk https://localhost:8443/apps/<name>/api/app-config` must return JSON carrying a `namespace` key — the deployment's WIP namespace or `null` (proves the runtime config source the client boots from, CASE-551). `curl -sk https://localhost:8443/apps/<name>/wip/api/registry/namespaces` must return the WIP install's namespaces (proves the server-side proxy works). Container must be marked healthy by podman within 30s, no manual env patching, no `--force-recreate` dance. If any of these fail, the contract has a gap — file a case naming which step broke.

A future `/check-app-deployability <app-source-dir>` slash command automates this verification — it parses the manifest, greps the source repo, and reports each contract item with ✓ / ✗.

---

## What breaks when you skip step N

The contract is small. The failure signatures when you skip a step are specific enough to recognize.

### Skip 1: no `Dockerfile.dev`

**Failure:** Container falls back to the production `Dockerfile`. Deployer forces `NODE_ENV=development` (the --app-source contract) but the entry-point is `tsx server/index.ts` running production-mode code paths. SPA static serve at line `if (NODE_ENV === 'production')` is guarded off in dev → bare `/apps/<name>/` returns **404 Cannot GET**.

**Symptom signature:** healthy container, 404 on the SPA root, server logs show production-stage entry-point with `NODE_ENV=development` (contradiction).

**Origin:** WIP-KB shipped prod-only containerization for 10 days; the first --app-source attempt failed exactly here.

### Skip 2: missing `server.host: '0.0.0.0'` in vite.config.ts

**Failure:** Vite binds to `localhost` inside the container. Caddy proxies `<container>:5173` and gets ECONNREFUSED. The container is healthy (Express on 3012 answers) but the SPA never loads.

**Symptom signature:** Caddy logs `dial tcp [container-ip]:5173: connect: connection refused`. Browser hangs on `/apps/<name>/`.

**Origin:** surfaced on WIP-KB; WIP-AA later landed it pre-emptively after reading the recipe.

### Skip 3: wrong vite proxy port (clintrial-copy-paste)

**Failure:** `vite.config.ts` was copied from clintrial; proxy targets `http://localhost:3001`. Your app runs on port 3012. The SPA loads (Vite serves it) but every `/server-api/*` and `/wip/*` fetch from the client returns 500 because Vite proxies into a void.

**Symptom signature:** SPA loads. F12 → Network shows every API call returns 500 or ECONNREFUSED. Server's own logs show no traffic (because Vite isn't reaching it).

**Origin:** WIP-KB's `vite.config.ts` had a `clintrial`-inherited port reference. Fixed in KB-YAC's 2026-05-14 patch.

### Skip 4: no `dev` port in manifest

**Failure:** Caddy routes `/apps/<name>/*` to the only declared port (the http/Express port). Express's SPA static serve is `NODE_ENV === 'production'`-gated and the deployer forces development for --app-source. The bare `/apps/<name>/` 404s. HMR never works.

**Symptom signature:** identical to Skip 1, but caused by the manifest, not the Dockerfile. `kubectl get pod <pod> -o yaml | grep -A 5 ports` (k8s) or `podman inspect <container>` (compose) shows only the http port.

### Skip 5: no `MCP_URL` env declared

**Failure (apps using MCP):** App boots; `agent.ts` falls back to `stdio` transport, tries to spawn `python -m wip_mcp` inside the container. Container is `node:20-alpine` — no Python. Crash loop with `ECONNREFUSED` after the spawn fails.

**Symptom signature:** container restart-loops. Logs show `MCP client error: ECONNREFUSED` or `python: command not found`. The app's chat / agent surface is dead even though everything else works.

### Skip 6: client fetches are bare absolute paths

**Failure:** Works on the host (Vite proxy handles `/api/*`). Breaks under `--app-source` and under deployment because the deployer mounts the app at `/apps/<name>/` — `fetch('/api/foo')` resolves to `https://host/api/foo`, not `https://host/apps/<name>/api/foo`. Every API call 404s.

**Symptom signature:** SPA loads. F12 → Network shows 404s on `/api/...` URLs that don't include the BASE_PATH. The Express server's own access log shows no traffic.

**Origin:** every retrofit ever. `--preset query` scaffold today emits bare paths; this is the single biggest source of integration friction. The fix is `${import.meta.env.BASE_URL}<path>`.

### Skip 7: `/api/me` is OIDC-only

**Failure:** App runs behind wip-router with gateway auth (the standard preset's `auth.mode=hybrid`). Router injects `X-WIP-User` / `X-WIP-Groups` headers. Your `/api/me` only consults the OIDC session — which is empty in gateway mode — so the SPA thinks no one is logged in even though the user is authenticated upstream.

**Symptom signature:** API calls succeed (the gateway accepted them). `/api/me` returns `anonymous: true`. The SPA shows a login prompt that does nothing.

**Origin:** k8s-from-start paper, gateway-aware /api/me section.

### Skip 8: `.dockerignore` excludes runtime data

**Failure:** Broad `*.md` pattern silently excludes `server/prompts/assistant.md` (or your app's equivalent runtime markdown). Build succeeds; agent loads a `wip://`-resource fallback prompt instead of the app-specific one; behavior degrades subtly.

**Symptom signature:** prod and dev behave identically locally; prod's agent gives generic answers; no error logs.

**Origin:** WIP-KB retrofit; `agent.ts` has a try/catch around the prompt load that hides the missing file.

### Skip 9: server `requireAuth()` doesn't pass-through in dev

**Failure:** App boots in `--app-source` mode (apps-only). `OIDC_ISSUER` is unset. `requireAuth()` redirects to a Dex login URL that doesn't exist. Every page redirects forever.

**Symptom signature:** browser bounces between `/apps/<name>/` and a Dex URL that 404s.

**Origin:** apps-only deployment shape. The middleware must explicitly check `if (!process.env.OIDC_ISSUER) return next()` at the top.

---

## Platform invariants you can rely on

These are platform-side guarantees the contract assumes. They're invariants today, named here so apps can count on them.

- **wip-router routes `/api/*` AND `/mcp`** — same-network MCP clients can reach the MCP server via the router; the internal Caddyfile carries the `/mcp` route.
- **mcp-server accepts `Host: wip-mcp-server`** — DNS-rebinding protection is configured (the `MCP_ALLOWED_HOST` env on the mcp-server manifest) so in-cluster MCP calls aren't rejected.
- **Standard preset defaults `auth.mode=hybrid`** — both JWT bearers (from local Dex) AND API keys (from cross-host clients) work without explicit `--auth-mode hybrid`. Operators wanting strict JWT-only opt in with `--auth-mode oidc`.
- **`from_component: router` resolves to the public ingress URL** — `WIP_BASE_URL` set this way works for same-host and cross-host installs alike.
- **Operator-stdout discipline** — `wip-deploy` warnings and progress go to stderr; stdout is reserved for `--format json` payloads.
- **Lib tarballs are versioned and immutable** — apps bundle `libs/wip-<lib>-<version>.tgz` and pin it in `package.json` as `file:libs/wip-<lib>-<version>.tgz`. The same filename always means the same bytes, so the `package-lock.json` integrity hash survives platform-side lib rebuilds and `npm ci` in a Docker build never hits EINTEGRITY. `create-app-project.sh --refresh` ships the current tarball AND rewrites your `file:` spec + lockfile for the `@wip/*` deps you already declare — **commit `package.json` + `package-lock.json` after every refresh**. A refresh that detects same-name-different-bytes aborts loudly (a lib changed content without a version bump) instead of silently desyncing you. The earlier mutable `-latest.tgz` was retired precisely because its fixed-name/changing-content shape desynced consumer lockfiles and npm caches.

  *Migration / failure signature:* an app still pinning `file:libs/wip-<lib>-latest.tgz` (a pre-versioned-tarball scaffold) heals on its next `--refresh`. If your container build fails with `npm error code EINTEGRITY` on an `@wip/*` package, that is this — re-run `--refresh` and commit the two files. Do not hand-edit or re-pack a bundled tarball: same version must mean same bytes.

- **External CA trust is auto-injected into app containers (Node-stack)** — when an apps-only install carries a `secrets/external-ca.crt` (seeded by `wip-deploy import-bundle`), the deployer automatically bind-mounts it read-only at **`/etc/ssl/certs/external-ca.crt`** in every app container and sets **`NODE_EXTRA_CA_CERTS`** to that path. You declare nothing — there is no manifest field; the behaviour flips on purely from the CA file's presence (the deployer's `install`/`render` auto-detect it). **Node-stack apps** pick the extra CA up transparently (Node reads `NODE_EXTRA_CA_CERTS`); **Python / Go / Rust apps ignore it** and use their own trust stores (`certifi`, the system pool, …) — harmless either way. Two reservations follow: don't unset `NODE_EXTRA_CA_CERTS`, and don't bind your own file at `/etc/ssl/certs/external-ca.crt`. Backend / full-stack installs never see this — it is apps-only. For the operator-side flow that makes the CA file exist in the first place, see `docs/design/bootstrap-bundle.md`.

You don't need to test these. They're stable. Just use them.

---

## Local dev with this scaffolded in

With `BASE_PATH` defaulting to `/` and `import.meta.env.BASE_URL` defaulting to `/`:

- Vite's dev server proxy keys become `'' + '/api'`, `'' + '/wip'`, `'' + '/server-api'` — same as a non-deployable scaffold today.
- Express router mounts at `/` — routes resolve to identical paths.
- All client fetches resolve to the same URLs.
- Session cookie path is `/` — same as today.
- OIDC stays off.

**Net: zero behavior change in standalone-dev.** The contract pays no tax for being wip-deployable; it pays a one-time scaffold tax of 30 minutes and gives you push-button deployment forever after.

---

## Companion contract: your data lives in WIP's primitives

This paper is the **deployment** contract — how to ship the container. It has a companion you read on the same session-start path (`/wip-setup`, `/wip-wake`): the **modelling** contract — how you use WIP once the app is running. One line, because it is the single most expensive mistake an APP-YAC makes — quietly, after multiple sessions, under feature-delivery pressure:

> **WIP's primitives are your only data model** — namespaces, terminologies, terms, templates, documents, files, relationships. `metadata.*` is a throwaway scratchpad, never identity, config, or schema. The moment your code reads metadata back as structure, you have built a **sidecar model**. A data-model change is a design event: get approval, don't inline it, don't route around it via metadata. Config that matters is a config *document* (create the config template first); a controlled vocabulary is **terms**; if you're stuck on where something belongs, discuss it — don't invent a shape in metadata.

This is enforced as a mechanical checkpoint in `/wip-implement` Step 0 (Q1/Q2) and `/wip-improve` Rule 6, and stated in your CLAUDE.md. The deployment contract gets you running; the modelling contract keeps you from accreting silent technical debt once you are.

---

## What this paper isn't solving

- **Persistence.** Apps with their own non-WIP state (PVCs, separate DBs) need separate manifest declarations. Out of scope here.
- **Multi-replica.** The contract assumes one pod / in-memory session. Scaling to N replicas requires sticky-session ingress or a shared session store. Separate paper when it lands.
- **Migrations.** WIP-side schema migrations (your app's bootstrap of templates/terminologies) ride `BootstrapGate`'s offer-on-empty / use-on-exists discipline — already in the scaffold; not contract surface.
- **The bootstrap-bundle UX.** That's the layer **above** this contract: once apps consistently satisfy the contract, the bundle layer condenses cross-host setup into one click. Apps need not anticipate the bundle work; the contract is sufficient.
- **The k8s-vs-compose-vs-apps-only choice.** The deployer handles target-specific concerns. The app code is target-agnostic if the contract is met.

---

## Scaffold gaps to fold in

What `--preset query` ships today vs. what this paper recommends:

| Surface | Scaffold today | Recommended |
|---|---|---|
| `server/index.ts` | Routes mounted on `app`, no `BASE_PATH` | Router mounted under `APP_BASE_PATH` |
| `vite.config.ts` | No `base`; raw dev proxy paths; default `localhost` host binding | `base: VITE_BASE_PATH \|\| APP_BASE_PATH \|\| '/'`, prefixed dev proxy, `server.host: '0.0.0.0'` |
| `src/main.tsx` (WipClient) | `baseUrl: '/wip'` | `baseUrl: \`${import.meta.env.BASE_URL}wip\`` |
| `src/lib/wipBulk.ts` | `fetch('/wip' + path)` | `fetch(\`${import.meta.env.BASE_URL}wip\` + path)` |
| Inline client fetches | Bare paths | Prefixed with `${import.meta.env.BASE_URL}` |
| `/api/me` | OIDC session only | Gateway-header sniff → OIDC → anonymous |
| `Dockerfile.dev` | Not in scaffold | Provided as starter (canonical pattern) |
| `Dockerfile` (production) | Not in scaffold | Two-stage; `VITE_BASE_PATH` as ARG |
| `.dockerignore` | Not in scaffold | Provided; narrow `*.md` exclusion called out |
| `apps/<name>/wip-app.yaml` | Not in scaffold | Provided alongside source-repo files; declares both ports + required env |

These are mechanical scaffold edits. The right move is filing a case (BE-YAC owns the scaffold today) to fold each into `--preset query` so the next APP-YAC doesn't relearn them.

---

## Day-1 checklist for new APP-YACs

1. Run the scaffold (`--preset query` or the variant you're using).
2. Before writing a single feature, do the contract — both halves. Source repo + `apps/<name>/wip-app.yaml`. 30 minutes total.
3. Run the verification: `wip-deploy install --target dev --app <name> --app-source <name>=<your-repo>`. SPA must load on the first try.
4. Once `/check-app-deployability` ships, run it against your repo before committing scaffold output. It catches the gaps mechanically.
5. If something breaks, find the failure signature in the "What breaks when you skip step N" annex. File a case if the signature isn't there yet — that's how this paper stays current.
6. Write your first feature. The scaffold is now permanent platform; the integration surface won't come back.

You don't have to actually deploy to k8s on day 1. You don't have to deploy at all on day 1. You just have to make the scaffold respect the contract so when you DO deploy, it's `wip-deploy install` rather than a multi-day refactor.

---

## Provenance

This paper supersedes `WIP-KB/papers/k8s-from-start-app-yac-recipe.md` (KB-YAC, 2026-05-13). The original was retrofit-driven; this version is contract-driven. Title and scope generalized. The original is preserved as a stub at its old path so existing links / case references still resolve.

Filed via CASE-379 (BE-YAC, 2026-05-14). Synthesizes the platform-side work from CASE-358 / CASE-359 / CASE-360 / CASE-361 / CASE-366 / CASE-374 / CASE-375 / CASE-377 / CASE-378, plus the WIP-KB and WIP-AA app-side containerization commits. Versioned-tarball invariant added 2026-06-10 (CASE-441 / CASE-442, BE-YAC). Companion modelling-discipline note added 2026-06-21 (CASE-480, FRanC) — a pointer to the canonical clause in the modelling gate, not a second copy.
