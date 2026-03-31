# Design: Authentication & Authorization

**Status:** Draft
**Priority:** Phase 1 is critical — apps currently serve sensitive data to anyone on the network

## Problem

WIP has working service-level authentication (API keys + OIDC JWT for the Console), but **no user authentication for apps**. Anyone who can reach the URL can use the app. On a home network, a guest on WiFi can open the ClinTrial app and see clinical trial data. On a shared server, any user with browser access can reach any app.

This is the single most important gap. Everything else — per-namespace permissions, per-user audit trails, multi-tenant isolation — is secondary until basic "who are you?" is solved for apps.

## Current State

| Layer | Status | Notes |
|-------|--------|-------|
| Service auth (API keys) | Working | All services validate `X-API-Key` |
| Console auth (OIDC) | Working | Caddy + Dex, JWT tokens, login page |
| App auth | **Missing** | `@wip/proxy` injects API key; no user identity |
| Namespace permissions | Designed, not enforced | `namespace-authorization.md` — grants exist, `check_namespace_permission()` exists, routes don't call it |
| User management | Static Dex config | No self-service, no dynamic user creation |
| Per-user audit trail | Missing for apps | All app requests look like "api-key-123", not "Alice" |
| Constellation / multi-app isolation | Not designed | Apps share one user pool, one Dex |

### What works today

```
Console user  ──OIDC──►  Caddy  ──JWT──►  WIP Services     ✅ authenticated
App user      ──HTTP──►  App    ──API key──►  WIP Services  ❌ no user identity
Script/CLI    ──API key──────────────────►  WIP Services     ✅ authenticated (service identity)
```

The gap is clear: app users are invisible to WIP.

## Design Principles

1. **Phase 1 blocks everything.** No fine-grained permissions, no audit trails, no multi-tenant — until apps know who the user is.
2. **Reuse what exists.** Dex is already running. OIDC is already integrated. The Console login flow works. Extend, don't replace.
3. **Zero per-app auth code.** Apps should not implement login pages, token management, or session handling. The infrastructure handles it.
4. **Backwards compatible.** Single-user Podman deployments work unchanged. Auth is opt-in per deployment.
5. **Progressive disclosure.** Phase 1 is a gate (authenticated or not). Phase 2 adds "what can you do?". Phase 3 adds "who did what?". Phase 4 adds multi-app isolation.

---

## Phase 1: Gateway Authentication — No Anonymous Access to Apps

**Goal:** Every app request comes from an identified user. No anonymous access.

### How It Works

```
User  ──HTTP──►  Caddy  ──session cookie?──►  No  ──►  Redirect to Dex login
                                               Yes ──►  Inject identity headers ──► App
```

Caddy already does OIDC for the Console via the `caddy-security` plugin. Extend the same pattern to app routes.

### Caddy Configuration

```caddyfile
# Existing: Console OIDC (already works)
@console path /console/*
handle @console {
    # ... existing OIDC config ...
}

# New: App OIDC — same Dex, same flow, same session
@apps path /apps/*
handle @apps {
    authenticate with dex_oidc {
        # Same OIDC config as Console
    }

    # Inject user identity into upstream requests
    header_up X-WIP-User {http.auth.user.email}
    header_up X-WIP-Groups {http.auth.user.groups}
    header_up X-WIP-Auth-Method oidc

    reverse_proxy {upstream}
}
```

After authentication, Caddy injects three headers:

| Header | Value | Example |
|--------|-------|---------|
| `X-WIP-User` | User's email from OIDC | `peter@wip.local` |
| `X-WIP-Groups` | Comma-separated groups from JWT | `wip-admins,wip-editors` |
| `X-WIP-Auth-Method` | Always `oidc` at gateway level | `oidc` |

### App-Side: `@wip/proxy` Changes

`@wip/proxy` currently injects a static API key into all WIP requests. After Phase 1, it gains a second mode: **forward user identity** from gateway headers.

```typescript
// Before (current): static API key
app.use('/wip', wipProxy({
  baseUrl: WIP_BASE_URL,
  apiKey: WIP_API_KEY,           // Service account key
}))

// After: forward user identity + API key for service auth
app.use('/wip', wipProxy({
  baseUrl: WIP_BASE_URL,
  apiKey: WIP_API_KEY,           // Still needed: service-to-service auth
  forwardIdentity: true,         // Forward X-WIP-User and X-WIP-Groups
}))
```

When `forwardIdentity: true`, the proxy:
1. Reads `X-WIP-User` and `X-WIP-Groups` from the incoming request (set by Caddy)
2. Forwards them to WIP services alongside the API key
3. WIP services see both: the API key (for service auth) and the user identity (for audit + authorization)

### wip-auth: Accept Forwarded Identity

wip-auth already validates API keys and JWTs. Add a third path: **trusted header identity**.

