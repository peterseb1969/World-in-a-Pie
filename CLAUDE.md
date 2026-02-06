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
- MinIO file storage infrastructure (Phase 1)

---

## Roadmap

| Priority | Task | Status |
|----------|------|--------|
| 1 | Binary File Storage | Phase 1 ✅, Phases 2-9 pending |
| 2 | File Upload (CSV/XLSX) | Pending |
| 3 | BI Dashboard (Metabase) | Pending |
| 4 | Semantic Types | Planning complete, implementation pending |
| 5 | Event Replay | Design complete, implementation pending |

See `docs/` for detailed specifications:
- `docs/architecture.md` - System architecture
- `docs/reporting-layer.md` - Reporting sync details
- `docs/authentication.md` - Auth configuration
- `docs/data-models.md` - Document, template, term models
- `docs/design/event-replay.md` - Event replay for consumer onboarding

---

## Quick Start

### Automated Setup (Recommended)

```bash
# Auto-detect platform and deploy
./scripts/setup.sh

# Or specify profile
./scripts/setup.sh --profile mac --network localhost
./scripts/setup.sh --profile pi-standard --hostname wip-pi.local
```

### Manual Development

```bash
# 1. Start infrastructure
podman-compose -f docker-compose.infra.yml up -d

# 2. Start services (in order)
cd components/registry && podman-compose -f docker-compose.dev.yml up -d
cd ../def-store && podman-compose -f docker-compose.dev.yml up -d
cd ../template-store && podman-compose -f docker-compose.dev.yml up -d
cd ../document-store && podman-compose -f docker-compose.dev.yml up -d
cd ../reporting-sync && podman-compose -f docker-compose.dev.yml up -d

# 3. Initialize namespaces (one-time)
curl -X POST http://localhost:8001/api/registry/namespaces/initialize-wip \
  -H "X-API-Key: dev_master_key_for_testing"

# 4. Start UI
cd ui/wip-console && podman-compose -f docker-compose.dev.yml up -d
```

### Access Points

| Service | URL |
|---------|-----|
| WIP Console | https://localhost:8443 or http://localhost:3000 |
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
podman exec -it wip-{service}-dev bash -c \
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
