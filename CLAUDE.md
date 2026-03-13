# Claude Session Context

Essential context for AI assistant sessions working on the World In a Pie (WIP) project.

For detailed documentation, see `docs/` directory.

---

## Project Overview

World In a Pie (WIP) is a universal template-driven document storage system designed to run on resource-constrained devices (Raspberry Pi) up to cloud deployments.

**Target:** Raspberry Pi 5 with 8GB RAM (Standard profile)

**Core Services:**
- **Registry** (port 8001) - ID generation and namespace management
- **Def-Store** (port 8002) - Terminologies and terms
- **Template-Store** (port 8003) - Document schemas
- **Document-Store** (port 8004) - Document storage and validation
- **Reporting-Sync** (port 8005) - MongoDB → PostgreSQL sync
- **WIP Console** (port 3000/8443) - Vue 3 + PrimeVue UI

---

## Current Status

### Completed (Phase 1-2)

All core services are implemented and working:

- [x] Registry - Namespace management, ID generation, bulk operations
- [x] Def-Store - Terminologies, terms, aliases, audit log, import/export, ontology relationships
- [x] Template-Store - Templates, fields, validation rules, inheritance
- [x] Document-Store - Documents, versioning, term validation, table view
- [x] Reporting-Sync - NATS consumer, PostgreSQL sync, batch operations, alerts
- [x] WIP Console - Full CRUD for all entities, OIDC support
- [x] Authentication - Dex OIDC + API key dual mode (wip-auth library)
- [x] Referential Integrity - Health checks, cascade warnings, reference protection

### Recently Added

- Bulk import tuning for 200k+ terms (sub-batching, throttling)
- Binary file storage via MinIO — full API, UI upload/list, reference tracking, orphan detection
- Semantic types — 7 types (email, url, lat/lon, percentage, duration, geo_point) with validation, reporting sync, UI
- Metabase optional deployment (`deploy/optional/metabase/`) with PostgreSQL reporting infrastructure
- Template draft mode — create templates with `status: "draft"`, cascading activation with full validation
- Bulk-first API convention — all write endpoints accept `List[Item]`, return `BulkResponse`; no single-entity write endpoints
- Ontology support — OBO Graph JSON import, typed relationships (is_a, part_of, etc.), relationship type validation, traversal queries, unified import view with auto-format detection
- MCP server — 30+ tools for AI-assisted development, 3 resources (conventions, data model, development guide), OpenAPI schema patching, stdio + SSE transport
- @wip/client — TypeScript client library with 6 service classes, typed error hierarchy, bulk abstraction, `templateToFormSchema()` utility
- @wip/react — React hooks library wrapping @wip/client with TanStack Query, 30+ hooks with sensible stale times, `WipProvider` context

---

## Roadmap

| Priority | Task | Status |
|----------|------|--------|
| 1 | Binary File Storage (MinIO) | ✅ Complete — Full CRUD API, UI, reference tracking, orphan detection |
| 2 | Semantic Types | ✅ Complete — 7 types, validation, reporting sync, UI hints |
| 3 | BI Dashboard (Metabase) | ✅ Optional deployment ready (`deploy/optional/metabase/`), pre-built dashboards pending |
| 4 | Ontology Support | ✅ Complete — OBO Graph JSON import, typed relationships, traversal, unified import UI |
| 5 | MCP Server | ✅ Complete — 30+ tools, 3 resources, OpenAPI schema patching, stdio + SSE transport |
| 6 | @wip/client + @wip/react | ✅ Complete — TypeScript client (6 services, error hierarchy, bulk abstraction), React hooks (TanStack Query) |
| 7 | File Upload (CSV/XLSX) | Pending |
| 8 | Event Replay | Design complete, implementation pending |
| 9 | Docker support | Test/document running with standard Docker |
| 10 | Rootful Podman | Test/document running with `sudo podman` |

See `docs/` for detailed specifications:
- `docs/architecture.md` - System architecture
- `docs/reporting-layer.md` - Reporting sync details
- `docs/authentication.md` - Auth configuration
- `docs/network-configuration.md` - **Network & OIDC setup (4 deployment scenarios)**
- `docs/production-deployment.md` - Production security guide
- `docs/data-models.md` - Document, template, term models
- `docs/design/ontology-support.md` - Ontology import, relationships, traversal
- `docs/uniqueness-and-identity.md` - **Uniqueness rules, Registry synonyms, ID generation**
- `docs/api-conventions.md` - **Bulk-first API convention, BulkResponse contract, client examples**
- `docs/mcp-server.md` - **MCP server tools, resources, and AI development workflow**
- `docs/design/event-replay.md` - Event replay for consumer onboarding

---

## Quick Start

### Automated Setup (Recommended)

```bash
# Development (localhost, default passwords)
./scripts/setup.sh --preset standard --localhost

# Network deployment (self-signed TLS)
./scripts/setup.sh --preset standard --hostname wip-pi.local

# Production (random secrets, auth enabled)
./scripts/setup.sh --preset standard --hostname wip-pi.local --prod -y

# Validate production security
./scripts/security/production-check.sh
```