```python
# New provider: TrustedHeaderProvider
# Only active when WIP_AUTH_TRUST_PROXY_HEADERS=true (opt-in, not default)

class TrustedHeaderProvider:
    """Accept user identity from trusted gateway headers."""

    async def authenticate(self, request: Request) -> AuthResult | None:
        user = request.headers.get("X-WIP-User")
        groups = request.headers.get("X-WIP-Groups")

        if not user:
            return None  # Fall through to next provider

        # API key must also be present (service auth)
        # This prevents anyone from spoofing headers without the key
        api_key = request.headers.get("X-API-Key")
        if not api_key:
            return None

        return AuthResult(
            identity=UserIdentity(
                user_id=user,
                username=user.split("@")[0],
                email=user,
                groups=groups.split(",") if groups else [],
                auth_method="gateway_oidc",
                provider="caddy",
            ),
            method="trusted_header",
        )
```

**Security:** The trusted header provider requires both:
- Valid `X-WIP-User` header (from gateway)
- Valid `X-API-Key` header (from `@wip/proxy`)

This prevents header spoofing — you can't just set `X-WIP-User` without also having a valid API key. The API key proves the request came through a trusted app proxy.

### K8s Deployment

On K8s with NGINX Ingress, use `oauth2-proxy` as a sidecar or Ingress-level annotation:

```yaml
# Ingress annotation approach
metadata:
  annotations:
    nginx.ingress.kubernetes.io/auth-url: "https://oauth2-proxy.wip.svc/oauth2/auth"
    nginx.ingress.kubernetes.io/auth-signin: "https://wip-kubi.local/oauth2/start"
    nginx.ingress.kubernetes.io/auth-response-headers: "X-WIP-User,X-WIP-Groups"
```

Same result: by the time the request reaches the app, identity headers are present.

### What This Gives Us

- No anonymous access to any app behind the gateway
- User identity visible to apps (for display: "Logged in as Peter")
- User identity forwarded to WIP (for future audit + authorization)
- Same login page for Console and all apps (consistent UX)
- Existing API key auth unchanged for scripts, CLI tools, direct API access

### What This Does NOT Give Us

- No per-namespace permission checks (Phase 2)
- No per-user audit trail in WIP events (Phase 3)
- No app-specific user pools (Phase 4)
- Apps that run outside the gateway (standalone) still need their own auth (Phase 4)

### Implementation

| Step | File(s) | Change |
|------|---------|--------|
| 1 | `config/caddy/Caddyfile` | Add OIDC + header injection for `/apps/*` routes |
| 2 | `libs/wip-proxy/src/api-proxy.ts` | Add `forwardIdentity` option, forward `X-WIP-User` + `X-WIP-Groups` |
| 3 | `libs/wip-auth/src/wip_auth/providers/trusted_header.py` | New provider: accept forwarded identity |
| 4 | `libs/wip-auth/src/wip_auth/config.py` | Add `WIP_AUTH_TRUST_PROXY_HEADERS` setting |
| 5 | `libs/wip-auth/src/wip_auth/middleware.py` | Register TrustedHeaderProvider when enabled |
| 6 | `scripts/setup.sh` | Generate Caddyfile with app OIDC config |
| 7 | Tests | Verify header forwarding, verify spoofing is blocked without API key |

**Estimated scope:** ~150 lines of new code across 5 files. Most complexity is in Caddyfile configuration.

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

### Key Changes from Existing Design

The namespace-authorization design assumes services validate permissions themselves via Registry calls. With Phase 1's gateway headers, we can add a **faster enforcement point**:

```
Gateway (Caddy)                    App (@wip/proxy)                 WIP Service
       │                                    │                             │
  OIDC login ✅                              │                             │
  Inject X-WIP-User                          │                             │
       ├──────────────────────────────────►  │                             │
       │                           Forward identity + API key              │
       │                                    ├────────────────────────────► │
       │                                    │              check_namespace_permission()
       │                                    │              (wip-auth, cached)
       │                                    │                     ✅ or 403│
```

Permission checks happen in wip-auth (service-side), not at the gateway. The gateway only handles authentication. This keeps the architecture clean — Caddy doesn't need to know about namespaces.

### Enforcement Rollout

Phase 2 activates the existing `check_namespace_permission()` calls. Services already have the function — it just needs to be called from route handlers. See `namespace-authorization.md` Session 2 for the per-service integration plan.

### Single-User Compatibility

The `wip-admins` superadmin bypass ensures single-user deployments are unaffected. The default admin user and legacy API key are both in `wip-admins` — they skip all permission checks.

---

## Phase 3: Per-User Audit Trail — Who Did What?

**Goal:** Every create, update, and delete in WIP records which user performed it, even when the request came through an app.

**Prerequisite:** Phase 1 (user identity forwarded to WIP)

### Current State

WIP already tracks `created_by` and `updated_by` on documents, templates, and terminologies. But for app-originated requests, these fields contain the API key name (e.g., `apikey:clintrial-app`), not the actual user.

### Change

With Phase 1's forwarded identity headers, wip-auth resolves the user identity. The `identity_string` used for audit becomes `user:peter@wip.local` instead of `apikey:clintrial-app`.

