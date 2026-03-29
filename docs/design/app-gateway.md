# Design: App Gateway & WIP Proxy

**Status:** Design complete, not started

## Motivation

Every WIP app that runs in a browser needs to solve the same three problems:

1. **Auth injection** — Browser JavaScript cannot hold API keys securely. The app's server must inject credentials into WIP API calls.
2. **MinIO URL rewriting** — Presigned file URLs point to internal hostnames (`wip-minio:9000`) that browsers cannot resolve. File downloads must be proxied.
3. **Sub-path routing** (K8s only) — Multiple apps share one hostname at `/apps/{name}/`, requiring path prefix handling.

The DnD Compendium solved all three by hand-rolling a 70-line Express reverse proxy (lines 22-70 of `server/index.ts`). Every new app would copy-paste this pattern with slight variations. This is the Dex situation again — constant hassle, token-expensive debugging, one-off workarounds per app.

**These are two related but separable problems:**

| Concern | Localhost (Podman) | K8s |
|---------|-------------------|-----|
| Auth injection | App-side proxy | App-side proxy OR shared gateway |
| MinIO rewriting | App-side proxy | App-side proxy OR shared gateway |
| Sub-path routing | Not needed (own port) | Shared gateway |
| App discovery / portal | Nice-to-have | Required |
| TLS termination | Caddy (existing) | Ingress (existing) |

This design addresses both with two deliverables:

1. **`@wip/proxy`** — Express middleware that any app drops in. Solves auth injection and file proxying on all deployment targets.
2. **App Gateway** — Shared infrastructure for K8s (and optionally Podman) that adds sub-path routing, app registration, and portal page.

## Deliverable 1: `@wip/proxy` Middleware

### What It Does

A Node.js middleware package that handles WIP API proxying and file content proxying. Any Express/Fastify app adds it with two lines:

```typescript
import { wipProxy } from '@wip/proxy'

app.use('/wip', wipProxy({
  baseUrl: process.env.WIP_BASE_URL || 'https://localhost:8443',
  apiKey: process.env.WIP_API_KEY,
}))
```

This creates routes:
- `GET/POST/PUT/DELETE /wip/api/{service}/*` — proxied to WIP with API key injected
- `GET /wip/files/{fileId}/content` — proxied file download (resolves MinIO URLs server-side)

The frontend uses `@wip/client` configured with `baseUrl: '/wip'` instead of the WIP instance URL:

```typescript
const wip = createWipClient({
  baseUrl: '/wip',        // goes through the proxy
  auth: { type: 'none' }, // proxy handles auth
})
```

### What It Replaces

The DnD app's 70-line manual proxy (lines 22-70 of `server/index.ts`) becomes:

```typescript
app.use('/wip', wipProxy({ baseUrl: WIP_BASE_URL, apiKey: WIP_API_KEY }))
```

All service prefixes, raw body handling, header forwarding, content-type propagation, and error handling are standardized.

### Implementation

The package lives at `libs/wip-proxy/`. It is a tarball dependency like `@wip/client` — no npm registry needed.

```
libs/wip-proxy/
├── src/
│   ├── index.ts          # Main export
│   ├── api-proxy.ts      # WIP API forwarding with auth injection
│   └── file-proxy.ts     # File content proxying (MinIO URL resolution)
├── package.json
├── tsconfig.json
└── README.md
```

Key implementation details:

- **Raw body forwarding:** Uses `express.raw({ type: '*/*' })` on proxy routes to avoid JSON parsing (same pattern as DnD app).
- **Streaming:** Response bodies are streamed, not buffered — important for large file downloads.
- **Header forwarding:** Forwards `content-type`, `content-disposition`, `content-length` from upstream. Strips internal headers.
- **Error handling:** Upstream failures return 502 with structured error JSON.
- **File content endpoint:** `GET /wip/files/{fileId}/content` calls the document-store file metadata endpoint to get the presigned URL, then proxies the download. The browser never sees the MinIO hostname.
- **No opinion on framework:** Core logic is a request handler function. Express middleware is a thin wrapper. Fastify plugin is a future option.

### `@wip/client` Proxy Mode

`@wip/client` already works with the proxy — just set `baseUrl` to the proxy mount point. The client calls `/wip/api/document-store/documents` instead of `https://localhost:8443/api/document-store/documents`. The proxy strips `/wip` and forwards to the real URL with credentials.

One addition needed: a `files.downloadContent(fileId)` method that calls `/wip/files/{fileId}/content` — the proxy-aware file download path. This replaces apps manually constructing MinIO URLs.

### Deployment

