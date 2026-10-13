# Design: Authentication & Authorization

**Status:** Phase 1 implemented (2026-03-31); Phase 2 implemented in document-store, template-store and def-store; Phase 2b (completing enforcement in reporting-sync and ingest-gateway) implemented and rolled out 2026-10-12 to wip-local, dev-test, prod-test and kb.internal — AWS pending; Phases 3-4 as below
**Priority:** Phase 1 was critical — apps previously served sensitive data to anyone on the network

## Problem

WIP has working service-level authentication (API keys + OIDC JWT for the Console), but **no user authentication for apps**. Anyone who can reach the URL can use the app. On a home network, a guest on WiFi can open the ClinTrial app and see clinical trial data. On a shared server, any user with browser access can reach any app.

This is the single most important gap. Everything else — per-namespace permissions, per-user audit trails, multi-tenant isolation — is secondary until basic "who are you?" is solved for apps.

## Current State

| Layer | Status | Notes |
|-------|--------|-------|
| Service auth (API keys) | **Implemented** | Every backend HTTP service validates `X-API-Key`; reporting-sync and ingest-gateway joined in Phase 2b. Still open on deployed installs: the MCP server's network transport (CASE-857) and NATS (CASE-852) |
| Console auth (OIDC) | Working | Client-side via `oidc-client-ts` + Dex, sends JWT to WIP services |
| App auth | **Phase 1 done** | App-side OIDC via `openid-client` + `express-session`, opt-in via `OIDC_ISSUER` |
| Namespace permissions | Designed, not enforced | `namespace-authorization.md` — grants exist, `check_namespace_permission()` exists, routes don't call it |
| User management | Static Dex config | No self-service, no dynamic user creation |
| Per-user audit trail | Plumbed, not verified | `TrustedHeaderProvider` sets `identity_string` to user email |
| Constellation / multi-app isolation | Not designed | Apps share one user pool, one Dex |

### How authentication works today

```
Console user  ──oidc-client-ts──►  Dex  ──JWT──►  WIP Services     ✅ user identity
App user      ──browser──►  App (openid-client)  ──session──►  @wip/proxy  ──X-WIP-User + API key──►  WIP Services  ✅ user identity (Phase 1)
App user (no OIDC)  ──browser──►  App  ──API key only──►  WIP Services  ⚠️ service identity only
Script/CLI    ──API key──────────────────────────────────►  WIP Services  ✅ service identity
```

**Key finding (2026-03-31):** The original design assumed Caddy had a `caddy-security` OIDC plugin. It does not. The Console handles OIDC entirely client-side (`oidc-client-ts` in Vue, talks to Dex directly, sends Bearer JWT to WIP). Caddy is stock `caddy:2-alpine` — a plain reverse proxy with no auth plugins. Phase 1 was redesigned to use app-side OIDC instead.

## Design Principles

1. **Phase 1 blocks everything.** No fine-grained permissions, no audit trails, no multi-tenant — until apps know who the user is.
2. **Reuse what exists.** Dex is already running. OIDC is already integrated. The Console login flow works. Extend, don't replace.
3. **No custom infrastructure.** Stock Caddy, no plugins, no sidecars. Auth lives in the app's Express server using a shared middleware pattern.
4. **Backwards compatible.** Single-user Podman deployments work unchanged. Auth is opt-in: set `OIDC_ISSUER` to enable, omit it for no auth.
5. **Progressive disclosure.** Phase 1 is a gate (authenticated or not). Phase 2 adds "what can you do?". Phase 3 adds "who did what?". Phase 4 adds multi-app isolation.

---

## Phase 1: App-Side OIDC — No Anonymous Access (Implemented)

**Goal:** Every app request comes from an identified user. No anonymous access.

### How It Works

```
User  ──browser──►  App (Express)
                      │
                      ├── Has session cookie? ──► No  ──► Redirect to Dex login (PKCE flow)
                      │                           Yes ──► Inject X-WIP-User, X-WIP-Groups into request
                      │
                      ├── @wip/proxy (forwardIdentity: true)
                      │     Copies identity headers + API key to upstream
                      │
                      └──► WIP Service
                             TrustedHeaderProvider validates X-WIP-User + X-API-Key
                             Returns UserIdentity with auth_method="gateway_oidc"
```

Auth is handled by the app's own Express server using `openid-client` (server-side OIDC with PKCE). No Caddy plugin, no sidecar. The app creates a session after Dex callback, then injects identity headers on every authenticated request.

### Components

**1. App auth middleware (`server/auth.ts` in scaffold)**