This is mostly free once Phase 1 is done. The remaining work:

| Step | Change |
|------|--------|
| 1 | Verify `identity_string` uses forwarded user when available |
| 2 | NATS events include `changed_by` with user identity (already plumbed) |
| 3 | Reporting-sync preserves `changed_by` in PostgreSQL (already does this) |
| 4 | Console: show actual user in audit columns, not API key name |

### Audit in Events

NATS events already carry `changed_by`. Once the user identity flows through, events become:

```json
{
    "event_type": "document.updated",
    "changed_by": "user:peter@wip.local",
    "document": { ... }
}
```

Instead of:

```json
{
    "event_type": "document.updated",
    "changed_by": "apikey:clintrial-app",
    "document": { ... }
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

### 4B: Stand-Alone App Auth

Apps deployed outside the WIP gateway (e.g., a public-facing web app, a mobile app) can't rely on Caddy's OIDC. They need to authenticate users themselves.

**Approach:** The app implements its own OIDC flow against the same Dex instance (or any OIDC provider). The app's backend validates the JWT and forwards identity headers to WIP via `@wip/proxy`.

```typescript
// Stand-alone mode: app validates JWT itself
app.use('/wip', wipProxy({
  baseUrl: WIP_BASE_URL,
  apiKey: WIP_API_KEY,
  // App sets X-WIP-User from its own auth middleware
  identityFromRequest: (req) => ({
    user: req.auth.email,       // From app's own JWT validation
    groups: req.auth.groups,
  }),
}))
```

This works because WIP's trusted header provider doesn't care whether Caddy or the app set the headers — it only requires a valid API key alongside them.

### 4C: WIP as Auth Provider

The boldest option: WIP itself becomes an OIDC-compatible auth provider. Apps authenticate against WIP, which manages users, issues tokens, and enforces permissions.

This replaces Dex entirely. WIP's Registry (which already manages namespaces and API keys) extends to manage users.

**Pros:** Single source of truth for identity. Apps get auth for free. No external dependency.
**Cons:** Significant implementation effort. OIDC compliance is non-trivial. Dex already does this well.

**Recommendation:** Defer. Dex (or any external OIDC provider) handles the hard parts of token issuance, key rotation, and protocol compliance. WIP should consume identity, not produce it. Revisit only if Dex becomes a maintenance burden.

---

## Phase Summary

| Phase | Goal | Prerequisite | Scope |
|-------|------|-------------|-------|
| **1** | **No anonymous app access** | App Gateway (Phase 2 in `app-gateway.md`) | ~150 lines, 5 files |
| **2** | Per-namespace permissions | Phase 1 | Already designed (`namespace-authorization.md`), ~200 lines |
| **3** | Per-user audit trail | Phase 1 | Mostly free — verify identity flows through |
| **4A** | Per-app user isolation | Phase 2 | Design needed — Dex connectors or app-scoped grants |
| **4B** | Stand-alone app auth | Phase 1 | ~50 lines in `@wip/proxy` |
| **4C** | WIP as auth provider | All | Major effort — deferred |

## Dependency Graph

```
Phase 1: Gateway Auth
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

1. **Session management.** Caddy's OIDC plugin handles sessions via cookies. What's the session lifetime? Should it match Dex token expiry (15 min) or be longer with silent refresh? Console currently uses 15-min tokens with automatic refresh — apps should match.

2. **Logout.** If a user logs out of one app, do they log out of all apps? With shared Caddy sessions, probably yes (single sign-out). Is this desired?

3. **API key + user identity coexistence.** In Phase 1, requests carry both an API key (service auth) and user identity (from headers). Should the API key's permissions constrain the user's? E.g., if the API key is restricted to namespace `dnd`, should a `wip-admins` user be limited to `dnd` through that app? **Probably yes** — the API key represents the app's scope, and the user's permissions are intersected with it.

4. **Header trust boundary.** `X-WIP-User` headers are only trusted when accompanied by a valid API key. But what if an attacker compromises an API key? They can impersonate any user. Mitigation: API keys for app proxies should have limited namespace scope, and key rotation should be easy (it already is — see `docs/security/key-rotation.md`).

5. **Mobile / native apps.** These can't use cookie-based sessions. They need token-based auth (OIDC authorization code flow with PKCE). This is a Phase 4B concern, but worth noting that the architecture supports it — `@wip/proxy` already forwards tokens.

## Relationship to Existing Designs

| Document | Relationship |
|----------|-------------|
| `app-gateway.md` | Phase 1 here = Phase 4 there. Gateway routing (Phases 2-3 there) is a prerequisite. |
| `namespace-authorization.md` | Phase 2 here = that entire document. Already designed in detail. |
| `authentication.md` | Reference doc for current auth. Needs updating after each phase. |
| `security/key-rotation.md` | API key management. Relevant to Phase 1 (app API keys) and Phase 4 (header trust). |