Works identically on:
- **Localhost:** App on `localhost:3011`, proxy talks to `https://localhost:8443`
- **K8s (standalone):** App pod, proxy talks to `https://wip-kubi.local` or internal service URLs
- **K8s (with gateway):** App pod, proxy talks to gateway (or direct — the proxy doesn't care)

## Deliverable 2: App Gateway (K8s / Multi-App)

### When It's Needed

The gateway is needed when **multiple apps share a single hostname**. On localhost with Podman, each app gets its own port — no gateway needed. On K8s (or a Pi running many apps behind Caddy), apps live at `/apps/{name}/` on one hostname.

### Architecture

The gateway is a reverse proxy (extending the existing Caddy or NGINX Ingress configuration) that routes `/apps/{name}/*` to the correct app backend. It is NOT a separate service — it's configuration added to the existing proxy infrastructure.

**Podman Compose (Caddy):**

```
# Added to Caddyfile by setup.sh when apps are registered
handle /apps/dnd-compendium/* {
    uri strip_prefix /apps/dnd-compendium
    reverse_proxy dnd-compendium:3011
}

handle /apps/clintrial/* {
    uri strip_prefix /apps/clintrial
    reverse_proxy clintrial:3010
}
```

**K8s (NGINX Ingress):**

Each app gets its own Ingress resource (as the DnD deployment already does):

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: dnd-compendium-ingress
  annotations:
    nginx.ingress.kubernetes.io/rewrite-target: /$2
spec:
  rules:
    - host: wip-kubi.local
      http:
        paths:
          - path: /apps/dnd(/|$)(.*)
            pathType: ImplementationSpecific
            backend:
              service:
                name: dnd-compendium
                port:
                  number: 3011
```

### App Registration

Apps register via `app-manifest.json` in the app's root directory:

```json
{
  "name": "dnd-compendium",
  "label": "D&D Compendium",
  "description": "Monster manual and spell reference with AI-powered natural language queries",
  "version": "1.0.0",
  "namespace": "dnd",
  "port": 3011,
  "health": "/api/health",
  "icon": "book-open"
}
```

**Podman:** `setup.sh` (or a new `register-app.sh` script) reads manifests from registered app directories and generates Caddyfile entries.

**K8s:** The manifest is baked into the container image. `build-images.sh` generates the Ingress resource from it. Or: a lightweight controller watches for pods with a `wip.app/manifest` annotation.

### Portal Landing Page

A minimal page served at `/apps/` (or `/`) listing all registered apps as cards. Auto-generated from app manifests. Shows:

- App name, description, icon
- Health status (green/red dot from health endpoint)
- Link to the app

**Podman:** Caddy serves a static HTML page generated by the registration script.

**K8s:** A small static-site pod, or generated into the Console, or a ConfigMap-driven nginx.

The portal is a nice-to-have, not a blocker. Apps work without it — users just need to know the URL.

### Auth at the Gateway Level

Two modes, depending on deployment:

**API key injection (current pattern):** Each app's `@wip/proxy` middleware injects its own API key. The gateway just routes. Simple, works today.

**OIDC session (future):** The gateway handles OIDC login, establishes a session cookie, and injects the user's identity into upstream requests. Apps receive an authenticated context without implementing any auth. This is the Caddy + Dex pattern already used for the Console, extended to apps.

OIDC session mode is a future enhancement. API key injection via `@wip/proxy` is sufficient for now and avoids the complexity of session management at the gateway level.

## Implementation Plan

### Phase 1: `@wip/proxy` Middleware

1. Create `libs/wip-proxy/` package with API proxy and file content proxy
2. Add `files.downloadContent(fileId)` to `@wip/client`
3. Update `create-app-project.sh` to include `@wip/proxy` tarball and wire it up in the app skeleton
4. Refactor DnD Compendium to use `@wip/proxy` (validate the middleware works)
5. Update DevGuardrails Guide 1 and Guide 3

### Phase 2: App Gateway (Caddy)

6. Add `/apps/{name}/*` routing support to `setup.sh` / Caddyfile generation
7. Create `register-app.sh` script that reads `app-manifest.json` and updates Caddy config
8. Generate portal landing page from manifests

### Phase 3: App Gateway (K8s)

9. Template for per-app Ingress resources generated from `app-manifest.json`
10. Add to `build-images.sh` pipeline
11. Portal page deployment (ConfigMap + nginx, or integrated into Console)

### Phase 4: OIDC Session Auth (Future)

12. Gateway-level OIDC login with session cookies
13. User identity forwarding to app backends
14. `@wip/proxy` accepts session token instead of API key

## What Changes Per App

**Before (DnD pattern):**
- 70 lines of hand-rolled Express proxy code per app
- Manual `VITE_BASE_PATH` handling
- Manual MinIO URL workarounds
- Per-app Ingress YAML with rewrite annotations
- Custom file download proxy per app

**After:**
- `app.use('/wip', wipProxy({ baseUrl, apiKey }))` — one line
- `createWipClient({ baseUrl: '/wip', auth: { type: 'none' } })` — standard config
- `app-manifest.json` — declarative registration
- `VITE_BASE_PATH` still needed for sub-path deployments, but handled by the app skeleton template

## Relationship to Existing Infrastructure

| Component | Current Role | Change |
|-----------|-------------|--------|
| **Caddy** (Podman) | TLS + API routing + Console | Add `/apps/*` routing, portal page |
| **NGINX Ingress** (K8s) | TLS + API routing + Console | Per-app Ingress resources (already done for DnD) |
| **`@wip/client`** | Direct WIP API calls | Add proxy-aware mode (`baseUrl: '/wip'`), add `files.downloadContent()` |
| **`create-app-project.sh`** | App scaffolding | Include `@wip/proxy`, wire up proxy in skeleton, generate `app-manifest.json` |
| **`build-images.sh`** (K8s) | Image builds | Generate Ingress from manifest |

## Open Questions

1. **Should `@wip/proxy` also proxy MCP?** The Node.js SDK has a content-length bug with streamable HTTP. SSE works. The proxy could normalize this. Defer to Phase 4 — apps that need MCP use server-side agents (like DnD's `/api/ask`), not browser-to-MCP connections.

2. **Portal in Console or standalone?** Arguments for Console: already deployed, Vue app, has namespace awareness. Arguments for standalone: Console is an admin tool, portal is for end users. Lean standalone — keep concerns separate.

3. **Should the gateway inject namespace?** Apps are namespace-scoped. The gateway could inject `X-WIP-Namespace` based on the app manifest's `namespace` field. Nice but not essential — apps already know their namespace from env vars.
