# Pluggable Apps — Greenfield Design

**Status:** Vision document — unconstrained by v1.0 implementation  
**Author:** BE-YAC-20260409-1636  
**Context:** Every problem hit during the Pi deployment (prefix routing, OIDC per-app, TLS between containers, password sync, manual bootstrap) stems from one root cause: apps are configured alongside WIP at the infrastructure level. This document reimagines apps as managed entities within WIP.

---

## The Core Shift

**Today:** Apps are peers of WIP services — configured in compose files, wired through Caddy, authenticated independently via Dex. Adding an app means editing config files, regenerating Caddy routes, registering Dex clients, restarting services.

**Ideal:** Apps are resources managed by WIP, like documents or templates. WIP handles routing, authentication, namespace provisioning, and data model bootstrap. Adding an app is an API call. Removing one is another. No restarts.

---

## The Actors

| Actor | Role |
|-------|------|
| **WIP Platform** | Always running. Manages infrastructure, data, identity, routing. |
| **App Manager** | New WIP service. The app lifecycle controller. |
| **App** | A container image + manifest. Declares what it needs, WIP provides it. |
| **Console** | UI for humans. Installs apps, shows health, triggers backup/restore. |
| **Installer** | A human or script that tells WIP "install this app." |

---

## App Manifest

Baked into the image at `/app/wip-manifest.yaml`. The app's declaration of what it is and what it needs. WIP reads it — the app doesn't configure WIP, WIP configures itself.

```yaml
app:
  id: clintrial-explorer
  name: Clinical Trials Explorer
  description: Browse and analyze clinical trials from ClinicalTrials.gov
  version: 1.2.0
  icon: flask-conical
  requires_wip: ">=1.0.0"

routing:
  base_path: /apps/clintrial
  port: 3001
  health: /health

auth:
  mode: gateway            # WIP handles auth; app receives identity headers
  allowed_groups:           # optional — restrict to specific groups
    - wip-admins
    - wip-editors

namespace:
  name: clintrial
  isolation_mode: open
  create_if_missing: true

data_model:
  terminologies:
    - value: CT_COUNTRY
      label: Countries
      source: data-model/terminologies/country.yaml
    - value: CT_CONDITION
      label: Medical Conditions
      source: data-model/terminologies/condition.yaml
      extensible: true

  templates:
    - value: CT_TRIAL
      source: data-model/templates/ct_trial.yaml
      on_conflict: validate
    - value: CT_ORGANIZATION
      source: data-model/templates/ct_organization.yaml
      on_conflict: validate

  relationships:
    - source: data-model/relationships/condition_hierarchy.yaml

resources:
  cpu: "0.5"
  memory: 512Mi

lifecycle:
  ready_endpoint: /health
  shutdown_grace_seconds: 10
```

---

## App Lifecycle

```
                    install
    [available] ──────────────► [installing]
                                     │
                              bootstrap data model
                              provision namespace
                              create API key
                              add route
                                     │
                                     ▼
                               [running] ◄──── update (new image)
                                     │
                              deactivate │ uninstall
                                     │         │
                                     ▼         │
                              [inactive]       │
                                     │         │
                              uninstall        │
                                     │         │
                                     ▼         ▼
                              [uninstalled]
                              (data retained or archived)
```

Each transition is an API call to the App Manager. No file edits, no restarts.

---

## What the Backend Provides

### 1. App Manager Service (new)

The single point of control for app lifecycle. Runs as a WIP service.

**API:**
- `POST /apps/install` — pull image, read manifest, validate, bootstrap, activate
- `GET /apps` — list installed apps with health + state
- `GET /apps/{id}` — app detail (manifest, health, data stats)
- `POST /apps/{id}/deactivate` — remove route, keep data
- `POST /apps/{id}/activate` — re-add route
- `DELETE /apps/{id}` — uninstall (optionally archive data first)
- `POST /apps/{id}/update` — pull new image, validate schema compatibility, roll over
- `POST /apps/{id}/backup` — backup the app's namespace
- `POST /apps/{id}/restore` — restore from archive

**Internally, install does:**
1. Pull image, extract `/app/wip-manifest.yaml`
2. Validate `requires_wip` version compatibility
3. Create namespace (if `create_if_missing`)
4. Provision namespace-scoped API key
5. Run data model bootstrapper (create terminologies, templates)
6. Start the container (via container runtime API)
7. Wait for health check
8. Add Caddy route (via Caddy admin API)
9. Register in app registry (MongoDB collection)

