# Design: Image-Based Distribution

**Status:** High-level design (needs fireside talk to finalize)
**Date:** 2026-04-01
**Related:** `distributed-deployment.md`, `distributable-app-format.md`

## Vision

All WIP services and constellation apps ship as container images on a registry. Deploying WIP or an app means pulling an image and providing configuration — no git clone, no build tools, no source code.

```
New WIP instance:       docker compose pull && docker compose up
Deploy an app:          add 4 lines to compose, docker compose up
Upgrade WIP:            change image tags, docker compose up
Roll back:              change image tags back, docker compose up
```

This is the organizing principle for deployment, distribution, and lifecycle management. It subsumes or partially replaces several roadmap items:
- App Development & Deployment Framework (item 8) — Dockerfile template, compose integration, deploy CLI
- Container Runtime Support (item 10) — if images are the distribution unit, Docker/Podman compatibility is validated by definition
- K8s Remaining (item 11) — Helm/Kustomize packaging is the K8s flavour of this
- Guides (item 12, partially) — installation guide becomes "pull and run"

---

## 1. Container Registry

**Recommendation:** Gitea built-in package registry.

Gitea (1.19+) supports OCI container images natively. WIP already runs Gitea for git hosting and CI. The registry is enabled by default — no new infrastructure.

**Image naming convention:**
```
gitea.local:3000/peter/world-in-a-pie/wip-registry:1.4.2
gitea.local:3000/peter/world-in-a-pie/wip-def-store:1.4.2
gitea.local:3000/peter/world-in-a-pie/wip-document-store:1.4.2
...
gitea.local:3000/peter/clintrial-explorer:0.2.0     # app image
```

**UI:** Gitea provides a package browser under each repository — image list, tags, pull commands, size. No separate registry UI needed.

**Alternatives for later:**
- **Harbor** — vulnerability scanning, replication across registries, RBAC. Worth it if WIP gains external users.
- **GHCR** — images are already mirrored to GitHub. Free for public images. Good for distribution to others outside the local network.

---

## 2. WIP Core Service Images

### Current State

WIP services already run in containers via Podman Compose. Each service has a build context in `docker-compose/base.yml`:

| Service | Base Image | Notes |
|---------|-----------|-------|
| wip-registry | python:3.12-slim | FastAPI + pymongo |
| wip-def-store | python:3.12-slim | FastAPI + pymongo |
| wip-template-store | python:3.12-slim | FastAPI + pymongo |
| wip-document-store | python:3.12-slim | FastAPI + pymongo + boto3 |
| wip-reporting-sync | python:3.12-slim | FastAPI + psycopg2 + nats |
| wip-ingest-gateway | python:3.12-slim | FastAPI + nats |
| wip-mcp-server | python:3.12-slim | MCP protocol |
| wip-console | node:20-alpine (build) + caddy:2-alpine (serve) | Vue 3 SPA |

### Shared Base Image

All Python services share the same core dependencies (FastAPI, uvicorn, pymongo, wip-auth). A shared base image eliminates redundant pip installs:

```
wip-base:python3.12
├── FastAPI, uvicorn, pydantic
├── pymongo, motor
├── wip-auth (shared library)
└── common utilities
```

Each service image builds FROM `wip-base` and adds only its specific dependencies. This also speeds up CI — the base image changes rarely.

### Dockerfiles

Each service gets a standalone Dockerfile (not just a compose build context) so it can be built and pushed independently:

```
components/registry/Dockerfile
components/def-store/Dockerfile
components/template-store/Dockerfile
components/document-store/Dockerfile
components/reporting-sync/Dockerfile
components/ingest-gateway/Dockerfile
components/mcp-server/Dockerfile
ui/wip-console/Dockerfile
```

### Infrastructure Images

MongoDB, PostgreSQL, NATS, MinIO, Caddy, and Dex use upstream images directly. These are NOT custom-built — they're referenced in compose/K8s manifests with version pinning:

```yaml
services:
  mongodb:
    image: mongo:7.0
  postgres:
    image: postgres:16-alpine
  nats:
    image: nats:2.10-alpine
  minio:
    image: minio/minio:latest
  caddy:
    image: caddy:2-alpine
  dex:
    image: dexidp/dex:v2.38.0
```

---

## 3. App Images

See `distributable-app-format.md` for the full specification. Key points:

- Two-stage build: `node:20-alpine` (build) → `caddy:2-alpine` (serve) for frontend-only apps
- Apps with a backend (query scaffold) use `node:20-alpine` as the runtime image
- Configuration via environment variables, injected at container startup
- Bootstrap script creates WIP data model (terminologies, templates) on first start
- `app-manifest.json` for gateway integration

### Scaffold Dockerfile Template

`create-app-project.sh` generates a Dockerfile in the app project. For the query preset:

```dockerfile
# Build stage
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

# Production stage
FROM node:20-alpine
WORKDIR /app
COPY --from=build /app/dist ./dist
COPY --from=build /app/server ./server
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/package.json ./
COPY data-model/ ./data-model/
COPY app-manifest.json ./
EXPOSE 3001
CMD ["node", "server/index.js"]
```

---

## 4. Configuration Strategy

All configuration is external to the image. Nothing is baked in.

### When to Use What

| Mechanism | Use For | Examples |
|-----------|---------|---------|
| **Environment variables** | Simple key-value settings | `WIP_AUTH_TRUST_PROXY_HEADERS=true`, `MONGO_URI`, `API_KEY` |
| **ConfigMaps** (K8s) / **bind mounts** (Compose) | Multi-line config files | Caddyfile, Dex config YAML, NATS config |
| **Secrets** (K8s) / **env_file** (Compose) | Credentials | API keys, DB passwords, OIDC client secrets, TLS certs |
| **Helm values.yaml** / **`.env` file** | User-facing knobs | The single file users edit to configure everything |

### Configuration Flow

```
User edits          →    Generated into    →    Consumed by service
─────────────────────────────────────────────────────────────────
.env (Compose)           env vars               all services
values.yaml (Helm)       ConfigMaps + Secrets   all services
                         env vars via Helm
```

### What Needs to Become Configurable

Most WIP services already read configuration from env vars. Gaps to close:

| Config | Current | Needed |
|--------|---------|--------|
| Caddyfile | Generated by `setup.sh`, file on disk | ConfigMap or template in Helm chart |
| Dex config | `config/dex/config.yaml`, file on disk | ConfigMap with client list templated from values |
| NATS config | Mostly defaults | ConfigMap if JetStream tuning needed |
| API key | `.env` file | K8s Secret, referenced by all service Deployments |
| TLS certs | Caddy auto-generates internal certs | K8s Secret for provided certs, or cert-manager |

---

## 5. Dev/Prod Environments

### Podman Compose (Single Host)

Two compose files, same images, different `.env`:

```
docker-compose.yml          # base (shared services)
docker-compose.prod.yml     # production overrides (resource limits, restart: always)
docker-compose.dev.yml      # dev overrides (debug ports, volume mounts for code)
```

### Kubernetes (Cluster)

Two K8s namespaces:

```
wip          → production images (pinned tags: wip-registry:1.4.2)
wip-dev      → development images (latest or branch tags: wip-registry:develop)
```

Each namespace has its own:
- ConfigMaps (different Caddyfile, different Dex clients)
- Secrets (different API keys, different DB credentials)
- PersistentVolumeClaims (separate data)
- Service endpoints (same DNS names, namespace-scoped: `wip-registry.wip.svc`)

Apps follow the same pattern: `clintrial` (prod) and `clintrial-dev` (dev) namespaces.

---

## 6. Orchestration

### Phase 1: Kustomize

Kustomize is built into `kubectl` — no extra tooling. It uses overlays to patch base manifests:

```
k8s/
├── base/                    # Shared manifests
│   ├── kustomization.yaml
│   ├── registry.yaml        # Deployment + Service
│   ├── def-store.yaml
│   ├── ...
│   └── configmaps.yaml
├── overlays/
│   ├── dev/
│   │   ├── kustomization.yaml
│   │   └── patches/         # Dev-specific overrides
│   └── prod/
│       ├── kustomization.yaml
│       └── patches/         # Prod-specific overrides (replicas, resources)
```

Deploy: `kubectl apply -k k8s/overlays/prod`

**Pros:** No new tooling, simple mental model, works today.
**Cons:** No rollback, no dependency management, no packaging for distribution.

### Phase 2: Helm Chart

Graduate to Helm when WIP needs to be installable by others:

```bash
helm repo add wip https://gitea.local:3000/api/packages/peter/helm
helm install wip wip/world-in-a-pie --values my-values.yaml
```

A Helm chart provides:
- **`values.yaml`** — the single config surface for the entire stack
- **Rollback** — `helm rollback wip 3` restores the previous state
- **Dependencies** — MongoDB, PostgreSQL, NATS as sub-charts (Bitnami)
- **Packaging** — one artifact (`.tgz`) that contains all manifests + templates
- **Upgrades** — `helm upgrade wip wip/world-in-a-pie --values my-values.yaml`

**Sample `values.yaml` structure:**

```yaml
global:
  imageRegistry: gitea.local:3000/peter/world-in-a-pie
  imageTag: "1.4.2"
  apiKey: ""          # override via --set or Secret reference

mongodb:
  enabled: true       # set false if using external MongoDB
  auth:
    rootPassword: ""

postgresql:
  enabled: true
  auth:
    postgresPassword: ""

nats:
  enabled: true

services:
  registry:
    replicas: 1
    resources:
      limits:
        memory: 256Mi
  defStore:
    replicas: 1
  templateStore:
    replicas: 1
  documentStore:
    replicas: 1
    fileStorage:
      enabled: true
  reportingSync:
    enabled: true
  ingestGateway:
    enabled: false    # optional module

console:
  enabled: true

caddy:
  tls:
    mode: internal    # internal | provided | cert-manager
    # secretName: wip-tls   # for mode: provided

dex:
  enabled: true
  clients:
    - id: wip-console
      secret: ""
    - id: wip-apps
      secret: ""

apps: []
# - name: clintrial
#   image: gitea.local:3000/peter/clintrial-explorer:0.2.0
#   path: /apps/clintrial
#   env:
#     WIP_NAMESPACE: clintrial
```

---

## 7. Robustness

### Liveness and Readiness Probes

Every WIP service already has a `/health` endpoint. These map directly to K8s probes:

```yaml
livenessProbe:
  httpGet:
    path: /health
    port: 8001
  periodSeconds: 30
readinessProbe:
  httpGet:
    path: /health
    port: 8001
  initialDelaySeconds: 5
  periodSeconds: 10
```

Services should report "not ready" until database connections are established.

### Connection Resilience

| Connection | Behaviour | Action Needed |
|------------|-----------|---------------|
| MongoDB (pymongo) | Auto-reconnects with configurable retry | Already handled by driver |
| PostgreSQL (reporting-sync) | Connection pool with retry | Verify pool reconnect on connection drop |
| NATS JetStream | Durable subscriptions, auto-reconnect | Already handled — consumer position survives restart |
| Service-to-service HTTP | Stateless — each request independent | K8s Service DNS handles pod rescheduling |
| MinIO (S3 API) | Stateless per-request | No action needed |

### Startup Ordering

Services have dependencies (Registry must be up before Def-Store). In Compose, `depends_on` with `condition: service_healthy` handles this. In K8s, two approaches:

1. **Init containers** — wait for dependency health endpoint before starting main container
2. **Retry on startup** — services retry their dependency connections with backoff (simpler, already partially implemented)

Recommendation: retry with backoff. Init containers add complexity and the retry approach is more resilient to transient failures during normal operation, not just startup.

---

## 8. CI/CD Pipeline

### Gitea Actions Workflow