Express middleware using `openid-client` for server-side OIDC:
- Redirects unauthenticated users to Dex login (authorization code flow with PKCE)
- Handles `/auth/callback` — exchanges code for tokens, creates Express session
- Handles `/auth/logout` — destroys session
- Sets `X-WIP-User`, `X-WIP-Groups`, `X-WIP-Auth-Method` on authenticated requests
- Opt-in: only active when `OIDC_ISSUER` env var is set

**2. `@wip/proxy` identity forwarding (`forwardIdentity: true`)**

```typescript
app.use('/wip', wipProxy({
  baseUrl: WIP_BASE_URL,
  apiKey: WIP_API_KEY,           // Service-to-service auth
  forwardIdentity: true,         // Forward X-WIP-User and X-WIP-Groups
}))
```

When `forwardIdentity: true`, the proxy copies three headers from the incoming request to upstream WIP services:
- `X-WIP-User` — user email (e.g., `admin@wip.local`)
- `X-WIP-Groups` — comma-separated groups (e.g., `wip-admins,wip-editors`)
- `X-WIP-Auth-Method` — always `gateway_oidc`

**3. `TrustedHeaderProvider` in wip-auth**

```python
class TrustedHeaderProvider:
    """Accept user identity from trusted gateway/proxy headers.

    Requires BOTH X-WIP-User AND a valid X-API-Key.
    The API key proves the request came through a trusted proxy.
    """

    async def authenticate(self, request: Request) -> UserIdentity | None:
        user = request.headers.get("x-wip-user")
        if not user:
            return None  # Fall through to next provider

        # Validate API key — prevents header spoofing
        api_key = request.headers.get(self.header_name.lower())
        if not api_key or not self._validate_api_key(api_key):
            return None  # X-WIP-User without valid API key = untrusted

        groups = parse_groups(request.headers.get("x-wip-groups", ""))

        return UserIdentity(
            user_id=user,
            username=user.split("@")[0],
            email=user,
            groups=groups or self.default_groups,
            auth_method="gateway_oidc",
            provider="trusted_header",
        )
```

Enabled via `WIP_AUTH_TRUST_PROXY_HEADERS=true`. When enabled, prepended to the provider chain (before JWT and API key providers).

**4. Dex client registration**

```yaml
# config/dex/config.yaml
staticClients:
  - id: wip-apps
    name: WIP Apps
    secret: wip-apps-secret
    redirectURIs:
    - http://localhost:3001/auth/callback
    - http://localhost:3002/auth/callback
    - http://localhost:3003/auth/callback
    - http://localhost:3004/auth/callback
    - http://localhost:3005/auth/callback
```

Separate client from `wip-console` — apps use server-side OIDC with a shared secret, Console uses client-side OIDC.

### Security Model

The trusted header provider requires **both** `X-WIP-User` and a valid `X-API-Key`. This prevents spoofing:

| Scenario | X-WIP-User | X-API-Key | Result |
|----------|-----------|-----------|--------|
| Normal app request (authenticated) | `admin@wip.local` | Valid | `UserIdentity(auth_method="gateway_oidc")` |
| Normal app request (no OIDC) | absent | Valid | Falls through to `APIKeyProvider` |
| Spoofing attempt | `admin@wip.local` | absent | `None` (ignored, warning logged) |
| Spoofing attempt | `admin@wip.local` | invalid | `None` (ignored, warning logged) |

### K8s Deployment

On K8s, the same app-side pattern works unchanged. Alternatively, `oauth2-proxy` as an Ingress sidecar can handle OIDC and inject the same headers:

```yaml
metadata:
  annotations:
    nginx.ingress.kubernetes.io/auth-url: "https://oauth2-proxy.wip.svc/oauth2/auth"
    nginx.ingress.kubernetes.io/auth-signin: "https://wip-kubi.local/oauth2/start"
    nginx.ingress.kubernetes.io/auth-response-headers: "X-WIP-User,X-WIP-Groups"
```

Both approaches produce the same headers — `TrustedHeaderProvider` doesn't care who set them, only that a valid API key accompanies them.

### What This Gives Us

- No anonymous access to any app with `OIDC_ISSUER` set
- User identity visible to apps (`/api/me` endpoint, session data)
- User identity forwarded to WIP (`created_by`/`updated_by` show actual user email)
- Same Dex users/groups for Console and all apps
- Existing API key auth unchanged for scripts, CLI tools, direct API access
- Local dev without auth continues to work (no `OIDC_ISSUER` = no auth)
- No custom Caddy build needed — stock `caddy:2-alpine` throughout

### What This Does NOT Give Us