### Manual Development

```bash
# 1. Start infrastructure
podman-compose -f docker-compose.infra.yml up -d

# 2. Start services (in order — uses docker-compose.yml + auto-generated override)
cd components/registry && podman-compose -f docker-compose.yml up -d --build
cd ../def-store && podman-compose -f docker-compose.yml up -d --build
cd ../template-store && podman-compose -f docker-compose.yml up -d --build
cd ../document-store && podman-compose -f docker-compose.yml up -d --build
cd ../reporting-sync && podman-compose -f docker-compose.yml up -d --build

# 3. Initialize namespaces (one-time)
curl -X POST http://localhost:8001/api/registry/namespaces/initialize-wip \
  -H "X-API-Key: dev_master_key_for_testing"

# 4. Start UI
cd ui/wip-console && podman-compose -f docker-compose.yml up -d --build
```

### Access Points

| Service | URL |
|---------|-----|
| WIP Console | https://localhost:8443 |
| API Docs | http://localhost:{port}/docs |
| Mongo Express | http://localhost:8081 (admin/admin) |
| MinIO Console | http://localhost:9001 |
| PostgreSQL | `podman exec -it wip-postgres psql -U wip -d wip_reporting` |

### Test Users (Dex OIDC)

| Email | Password | Group |
|-------|----------|-------|
| admin@wip.local | admin123 | wip-admins |
| editor@wip.local | editor123 | wip-editors |
| viewer@wip.local | viewer123 | wip-viewers |

**API Key:** `dev_master_key_for_testing`

---

## Key Technical Concepts

### Uniqueness & Identity

Two tiers of uniqueness:
- **Global:** Registry `entry_id` and namespace `prefix` are unique across the entire instance
- **Namespace-scoped:** All domain entities use `(namespace, ...)` compound keys

| Entity | Unique Key(s) | Scope |
|--------|--------------|-------|
| Namespace | `prefix` | Global |
| Registry Entry | `entry_id` | Global |
| Terminology | `(ns, terminology_id)`, `(ns, value)` | Namespace |
| Term | `(ns, term_id)`, `(ns, terminology_id, value)` | Namespace |
| Template | `(ns, template_id, version)`, `(ns, value, version)` | Namespace |
| Document | `(ns, document_id, version)` | Namespace |
| File | `(ns, file_id)` | Namespace |

**ID generation flow:** Service computes a composite key → calls Registry → Registry hashes the key and checks for existing entries → returns existing ID (upsert) or generates a new ID. For versioned entities (templates, documents), the entity_id stays the same across versions; `(entity_id, version)` is the true unique key.

**Document identity hash:** Template defines `identity_fields` (e.g., `["email"]`). Document-Store sends raw `identity_values` to Registry, which computes SHA-256 hash, injects it into the composite key `{namespace, identity_hash, template_id}`, and creates a synonym with the raw values. Same hash = same `document_id`, new version. No identity_fields = empty composite key = always new `document_id`.

**Registry synonyms:** An entry can have multiple composite keys (synonyms) that all resolve to the same canonical `entry_id`. The Registry is a standalone registrar; WIP services are its primary consumer. Synonyms enable cross-namespace linking, ID merging, and external/vendor ID mapping.

**Cross-namespace:** Same entity_id can exist in different namespaces (by design, for restore/migration). Uniqueness is enforced per-namespace, not globally.

See `docs/uniqueness-and-identity.md` for detailed rules, examples, and synonym use cases.

### Template Versioning

Templates support multi-version operation (e.g., different data model versions for different vendors):
- Update keeps the SAME `template_id`, increments `version`
- Multiple versions can be active simultaneously
- `extends_version` field pins inheritance to a specific parent version (None = latest)

### Template Draft Mode

Templates can be created with `status: "draft"` to skip reference validation:
- Enables circular dependencies and order-independent creation
- `POST /templates/{id}/activate` validates and activates (cascading to referenced drafts)
- All-or-nothing: if any template in the set fails, none activate
- See `docs/design/template-draft-mode.md` for details

### Term Storage

Documents store both original value AND resolved term_id:
```json
{
  "data": { "country": "United Kingdom" },
  "term_references": { "country": "T-000042" }
}
```

### Reporting Sync

Real-time sync from MongoDB → PostgreSQL via NATS events.
See `docs/reporting-layer.md` for architecture details.

---

## API Conventions

### Bulk-First: Every Write Endpoint is Bulk

All write endpoints (POST/PUT/DELETE) accept a JSON array and return `BulkResponse`. Single operations are just `[item]`. There are no single-entity write endpoints.

**Request:** `List[ItemRequest]` via `Body(...)`
**Response:** `BulkResponse { results: List[BulkResultItem], total, succeeded, failed }`

