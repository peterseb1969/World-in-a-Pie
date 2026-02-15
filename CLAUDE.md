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
- [x] Def-Store - Terminologies, terms, aliases, audit log, import/export
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

---

## Roadmap

| Priority | Task | Status |
|----------|------|--------|
| 1 | Binary File Storage (MinIO) | ✅ Complete — Full CRUD API, UI, reference tracking, orphan detection |
| 2 | Semantic Types | ✅ Complete — 7 types, validation, reporting sync, UI hints |
| 3 | BI Dashboard (Metabase) | ✅ Optional deployment ready (`deploy/optional/metabase/`), pre-built dashboards pending |
| 4 | File Upload (CSV/XLSX) | Pending |
| 5 | Event Replay | Design complete, implementation pending |
| 6 | Docker support | Test/document running with standard Docker |
| 7 | Rootful Podman | Test/document running with `sudo podman` |

See `docs/` for detailed specifications:
- `docs/architecture.md` - System architecture
- `docs/reporting-layer.md` - Reporting sync details
- `docs/authentication.md` - Auth configuration
- `docs/network-configuration.md` - **Network & OIDC setup (4 deployment scenarios)**
- `docs/production-deployment.md` - Production security guide
- `docs/data-models.md` - Document, template, term models
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

### Document Identity & Versioning

Documents use identity fields (defined in template) to determine uniqueness:
- Same identity_hash → new version of existing document
- Different identity_hash → new document

```json
{
  "template_id": "TPL-000001",
  "identity_fields": ["email"],  // SHA-256 hash of these fields
  "data": { "email": "john@example.com", ... }
}
```

### Template Versioning

Templates support multi-version operation (for gradual migration):
- Update creates NEW template_id with incremented version
- Original template remains active
- Documents reference specific template_id

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

- All endpoints support bulk operations
- Authentication: `X-API-Key` header or `Authorization: Bearer <token>`
- RESTful design with OpenAPI docs at `/docs`
- Data is never deleted, only soft-deleted (status: inactive)

---

## File Structure

```
WorldInPie/
├── CLAUDE.md              # This file
├── docs/                  # Detailed documentation
├── scripts/               # Setup and utility scripts
├── config/                # Configuration files
├── libs/wip-auth/         # Shared auth library
├── components/
│   ├── registry/          # ID & namespace management
│   ├── def-store/         # Terminologies & terms
│   ├── template-store/    # Document schemas
│   ├── document-store/    # Document storage
│   ├── reporting-sync/    # PostgreSQL sync
│   └── seed_data/         # Test data generation
└── ui/wip-console/        # Vue 3 + PrimeVue UI
```

---

## WIP Namespaces

| Namespace | ID Format | Service |
|-----------|-----------|---------|
| wip-terminologies | TERM-XXXXXX | Def-Store |
| wip-terms | T-XXXXXX | Def-Store |
| wip-templates | TPL-XXXXXX | Template Store |
| wip-documents | UUID7 | Document Store |
| wip-files | FILE-XXXXXX | Document Store |

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