- Caddy-level auth enforcement (apps must use the middleware — an app that skips it is unprotected)
- Single sign-out across apps (each app has its own Express session)
- Per-namespace permission checks (Phase 2)
- Per-user audit trail verification (Phase 3)

### Implementation (complete)

| Component | File(s) | Change |
|-----------|---------|--------|
| wip-auth | `providers/trusted_header.py` | New provider: accept forwarded identity + validate API key |
| wip-auth | `config.py` | `trust_proxy_headers: bool` setting |
| wip-auth | `__init__.py`, `providers/__init__.py` | Register provider in factory, exports |
| wip-auth | `models.py` | Add `gateway_oidc` to `auth_method` Literal |
| wip-auth | `tests/test_trusted_header.py` | 14 tests: valid/invalid combos, groups parsing, config integration |
| @wip/proxy | `api-proxy.ts`, `file-proxy.ts`, `index.ts` | `forwardIdentity` option, forward 3 headers |
| Dex | `config/dex/config.yaml` | `wip-apps` static client |
| Scaffold | `server/auth.ts` | OIDC middleware (openid-client, PKCE, express-session) |
| Scaffold | `server/index.ts` | Wire auth middleware, `/auth/*` routes, `/api/me` |
| Scaffold | `package.json`, `.env.example` | Dependencies, OIDC env vars |

Versions: wip-auth 0.4.0, @wip/proxy 0.2.0

---

## Phase 1.5: App-Level Access Control — Can You Use This App?

**Goal:** Only authorized users can access a given app. Unauthorized users are blocked at the door, not inside the room.

**Prerequisite:** Phase 1 (user must be authenticated first)

### Problem

Phase 1 gates on "are you logged in?" but any Dex user can open any app. Without app-level access control, namespace permissions (Phase 2) become the first gate — but that gives a frustrating UX: users see the UI, click around, and get cryptic 403s on API calls.

### Implementation

One env var in the scaffold:

```bash
# Only these Dex groups can access this app
ALLOWED_GROUPS=wip-admins,clintrial-users
```

The auth middleware checks group membership immediately after session validation, before serving any content or API response:

```typescript
if (ALLOWED_GROUPS.length > 0 && !user.groups.some(g => ALLOWED_GROUPS.includes(g))) {
  res.status(403).json({
    error: 'Access denied',
    message: "You don't have access to this app. Required group: ..."
  })
  return
}
```

- Empty `ALLOWED_GROUPS` = all authenticated users allowed (backward compatible)
- Check runs on every request, not just page loads (API calls are gated too)
- Logged at startup: `[auth] OIDC configured: ... allowed_groups=[wip-admins, clintrial-users]`

### What This Gives Us

- App access is denied at the door — no misleading UI
- One env var per app — no service-side changes, no database, no new APIs
- Works on every scaffold-based app immediately
- Namespace auth (Phase 2) becomes defense-in-depth, not the primary UX gate

---

## Phase 2: Namespace Authorization — What Can You Do?

**Goal:** Users can only access namespaces they've been granted access to.

**Prerequisite:** Phase 1 (user identity must be known)

Phase 2 is implemented. Summary:

### Permission Model

```
User  ──has──►  NamespaceGrant  ──on──►  Namespace
                 (read|write|admin)
```

- `none` — namespace invisible (404, not 403)
- `read` — list, get, query
- `write` — create, update, delete
- `admin` — manage templates, terminologies, grants

### Enforcement

Permission checks happen in wip-auth (service-side), not at the gateway. The gateway only handles authentication. This keeps the architecture clean — neither Caddy nor the app needs to know about namespaces.

```
App (Express)                      @wip/proxy                       WIP Service
      │                                 │                                │
 OIDC login ✅                           │                                │
 Inject X-WIP-User                       │                                │
      ├─────────────────────────────►    │                                │
      │                        Forward identity + API key                 │
      │                                 ├──────────────────────────────► │
      │                                 │              check_namespace_permission()
      │                                 │              (wip-auth, cached)
      │                                 │                        ✅ or 403│
```

### Enforcement Rollout

Phase 2 was implemented in three services: `check_namespace_permission()` is called from route handlers in document-store, template-store, and def-store. reporting-sync and ingest-gateway were not part of it and enforced neither authentication nor authorization until Phase 2b closed that gap (2026-10-12). Registry is the permission *authority* — it evaluates grants via `check_permission_internal()` and is queried by those services — rather than a caller of `check_namespace_permission()` (there are no call sites in registry).

### Single-User Compatibility

The `wip-admins` superadmin bypass ensures single-user deployments are unaffected. The default admin user and legacy API key are both in `wip-admins` — they skip all permission checks.

---

## Phase 2b: Completing Service-Side Enforcement