```
Trigger: push to develop (CI) or tag v* (release)

Steps:
1. Run tests (existing test.yaml)
2. Build images (parallel, one per service)
3. Push to Gitea registry
4. (Optional) Push to GHCR for external distribution
```

### Build Performance

| Concern | Mitigation |
|---------|-----------|
| Python pip install is slow | Shared base image (`wip-base`), layer caching |
| Building 8+ images | Parallel builds in CI |
| Image size | Multi-stage builds, `.dockerignore`, slim base images |
| ARM-only for now | Build natively on Pi runner (Gitea act_runner already runs on Pi) |

**Cross-platform (deferred):** Peter has an Intel/Windows machine available if amd64 images are needed. Multi-arch can be added later via `docker buildx` or separate CI runners per architecture. ARM-first is fine for now.

### Tagging Strategy

```
develop push  →  wip-registry:develop       (mutable, latest dev build)
v1.4.2 tag    →  wip-registry:1.4.2         (immutable release)
                  wip-registry:1.4            (mutable minor)
                  wip-registry:latest          (mutable, latest release)
```

---

## 9. Persistent Storage

Stateful services need persistent volumes:

| Service | Data | Compose | K8s |
|---------|------|---------|-----|
| MongoDB | Documents, terminologies, templates | Named volume | PVC (local-path or cloud storage class) |
| PostgreSQL | Reporting tables | Named volume | PVC |
| MinIO | Uploaded files | Named volume | PVC |
| NATS | JetStream state | Named volume | PVC |

**Backup:** `wip-toolkit export` already handles logical backups (namespace → ZIP). For volume-level backups, standard tools apply (mongodump, pg_dump, volume snapshots).

---

## 10. Phases

### Phase A: Dockerfiles + CI (Foundation)

- Write standalone Dockerfiles for all 8 WIP services
- Build shared `wip-base` Python image
- Gitea Actions workflow: build + push to Gitea registry on develop push
- Compose file variant that uses `image:` instead of `build:` (pull-only deployment)
- Scaffold generates Dockerfile for apps
- **Result:** `docker compose pull && docker compose up` works for WIP

### Phase B: Kustomize (K8s Dev/Prod)

- Kustomize base + dev/prod overlays
- ConfigMaps for Caddyfile, Dex config, NATS config
- Secrets for API keys, DB credentials
- PVC definitions for stateful services
- Readiness probes on all services
- **Result:** `kubectl apply -k k8s/overlays/prod` deploys WIP to a cluster

### Phase C: Helm Chart (Distribution)

- Package Kustomize manifests into a Helm chart
- `values.yaml` as the user-facing config surface
- Sub-chart dependencies (MongoDB, PostgreSQL, NATS from Bitnami)
- App integration (`apps:` list in values.yaml)
- Publish chart to Gitea Helm registry
- **Result:** `helm install wip wip/world-in-a-pie` installs WIP on any K8s cluster

### Phase D: App Lifecycle

- `wip-toolkit build-app` — build app image from scaffold
- `wip-toolkit deploy-app` — push image, add to compose/K8s
- App catalog in WIP Console (discover and enable apps)
- **Result:** Full app lifecycle from scaffold to production deployment

---

## Open Questions (For Fireside Talk)

1. **Gitea registry authentication** — do pulled images need auth, or is the registry open on the local network?
2. **Version coordination** — do all WIP services share a version (monorepo release), or are they versioned independently?
3. **Database migrations** — how are MongoDB/PostgreSQL schema changes applied during upgrades? Init container? Startup migration?
4. **Secrets management** — `.env` file for Compose is simple. K8s Secrets are base64-encoded but not encrypted by default. External secrets operator?
5. **Monitoring** — once services are images, adding Prometheus metrics scraping and Grafana dashboards is straightforward. Priority?
6. **WIP Console build-time config** — Vue bakes `VITE_*` vars at build time. The distributable-app-format doc solves this with runtime `/config.json` injection. Apply the same pattern to Console?