### 2. Gateway Authentication

**The biggest simplification.** Apps don't talk to Dex. Apps don't manage sessions. Apps don't deal with OIDC at all.

The flow:
1. Browser hits `https://wip.local:8443/apps/clintrial/`
2. Caddy routes to WIP's **auth gateway** (a middleware layer, not the app)
3. Auth gateway checks the session cookie (set by the Console's Dex login)
4. If authenticated: forward to the app with headers:
   ```
   X-WIP-User: admin@wip.local
   X-WIP-User-ID: admin-001
   X-WIP-Groups: wip-admins
   X-WIP-Auth-Method: gateway_oidc
   ```
5. If not authenticated: redirect to Dex login, then back to the original URL
6. The app reads identity from headers. No OIDC library, no session management, no cookies.

**What this eliminates:**
- Per-app Dex client registration
- Per-app session cookies
- `trust proxy` configuration
- Cookie path conflicts
- OIDC state parameter handling
- `NODE_TLS_REJECT_UNAUTHORIZED` hacks
- The entire CASE-38 saga

**The app's auth code becomes:**
```typescript
function getUser(req: Request) {
  return {
    email: req.headers['x-wip-user'],
    groups: (req.headers['x-wip-groups'] || '').split(','),
    method: req.headers['x-wip-auth-method'],
  }
}
```

### 3. Dynamic Caddy Routing

Caddy has a REST admin API on port 2019. Instead of generating Caddyfiles:

```bash
# Add a route
curl -X POST http://localhost:2019/config/apps/http/servers/srv0/routes \
  -d '{"handle": [{"handler": "reverse_proxy", "upstreams": [{"dial": "wip-clintrial:3001"}]}],
       "match": [{"path": ["/apps/clintrial/*"]}]}'

# Remove a route
curl -X DELETE http://localhost:2019/config/apps/http/servers/srv0/routes/3
```

No Caddy restart. No Caddyfile regeneration. Routes appear and disappear in milliseconds.

### 4. Data Model Bootstrapper

A WIP service (or part of App Manager) that reads the manifest's `data_model` section and idempotently creates the app's schema:

- **Terminologies:** Create with terms. If exists: compare term lists, add missing, warn on conflicts.
- **Templates:** Create with `on_conflict=validate`. Compatible changes (added optional fields) auto-version. Incompatible changes (removed fields, changed types) block the install with a diff.
- **Relationships:** Create ontology relationships from the manifest.

This replaces:
- Manual `createTerminology` / `createTemplate` calls from app code
- The "bootstrap" phase that each APP-YAC has to implement independently
- The risk of apps creating incompatible schemas silently

The bootstrapper runs **before** the app container starts. If bootstrap fails (schema incompatibility), the app doesn't start — fail fast, not fail silent.

### 5. Per-App API Keys

The App Manager provisions a namespace-scoped API key for each app:

```
Key: app-clintrial-explorer-a7b3c9d2
Scopes: [clintrial]
Permissions: write
```

The key is injected into the app container as `WIP_API_KEY`. The app never sees the master key. If the app is uninstalled, the key is revoked. If the app is deactivated, the key is suspended.

This replaces:
- Sharing the master `API_KEY` with all apps
- Manual key management
- The risk of apps accessing each other's namespaces

---

## What the Console Does

### App Store View

```
┌─────────────────────────────────────────────┐
│  Installed Apps                              │
├─────────────────────────────────────────────┤
│  ● ClinTrial Explorer  v1.2.0   [healthy]  │
│    /apps/clintrial  •  228k docs  •  3.2 GB │
│    [Backup] [Deactivate] [Update]           │
│                                              │
│  ● React Console       v0.9.0   [healthy]  │
│    /apps/rc  •  admin UI                     │
│    [Deactivate]                              │
│                                              │
│  ○ DnD Compendium      v0.1.0   [inactive] │
│    /apps/dnd  •  1,384 docs  •  45 MB       │
│    [Activate] [Uninstall] [Backup]          │
├─────────────────────────────────────────────┤
│  [+ Install App]                             │
└─────────────────────────────────────────────┘
```

### Install Flow

1. User provides image reference (e.g., `gitea.local:3000/peter/clintrial-explorer:1.2.0`)
2. Console calls `POST /apps/install`
3. App Manager pulls image, reads manifest, shows the user what will be created
4. User confirms
5. App Manager bootstraps, starts, routes
6. App appears in the list — no page reload needed (WebSocket health updates)

### Backup/Restore Per App

The backup engine from this session becomes app-aware:

- **Backup:** backs up the app's namespace (terminologies, templates, documents, files, registry entries)
- **Restore:** restores into a target WIP instance — the App Manager handles namespace creation and API key provisioning
- **Migrate:** backup from instance A, install manifest on instance B, restore data

---

## What the App Does

### Minimal responsibilities:

1. **Bake the manifest** into the image at `/app/wip-manifest.yaml`
2. **Serve HTTP** on the declared port, under `APP_BASE_PATH`
3. **Read identity** from `X-WIP-*` headers (no OIDC, no sessions)
4. **Use WIP APIs** via `@wip/client` with the auto-provisioned `WIP_API_KEY`
5. **Respond to health** at the declared endpoint
6. **Declare the data model** in the manifest — don't create it programmatically

### What apps DON'T do:

- ~~Configure Caddy~~
- ~~Register Dex clients~~
- ~~Manage OIDC sessions~~
- ~~Handle cookies~~
- ~~Bootstrap terminologies/templates in code~~
- ~~Know about TLS, proxy settings, or container networking~~

---

## The Internal HTTP Question Disappears

With gateway auth, apps don't proxy to WIP through Caddy at all. The `@wip/client` in the app talks directly to WIP services:

```
WIP_REGISTRY_URL=http://wip-registry:8001
WIP_DEF_STORE_URL=http://wip-def-store:8002
...
```

Or through a single internal gateway (the App Manager could provide this):

```
WIP_API_URL=http://wip-app-manager:8010/api
```

No TLS between containers. No Caddy in the internal path. No `NODE_TLS_REJECT_UNAUTHORIZED`. The browser talks to Caddy (HTTPS). Containers talk to each other (HTTP). Clean separation.

---

## App-Scoped Authorization

The gateway resolves a unified permission model: `(user/group, app, namespace, role)`.

The existing `NamespaceGrant` model extends naturally:

```
NamespaceGrant:
  namespace: clintrial
  subject: wip-editors           # group
  subject_type: group
  permission: write
  app_scope: clintrial-explorer  # NEW — null means "all apps"
```

The gateway evaluates all matching grants (user + group, direct + inherited) for the specific app and injects the resolved role:

```
X-WIP-User: editor@wip.local
X-WIP-Groups: wip-editors
X-WIP-App-Role: write
X-WIP-Namespaces: clintrial(write), wip(read)
```

Example grant table:

| Subject | Type | App | Namespace | Permission |
|---------|------|-----|-----------|------------|
| wip-admins | group | *(all)* | *(all)* | admin |
| wip-editors | group | clintrial-explorer | clintrial | write |
| wip-editors | group | react-console | *(all)* | admin |
| clinical-viewers | group | clintrial-explorer | clintrial | read |
| clinical-viewers | group | dnd-compendium | — | *(no grant = no access)* |

Apps never see the grant logic — they receive the resolved role in headers. The Console manages grants via the existing Registry API (extended with `app_scope`). No restart, no config files.

---

## Users as WIP Data, Not Infrastructure Config

**Today:** Users live in Dex's config file as static passwords with bcrypt hashes. Adding a user means editing `config.yaml` and restarting Dex. Password changes require regenerating hashes. WIP has no control over user management.

**Ideal:** Users are a WIP resource stored in MongoDB, managed via API, visible in the Console. Dex (or its successor) is just the OIDC protocol layer — it delegates credential validation to WIP.

### Option A: Keep Dex, Add a WIP User Backend (v1.5)

Dex stays as the OIDC protocol handler. A custom connector delegates authentication to WIP's Registry:

```
Browser → Dex login page → Dex connector → POST /api/registry/auth/validate
  → Registry checks MongoDB users collection → returns user + groups
  → Dex issues JWT with groups claim
```

Users are managed via WIP API:
- `POST /api/registry/users` — create user
- `PATCH /api/registry/users/{id}` — update profile, change password
- `DELETE /api/registry/users/{id}` — deactivate
- `GET /api/registry/users` — list (admin only)

The Console gets a user management page. No config files, no Dex restarts, no bcrypt hashing in shell scripts.

**MongoDB schema:**
```
users collection:
  email: "admin@wip.local"
  username: "admin"
  display_name: "Admin User"
  password_hash: "$2b$10$..."   # bcrypt
  groups: ["wip-admins"]
  status: active
  created_at: ...
  last_login: ...
```

**What this eliminates:**
- Dex `config.yaml` static passwords section
- `setup-wip.sh` bcrypt password generation
- The CASE-39 password desync problem
- Dex restart on user changes
- The Dex version sensitivity (v2.38 vs v2.45 for group claims — WIP controls the JWT content)

### Option B: Replace Dex Entirely (v2.0)

WIP implements the OIDC endpoints itself:

- `/.well-known/openid-configuration`
- `/authorize` (authorization code flow)
- `/token` (code exchange)
- `/keys` (JWKS for JWT validation)
- `/userinfo`

Users in MongoDB. JWTs signed by WIP's own keys. WIP already has JWT validation (`wip-auth`), key concepts, and the user/group model. The gap is token issuance and the authorization code flow.

**What this eliminates:** Dex container, Dex config, Dex SQLite volume, Dex version management — one fewer moving part in the stack.

**What this costs:** Implementing OIDC correctly is non-trivial. But libraries exist (`node-oidc-provider` for Node, `authlib` for Python). And WIP only needs to support the authorization code flow with PKCE — not the full OIDC spec.

### Option C: Defer External Identity (v2.0+)

For organizations with existing identity providers (Active Directory, Okta, Google Workspace), WIP should federate — not replace. Dex's connector model handles this well. The key is that WIP's internal user model is the canonical source of groups and app-scoped permissions, even when the actual authentication happens externally.

```
Google → Dex connector → WIP maps external identity to internal groups → JWT
```

The WIP user record becomes a profile enrichment layer:
- External auth: "this person is authenticated by Google"
- WIP user record: "this person is in groups [wip-editors, clinical-viewers] and has these app grants"

### Recommendation

**v1.5:** Option A — MongoDB users with Dex connector. Biggest user-facing improvement for the least risk.

**v2.0:** Evaluate Option B. If WIP's OIDC needs remain simple (one flow, internal users only), it's worth eliminating Dex. If federation with external IdPs is needed, keep Dex as the protocol layer and focus on Option C.

**Principle:** Users are data, not config. Everything about a user — credentials, groups, permissions, app access — should be queryable via API and manageable via Console.

---

## Migration Path

| Version | Apps | Auth | Users | Routing |
|---------|------|------|-------|---------|
| **v1.0** (today) | Compose chunks, setup-wip.sh, manual bootstrap | Per-app OIDC via Dex | Static in Dex config.yaml | Generated Caddyfile |
| **v1.5** | App Manager, manifest-driven bootstrap | Per-app OIDC (but managed by App Manager) | MongoDB via WIP API, Dex connector | Caddy admin API (dynamic) |
| **v2.0** | Hot add/remove via API, Console app store | Gateway auth — apps receive headers only | WIP-native (Dex optional for federation) | Caddy admin API |
| **v3.0** | Marketplace, version enforcement, auto-updates | App-scoped RBAC, federated IdP support | Full user lifecycle, external IdP mapping | Service mesh / discovery |

---

## Design Questions (for fireside)

1. **Container runtime API access.** The App Manager needs to start/stop containers. In compose mode: call `podman` CLI? In K8s: create Deployments via API? Or should the App Manager just update the compose file and let the user run `compose up`?

2. **Gateway auth vs per-app OIDC.** Gateway auth is simpler but means all apps share one login session. Is that acceptable? What about apps that need their own auth flow (e.g., an app that authenticates external users, not WIP users)?

3. **Data model versioning.** When an app updates and its schema changes, who mediates? The bootstrapper can detect incompatibilities — but who decides whether to proceed? The installer? The Console? Automatic for compatible changes?

4. **Multi-tenancy.** Can two instances of the same app run in different namespaces? (e.g., ClinTrial for Roche data in `roche-ct`, ClinTrial for public data in `public-ct`)

5. **App-to-app communication.** Can apps discover and talk to each other? Should they? Or should all inter-app communication go through WIP's data layer?

6. **User management scope.** Should WIP own the full user lifecycle (create, password reset, disable, delete) or should it always delegate to an external IdP? If WIP owns users, it becomes a mini identity provider — is that a feature or a liability?