**Goal:** Every WIP HTTP service refuses unauthenticated requests and applies namespace authorization. A namespace-scoped key means the same thing against every service.

**Prerequisite:** Phase 2 (the permission model and `wip_auth` enforcement primitives exist).

**Tracking:** CASE-851 (reporting-sync), CASE-855 (ingest-gateway). The design below was reviewed in CASE-851's response thread (#1 proposal, #2 critical review with Peter's rulings).

### Problem (measured 2026-10-12)

Phase 2 was rolled out to document-store, template-store and def-store. Two services were never included.

**reporting-sync** has no authentication layer at all: no `setup_auth`, no middleware, no route dependencies. Its only `wip_auth` use is `declare_api_key_security`, which documents an `X-API-Key` requirement in the OpenAPI schema without enforcing it.

Probed with no credentials, through the public ingress:

| Install | Result |
|---------|--------|
| k8s `dev-test` | `GET …/namespace/{ns}/views`, `POST /query` and `GET /tables` answer 200 (template-store answers 401) |
| wip-local | same, observed by the filing session |

Consequences:

- **Cross-namespace reads.** `/query` executes caller-supplied SQL and can read every namespace schema in the reporting database.
- **Superuser execution.** It runs as the PostgreSQL role `wip`, which is a superuser on the k8s and wip-local installs that were checked. A keyword filter and a read-only transaction setting are the only barriers, and neither is a security boundary.

**ingest-gateway** is publicly routed (`/api/ingest-gateway/*`) and has no inbound authentication. Its HTTP surface is operational (`/health`, `/status`, `/metrics`, `/`), so the exposure is information disclosure.

**A false premise spread.** Both services' factory-gate tests carried a comment claiming they "authenticate in MIDDLEWARE". reporting-sync's was corrected in `47e9f5bf`; ingest-gateway's (`components/ingest-gateway/tests/test_no_factory_dependencies.py`) still carries it.

### Why `setup_auth` alone does not fix it

The platform's authentication has two layers:

1. **`AuthMiddleware`** (installed by `setup_auth`) only *identifies* the caller. A request with no credentials proceeds with no identity.
2. **Route dependencies** enforce. Template-store uses `APIRouter(..., dependencies=[Depends(require_api_key)])`, and adds `check_namespace_permission` / `resolve_namespace_filter` per route.

A service needs both. Authorization also needs the Registry: `REGISTRY_URL` and `REGISTRY_API_KEY`, which reporting-sync's manifest does not carry today. Authentication is already wire-ready, because the manifest carries `API_KEY` and `WIP_AUTH_API_KEYS_FILE`.

### Decisions

**D1 — Authenticate every route.**
- `setup_auth(app, public_paths=[<api-prefixed health>])`, `setup_key_sync` in the lifespan, and a router-level `Depends(require_api_key)`.
- Public: health only. The middleware's default-public root `/` gets an explicit per-service decision.

**D2 — Authorize per route group (reporting-sync).**

| Group | Requirement |
|-------|-------------|
| Namespace-addressed reads (views list, schema, table-name, export by table, parity, and search/tables/activity when a namespace is given) | `read` on the namespace |
| Optional-namespace listings (search, tables, activity, references, entity reference lookups) | `resolve_namespace_filter`, restricted to accessible namespaces — the same contract as other services' list endpoints |
| Registered-view writes | `wip-admins` / `wip-services` for any view; **or ADMIN on the namespace for contained views** — after CREATE, registration checks the view's actual dependencies (`pg_depend`, no SQL parsing) and rolls back with 403 if any lie outside the namespace's own schema. Amended 2026-10-13 (CASE-860, after the CASE-849 ruling made views app-owned bootstrap artifacts): the original all-privileged rule (planning amendment 2026-10-12) made app-key bootstrap registration impossible. |
| Registered-view rows read (`POST …/views/{name}/rows`) | `read` on the namespace — structured (closed operator set, columns validated against the view, bound params), no caller SQL, served from the read-only role. This is the "structured endpoints" path D3 points namespace-scoped callers to (added 2026-10-13, CASE-860). |
| Destructive and operator routes (namespace schema drop, all batch-sync triggers and job management, alert configuration and test) | `wip-admins` / `wip-services` |
| Operational reads (status, metrics, alerts, integrity, job listing) | authenticated; admin where they expose cross-namespace detail |

