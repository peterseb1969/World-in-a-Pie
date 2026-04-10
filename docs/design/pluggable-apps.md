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

## Migration Path

| Version | What changes |
|---------|-------------|
| **v1.0** (today) | Compose chunks, setup-wip.sh, manual bootstrap, per-app OIDC. Works but fragile. |
| **v1.5** | App Manager service, manifest-driven bootstrap, Caddy admin API for dynamic routes. Apps still need compose chunks but lifecycle is managed. |
| **v2.0** | Gateway auth replaces per-app OIDC. Console app store. Hot add/remove. No compose chunks — just image references. |
| **v3.0** | App marketplace. Version compatibility enforcement. Automatic updates. Multi-instance federation (install app on instance A from instance B's catalog). |

---

## Design Questions (for fireside)

1. **Container runtime API access.** The App Manager needs to start/stop containers. In compose mode: call `podman` CLI? In K8s: create Deployments via API? Or should the App Manager just update the compose file and let the user run `compose up`?

2. **Gateway auth vs per-app OIDC.** Gateway auth is simpler but means all apps share one login session. Is that acceptable? What about apps that need their own auth flow (e.g., an app that authenticates external users, not WIP users)?

3. **Data model versioning.** When an app updates and its schema changes, who mediates? The bootstrapper can detect incompatibilities — but who decides whether to proceed? The installer? The Console? Automatic for compatible changes?

4. **Multi-tenancy.** Can two instances of the same app run in different namespaces? (e.g., ClinTrial for Roche data in `roche-ct`, ClinTrial for public data in `public-ct`)

5. **App-to-app communication.** Can apps discover and talk to each other? Should they? Or should all inter-app communication go through WIP's data layer?