```python
# Creating one terminology
POST /api/def-store/terminologies
Body: [{"value": "GENDER", "label": "Gender"}]
→ {"results": [{"index": 0, "status": "created", "id": "..."}], "total": 1, "succeeded": 1, "failed": 0}

# Creating multiple
POST /api/def-store/terminologies
Body: [{"value": "GENDER", ...}, {"value": "COUNTRY", ...}]
→ {"results": [...], "total": 2, "succeeded": 2, "failed": 0}
```

**Key rules:**
- Write endpoints always return HTTP 200 — errors are per-item in `results[i].status == "error"`
- Never check HTTP status codes for duplicates/conflicts; check `result.status` and `result.error`
- `BulkResultItem` is subclassed per service for extra fields (e.g., `version`, `identity_hash`)
- Updates use PUT with entity ID in the body (not URL): `PUT /templates` with `[{"template_id": "...", ...}]`
- Deletes use DELETE with body: `DELETE /templates` with `[{"id": "..."}]`
- GET endpoints (single by ID, list with pagination) are NOT bulk — they stay as-is
- Pagination: default 50, max 100 for all services
- All list responses include `pages: int` (computed `ceil(total / page_size)`)

### Other Conventions

- Authentication: `X-API-Key` header or `Authorization: Bearer <token>`
- RESTful design with OpenAPI docs at `/docs`
- Data is never deleted, only soft-deleted (status: inactive). Exception: files support hard-delete (`DELETE /files/{id}/hard`) to reclaim MinIO storage after soft-delete.
- Upstream service errors return HTTP 502 (Bad Gateway)

---

## File Structure

```
WorldInPie/
├── CLAUDE.md              # This file
├── docs/                  # Detailed documentation
├── scripts/               # Setup and utility scripts
├── config/                # Configuration files
├── libs/wip-auth/         # Shared auth library (Python)
├── libs/wip-client/       # @wip/client — TypeScript client for apps (6 services, error hierarchy, bulk abstraction)
├── libs/wip-react/        # @wip/react — React hooks wrapping @wip/client (TanStack Query, 30+ hooks)
├── components/
│   ├── registry/          # ID & namespace management
│   ├── def-store/         # Terminologies & terms
│   ├── template-store/    # Document schemas
│   ├── document-store/    # Document storage
│   ├── reporting-sync/    # PostgreSQL sync
│   ├── ingest-gateway/    # Async ingestion via NATS JetStream
│   ├── mcp-server/        # MCP server — 30+ tools for AI-assisted development (stdio + SSE)
│   └── seed_data/         # Test data generation
├── deploy/
│   └── optional/          # Optional services (e.g., Metabase)
├── docker-compose/        # Compose overrides
├── k8s/                   # Kubernetes manifests
├── data/                  # Runtime data (volumes)
├── testdata/              # Test fixtures
├── WIP-Toolkit/           # CLI toolkit
└── ui/wip-console/        # Vue 3 + PrimeVue UI
```

---

## WIP Namespaces

| Namespace | ID Format | Service |
|-----------|-----------|---------|
| wip-terminologies | UUID7 | Def-Store |
| wip-terms | UUID7 | Def-Store |
| wip-templates | UUID7 | Template Store |
| wip-documents | UUID7 | Document Store |
| wip-files | UUID7 | Document Store |

---

## Running Tests

```bash
# Run tests inside container
podman exec -it wip-{service} bash -c \
  "pip install pytest pytest-asyncio httpx && pytest /app/tests -v"
```

---

## Seed Test Data

```bash
pip install faker requests
python scripts/seed_comprehensive.py --profile standard
```

Profiles: minimal (50 docs), standard (500 docs), full (2000 docs), performance (100k docs)

---

## Bulk Import Tuning

For large imports (100k+ terms), tune batch sizes to avoid timeouts:

```bash
# Via API parameters
POST /api/def-store/import-export/import?batch_size=1000&registry_batch_size=50
```

See `docs/bulk-import-tuning.md` for details.

---

## Environment Variables

Key auth variables:
```bash
WIP_AUTH_MODE=dual                    # api_key_only, jwt_only, dual
WIP_AUTH_LEGACY_API_KEY=...           # API key for service auth
WIP_AUTH_JWT_ISSUER_URL=...           # OIDC issuer
WIP_AUTH_JWT_JWKS_URI=...             # JWKS endpoint
```

See `config/profiles/*.env` for profile-specific settings.

---

## OIDC Configuration (Critical)

**When OIDC is enabled, these THREE values MUST be identical:**

| Config File | Variable | Example Value |
|-------------|----------|---------------|
| `config/dex/config.yaml` | `issuer` | `https://localhost:8443/dex` |
| `.env` | `WIP_AUTH_JWT_ISSUER_URL` | `https://localhost:8443/dex` |
| `.env` | `VITE_OIDC_AUTHORITY` | `https://localhost:8443/dex` |

**Mismatch causes 401 "Invalid token issuer" errors.**

**After changing `.env`, recreate containers (not just restart):**
```bash
# WRONG - env vars not reloaded
podman-compose restart

# CORRECT - containers pick up new env
podman-compose down && podman-compose up -d
```

See `docs/network-configuration.md` for all 4 deployment scenarios.