**D3 — Raw SQL is admin/service-only in v1.**
- `POST /query` and query-shaped `POST /export/csv` require `wip-admins` / `wip-services`.
- Namespace-scoped callers use the structured endpoints, which can be namespace-checked properly.
- **Rejected alternative:** switching to a restricted role with `SET ROLE` on the service's privileged connection. Role switching is checked against the connection's login role, so SQL submitted on that connection can switch back; it is not a boundary.
- **Rejected alternative:** SQL parsing.
- Per-namespace SQL isolation, if a tenant ever needs it, requires a separate low-privilege *connection*. That is a future case, not part of 2b.

**D4 — No request-driven work runs as a superuser.** Two static, deployer-rendered PostgreSQL roles replace `wip` for the services:

| Role | Purpose |
|------|---------|
| `wip_sync_owner` | DDL within namespace schemas; owns the sync pipeline's objects; not superuser; no `CREATEROLE` |
| `wip_reporting_read` | ceiling for caller-driven SQL |

- **No dynamic per-namespace roles.** They would add a role-lifecycle-vs-namespace-lifecycle consistency problem: a fresh namespace has no role until something mints it.
- **Existing installs need a migration.** Their schemas and tables are owned by `wip`, so the deployer needs a one-time re-ownership step, not just role creation.

**D5 — Callers keep working without changes.**
- Every verified service-side caller sends `X-API-Key` from the install secret:
  - registry's namespace deletion;
  - document-store's reporting client and post-restore batch trigger;
  - mcp-server.
