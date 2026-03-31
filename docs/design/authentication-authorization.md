# Design: Authentication & Authorization

**Status:** Phase 1 implemented (2026-03-31), Phases 2-4 designed
**Priority:** Phase 1 was critical — apps previously served sensitive data to anyone on the network

## Problem

WIP has working service-level authentication (API keys + OIDC JWT for the Console), but **no user authentication for apps**. Anyone who can reach the URL can use the app. On a home network, a guest on WiFi can open the ClinTrial app and see clinical trial data. On a shared server, any user with browser access can reach any app.

This is the single most important gap. Everything else — per-namespace permissions, per-user audit trails, multi-tenant isolation — is secondary until basic "who are you?" is solved for apps.

## Current State

| Layer | Status | Notes |
|-------|--------|-------|
| Service auth (API keys) | Working | All services validate `X-API-Key` |
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

## Phase 2: Namespace Authorization — What Can You Do?

**Goal:** Users can only access namespaces they've been granted access to.

**Prerequisite:** Phase 1 (user identity must be known)

This phase is already designed in detail: see `docs/design/namespace-authorization.md`. Summary:

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

Phase 2 activates the existing `check_namespace_permission()` calls. Services already have the function — it just needs to be called from route handlers. See `namespace-authorization.md` Session 2 for the per-service integration plan.

### Single-User Compatibility

The `wip-admins` superadmin bypass ensures single-user deployments are unaffected. The default admin user and legacy API key are both in `wip-admins` — they skip all permission checks.

---

## Phase 3: Per-User Audit Trail — Who Did What?

**Goal:** Every create, update, and delete in WIP records which user performed it, even when the request came through an app.

**Prerequisite:** Phase 1 (user identity forwarded to WIP)

### Current State

WIP already tracks `created_by` and `updated_by` on documents, templates, and terminologies. With Phase 1 complete, `TrustedHeaderProvider` sets `identity_string` to the user's email (e.g., `admin@wip.local`) instead of the API key name (e.g., `apikey:legacy`). This is already plumbed — Phase 3 is verification and Console UI updates.

### Remaining Work

| Step | Change |
|------|--------|
| 1 | Verify `identity_string` shows user email end-to-end (app → proxy → service → MongoDB) |
| 2 | Verify NATS events carry user identity in `changed_by` |
| 3 | Verify reporting-sync preserves user identity in PostgreSQL |
| 4 | Console: show actual user in audit columns, not API key name |

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
| **2** | Per-namespace permissions | Phase 1 | Designed (`namespace-authorization.md`), not started |
| **3** | Per-user audit trail | Phase 1 | Plumbed by Phase 1, needs verification |
| **4A** | Per-app user isolation | Phase 2 | Design needed |
| **4B** | Gateway-level auth | Phase 1 | Fallback option, not needed while apps use scaffold |
| **4C** | WIP as auth provider | All | Deferred |

## Dependency Graph

```
Phase 1: App-Side OIDC  ✅
    │
    ├──────────────────┐
    ▼                  ▼
Phase 2:           Phase 3:
Namespace Auth     Audit Trail
    │
    ▼
Phase 4A/4B/4C:
Constellation
```

## Open Questions

1. **Session lifetime.** App sessions default to 24 hours. Dex ID tokens expire in 15 minutes. Should apps refresh tokens silently, or is a long session acceptable since the API key provides the service-level trust boundary?

2. **Single sign-out.** Each app has its own Express session — logging out of one app doesn't log out of others. Is this acceptable, or should we implement back-channel logout via Dex?

3. **API key + user identity intersection.** Requests carry both an API key (service auth) and user identity (from headers). Should the API key's namespace restrictions constrain the user's permissions? **Probably yes** — the API key represents the app's scope, and the user's permissions are intersected with it.

4. **Header trust boundary.** `X-WIP-User` headers are only trusted when accompanied by a valid API key. But if an attacker compromises an API key, they can impersonate any user. Mitigation: API keys for app proxies should have limited namespace scope, and key rotation should be easy (see `docs/security/key-rotation.md`).

## Relationship to Existing Designs

| Document | Relationship |
|----------|-------------|
| `app-gateway.md` | Phase 1 here is independent of gateway routing. Gateway (Phases 2-3 there) is needed for multi-app sub-path routing, not for auth. |
| `namespace-authorization.md` | Phase 2 here = that entire document. Already designed in detail. |
| `authentication.md` | Reference doc for current auth. Needs updating after each phase. |
| `security/key-rotation.md` | API key management. Relevant to Phase 1 (app API keys) and Phase 4 (header trust). |