- `API_KEY` / `MASTER_API_KEY` map to the legacy key, which carries the admin groups (`wip_auth/config.py`). These callers therefore pass every gate above.
- **Unverified before rollout:** browser and app consumers (React Console, `@wip/proxy` apps, the KB app's search). Any that rely on anonymous access must be fixed first.
- **Verified during the rollout:** the React Console, the KB app and ClinTrial Explorer all send the install key (their own server-side proxies, or the auth-gateway's injected key for browser sessions). No app request was refused on any upgraded install; the only refusals logged were the rollout's own credential-less probes.

**D6 — The definition of done is mechanical.**
- A shape-agnostic walker in `wip_auth.testing`, beside the factory and strictness gates, fires a **credential-less** request at every route of the real ASGI app.
- It asserts 401/403 unless the route is on the service's explicit public allowlist, with the same examined-count canary.
- It catches a missing middleware, a missing dependency and a mis-allowlisted path alike.
- Per-route refusal tests with a non-privileged key (the CASE-845 pattern) cover the wrong-credential axis.
- Both services' `expect_dependencies=False` factory-gate opt-outs are removed once they declare dependencies.

**D7 — No stopgap.** No interim ingress rule or route restriction (Peter, 2026-10-12). The fix proceeds properly, step by step.

### Out of scope

- **NATS authentication (CASE-852).** The same class of gap one layer down. It shares the deployer's secret rendering but needs its own client-credential rollout across every publisher and consumer, so it is a separate phase.
- **Registered views missing from backups (CASE-849).** Unrelated to authentication.

### Plan

Approved 2026-10-12.

#### Planning decisions (Peter)

- **Registered-view writes:** admin/service-only (D2 amended above).
- **The walker covers every service.** Existing gaps it exposes elsewhere are fixed, not allowlisted. Known so far: `/health/integrity` on template-store and document-store.
- **Public allowlist** (each entry must carry a reason):
  - probes (`/health`, `/ready`, `/api/<svc>/health`);
  - the prefixed service roots `GET /api/<svc>/` (build-provenance probe);
  - docs;
  - auth-gateway's OIDC protocol endpoints.
- **Postgres roles are provisioned by reporting-sync at startup.** It uses the existing bootstrap credential on a one-off connection, before opening its two service pools.
  - Why: this works identically on compose, dev and k8s, covers fresh and existing installs and volume rebuilds, and avoids the ordering problem. A post-install hook only runs once everything is healthy, and reporting-sync cannot become healthy without its roles.
  - Residual risk: the process still holds the bootstrap password, but never on a request path. A follow-up moves provisioning out once the deployer supports init jobs.
- **Out of scope:** the MCP server's network transport, tracked separately as CASE-857 (it reads `API_KEY`, while the manifest provides only `MASTER_API_KEY`).

#### Facts the sequence depends on

- **Registry's namespace-deletion cascade sends an empty key.** It reads `API_KEY` / `REGISTRY_API_KEY`, and its manifest sets neither. It must be fixed before reporting-sync enforces, or the cascade silently leaves rows.
- **ingest-gateway's outbound key is the dev default on deployed installs.** It reads only `API_KEY`. This is a live bug, fixed in the same step.
- **reporting-sync registers `/api/reporting-sync/health` on its router.** It must move to the app, or a router-level dependency would gate the probe.
- **`@wip/client`'s `awaitSync()` polls `/status`**, so `/status` stays open to any authenticated caller, with its drop list filtered to the caller's namespaces.
- **reporting-sync calls the stores with its own admin key**, so all namespace filtering for listings must happen inside reporting-sync.
- **Bookkeeping tables have a `$user` shadow split.** `_wip_schema_migrations` and `_wip_sync_status` are accessed unqualified, and live rows sit in a schema named after the login role. Changing the login role would silently switch to stale copies, so the split is repaired before the role change.
- **The bootstrap role owns the system catalogs.** Re-ownership must use targeted `ALTER … OWNER TO`, not `REASSIGN OWNED`.

#### Commit sequence

C3–C5 ship in one release; no install runs C3 without C4 and C5. Every commit leaves all callers working.

| # | Commit | Done when |
|---|--------|-----------|
| C0 | This plan | — |
| C1 | Outbound credentials: registry cascade key fallback (legacy → master → `API_KEY` → registry key; never send empty); ingest-gateway key via `API_KEY`/`DOCUMENT_STORE_API_KEY`/`MASTER_API_KEY` plus a manifest `API_KEY` | neither path can send an empty key or the dev default |
| C2 | `wip_auth.testing` credential-less walker gate plus library tests; adopted by registry, def-store, template-store, document-store, auth-gateway; integrity routes gated; reporting-sync and ingest-gateway adopt it as strict xfail | seven suites green; library tests cover every failure mode |
| C3 | reporting-sync: `setup_auth`, key sync, router-level `require_api_key`, health and root on the app, manifest `REGISTRY_URL`/`REGISTRY_API_KEY`, conftest keys (admin plus local-resolving scoped keys) | walker strict-green; factory gate without opt-out; all tests green |
| C4 | reporting-sync per-route authorization (D2, D3, view writes) plus a refusal matrix with a route-completeness assert | every route in the matrix |
| C5 | reporting-sync listings run in the caller's effective namespace (search, tables, activity, term references, referenced-by), following the platform's multi-namespace-key convention: an explicit namespace must be readable (404); a key reading one namespace gets it implicitly; a key reading several must name one (400); a key reading none is refused (403); superadmins keep the all-namespaces listing. Entity references authorize on the entity's own namespace. Chosen over a per-namespace fan-out (Peter, 2026-10-12): merging per-type paginated buckets across namespaces is only correct after fetching every namespace's full hit list, at N× cost. Outgoing reference values are not redacted: the entity's own data already carries the values it submitted. | no listing returns rows from an inaccessible namespace |
| C6 | ingest-gateway: wip-auth packaging (requirements, release build, dev renderer) in the same commit as `setup_auth`, router dependency and manifest | walker strict-green; image imports `wip_auth` |
| C7 | Bookkeeping repaired into `public` (merge shadows) and every access qualified | integration tests green; second run no-op |
| C8 | Static roles `wip_sync_owner` / `wip_reporting_read` provisioned at startup, re-ownership, default privileges, two pools, self-check (superuser pool → `/health` 503); two new secrets | role integration tests and deployer tests green |
| C9 | `wip-deploy verify-security --live`: credential-less probes (JSON 401/403/404 only) and the role check | fake-transport tests green |

#### Rollout

One image tag carrying C1–C9, deployed in this order: wip-local (compose) → dev-test (k8s) → AWS (k8s) → kb (k8s, last).

**The install must be re-rendered, not just re-tagged.** From C8 on, reporting-sync needs two new secret-backed variables (`POSTGRES_SYNC_OWNER_PASSWORD`, `POSTGRES_READ_PASSWORD`), and reporting-sync and ingest-gateway need new auth and Registry variables (C3, C6).

- **A `wip-deploy` render** generates and persists the secrets in the install's secret backend and applies the new environment.
- **An image-only roll** (for example `kubectl set image`) leaves the old environment in place. reporting-sync then refuses to serve PostgreSQL and answers `/health` with 503. That is deliberately loud, and the fix is to re-render.

For each install:

1. **Baseline:**
   - re-run the measured reproduction;
   - read-only inventory of roles, bookkeeping copies and object ownership;
   - dump `public._wip_views` (registered views are not in WIP backups).
2. **Deploy.**
3. **Check the startup log:** roles ensured, objects re-owned, shadows merged, self-check passed.
4. **Credential-less re-probe** of every route group.
5. **Admin-key and scoped-key checks.**
6. **End-to-end callers:**
   - registry namespace-deletion cascade;
   - document-store parity and post-restore batch;
   - MCP report tools;
   - toolkit status;
   - one ingest message.
7. **`verify-security --live`.**

#### Rollout record (2026-10-12)

The image tag was `20261012a` for every platform service. The order changed from the plan: Peter sequenced the installs and held AWS back.

| Install | Path | What the startup did | Result |
|---|---|---|---|
| wip-local (dev, compose) | Source clone pulled, then `wip-deploy redeploy` | Bookkeeping copies had already merged on an earlier hot reload; roles created; 15 schemas and 414 relations re-owned | `verify --security --live` 8/8; scoped key refused raw SQL and view writes, saw only its namespace; registry cascade, ingest message, MCP parity and batch syncs all passed |
| dev-test (k8s) | Clean slate: namespace deleted, fresh `install`, backup restored | Roles created on an empty database, nothing to re-own | 8/8; parity complete after restore (`kb` 4737, `library` 19); scoped key behaved as on wip-local |
| prod-test (k8s) | In place: saved-state tag edit, `redeploy`, apps via `app-deploy` | Bookkeeping copies both populated (32 / 40 rows, 3 conflicting rows) merged into 47 rows, the newer copy winning; 7 schemas and 139 relations re-owned | 8/8 including ingest-gateway; ClinTrial Explorer browsed end to end without refusals |
| kb.internal (k8s) | In place, as prod-test | Copies of 28 / 39 rows, all 28 overlaps conflicting with the `wip` copy newer, merged into 39 rows; 3 schemas and 102 relations re-owned | 8/8; KB, React Console and the case client working |
| AWS (k8s) | Pending | — | — |

Findings outside Phase 2b that the rollout surfaced:
- **MinIO images.** Docker Hub and quay.io no longer serve MinIO anonymously. The manifest now points at an arm64-only copy on ghcr, so an amd64 cluster needs a multi-arch copy first.
- **Identity-hash migration (CASE-847)** has to run after restoring an older archive, followed by a forced reporting re-sync. It applied cleanly on every install.
- **Startup self-healing (CASE-862).** Two startup paths do not retry a late dependency.

---

## Phase 3: Per-User Audit Trail — Who Did What?

**Goal:** Every create, update, and delete in WIP records which user performed it, even when the request came through an app.

**Prerequisite:** Phase 1 (user identity forwarded to WIP)

### Current State

WIP already tracks `created_by` and `updated_by` on documents, templates, and terminologies. With Phase 1 complete, `TrustedHeaderProvider` sets `identity_string` to the user's email (e.g., `admin@wip.local`) instead of the API key name (e.g., `apikey:legacy`). This is already plumbed — Phase 3 is verification and Console UI updates.

### Verification (2026-04-01)

Code review confirmed the full chain works correctly:

| Step | Status |
|------|--------|
| `identity_string` shows user email for `gateway_oidc` | **Verified** — `models.py:40`, test in `test_trusted_header.py:164` |
| All services use `get_identity_string()` for `created_by`/`updated_by` | **Verified** — document-store, template-store, def-store all consistent |
| NATS events carry user identity in `changed_by` | **Verified** — all 3 service NATS clients pass `changed_by=actor` |
| reporting-sync preserves identity in PostgreSQL | **Verified** — transformer extracts `created_by`/`updated_by`, worker maps `changed_by` |
| Console shows actual user in audit columns | **Already works** — `VersionHistory.vue` displays `created_by` as-is; with gateway_oidc this shows email |

### Audit in Events

NATS events already carry `changed_by`. With Phase 1's identity flowing through:

```json
{
    "event_type": "document.updated",
    "changed_by": "admin@wip.local",
    "document": { "..." }
}
```

Instead of:

```json
{
    "event_type": "document.updated",
    "changed_by": "apikey:legacy",
    "document": { "..." }
}
```

---

## Phase 4: Constellation Auth — App Isolation & External Access

**Goal:** Multiple apps can have independent user pools, and apps deployed outside the WIP network can authenticate users.

**Prerequisite:** Phases 1-3

This is the most open-ended phase. Several sub-problems:

### 4A: Per-App User Pools

Today, all apps share one Dex instance with one set of users. ClinTrial users see D&D data if namespace permissions aren't locked down. For true multi-tenant isolation:

**Option A: Dex Connectors** — Dex supports multiple identity connectors (LDAP, SAML, GitHub, etc.). Each app could use a different connector, giving it a different user pool. Dex remains the single OIDC provider, but user sources differ.

**Option B: Multiple Dex Instances** — Each app gets its own Dex. Heavy, but complete isolation. Only makes sense for truly independent deployments.

**Option C: App-Scoped Grants** — Keep one user pool, but grants are scoped to both namespace AND app. User Peter has `write` on namespace `clintrial` via the ClinTrial app, but no access via D&D Compendium. The `app-manifest.json` already declares a namespace — this just formalizes the binding.

**Recommendation:** Option C for most cases. Option A when external IdP integration is needed (e.g., hospital LDAP for a clinical app). Option B only for air-gapped deployments.

### 4B: Gateway-Level Auth (Caddy or NGINX)

If a future deployment needs auth enforcement at the reverse proxy level (e.g., protecting apps that don't use the scaffold middleware), two options:

**Option A: Custom Caddy build** — Build Caddy with the `caddy-security` plugin for server-side OIDC. Requires maintaining a custom Docker image.

**Option B: `oauth2-proxy` sidecar** — Deploy `oauth2-proxy` alongside Caddy or NGINX Ingress. Handles OIDC, injects the same `X-WIP-User`/`X-WIP-Groups` headers. No custom builds, but an extra container.

Neither is needed while apps use the scaffold auth middleware. These are fallback options for apps that can't or don't want to handle auth themselves.

### 4C: WIP as Auth Provider

The boldest option: WIP itself becomes an OIDC-compatible auth provider. Apps authenticate against WIP, which manages users, issues tokens, and enforces permissions.

This replaces Dex entirely. WIP's Registry (which already manages namespaces and API keys) extends to manage users.

**Pros:** Single source of truth for identity. Apps get auth for free. No external dependency.
**Cons:** Significant implementation effort. OIDC compliance is non-trivial. Dex already does this well.

**Recommendation:** Defer. Dex (or any external OIDC provider) handles the hard parts of token issuance, key rotation, and protocol compliance. WIP should consume identity, not produce it. Revisit only if Dex becomes a maintenance burden.

---

## Phase Summary

| Phase | Goal | Prerequisite | Status |
|-------|------|-------------|--------|
| **1** | **No anonymous app access** | None | **Done** (2026-03-31) — app-side OIDC, wip-auth 0.4.0, @wip/proxy 0.2.0 |
| **1.5** | **App-level access control** | Phase 1 | **Done** (2026-04-01) — `ALLOWED_GROUPS` env var in scaffold middleware |
| **2** | Per-namespace permissions | Phase 1 | **Backend done in document-store, template-store, def-store** (0e548f3), Console UX polish remaining |
| **2b** | Complete service-side enforcement (reporting-sync, ingest-gateway) | Phase 2 | **Implemented and rolled out** (2026-10-12, C0–C9): wip-local, dev-test, prod-test, kb.internal — AWS pending |
| **3** | Per-user audit trail | Phase 1 | **Verified** — code review confirmed full chain |
| **4A** | Per-app user isolation | Phase 2 | Design needed |
| **4B** | Gateway-level auth | Phase 1 | Fallback option, not needed while apps use scaffold |
| **4C** | WIP as auth provider | All | Deferred |

## Dependency Graph

```
Phase 1: App-Side OIDC  ✅
    │
    ▼
Phase 1.5: App Access Control  ✅
    │
    ├──────────────────┐
    ▼                  ▼
Phase 2:           Phase 3:
Namespace Auth     Audit Trail
    │
    ├──────────────────┐
    ▼                  ▼
Phase 4A/4B/4C:    Phase 2b:
Constellation      Complete enforcement
                   (reporting-sync,
                    ingest-gateway)
```

## Open Questions

1. **Session lifetime.** App sessions default to 24 hours. Dex ID tokens expire in 15 minutes. Should apps refresh tokens silently, or is a long session acceptable since the API key provides the service-level trust boundary?

2. **Single sign-out.** Each app has its own Express session — logging out of one app doesn't log out of others. Is this acceptable, or should we implement back-channel logout via Dex?

3. **API key + user identity intersection.** ~~Open question.~~ **Resolved** (2026-04-04). Non-privileged API keys must have explicit `namespaces`; unscoped keys get no access. The API key's namespace scope acts as a ceiling on what the user can access through that app.

4. **Header trust boundary.** `X-WIP-User` headers are only trusted when accompanied by a valid API key. But if an attacker compromises an API key, they can impersonate any user. Mitigation: API keys for app proxies should have limited namespace scope, and key rotation should be easy (see the WIP Guide for key rotation procedures).

## Relation to Existing Designs

| Document | Relation |
|----------|-------------|
| `pluggable-apps.md` | Phase 1 here is independent of gateway routing. Pluggable-apps proposes gateway-level auth for v2. |
| `wip-guide.md` | WIP Guide covers key rotation (formerly `security/key-rotation.md`) and auth configuration. |
| `authentication.md` | Reference doc for current auth. Needs updating after each phase. |
