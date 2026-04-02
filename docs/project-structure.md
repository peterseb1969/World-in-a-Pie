# Project Structure

This document defines the directory structure for World In a Pie (WIP), designed to keep components isolated and independently deployable.

---

## Design Principles

1. **Component Isolation**: Each service lives in its own directory with its own configuration
2. **Independent Deployment**: Services can be built and deployed independently
3. **Shared Infrastructure**: Common databases and message queue shared via docker-compose.infra.yml
4. **Shared Libraries**: Common authentication code in `libs/wip-auth`
5. **Clear Boundaries**: API contracts defined between services

---

## Directory Structure

```
world-in-a-pie/
│
├── README.md                              # Project overview
├── CLAUDE.md                              # AI assistant context and roadmap
│
├── docs/                                  # Documentation
│   ├── architecture.md
│   ├── api-conventions.md
│   ├── authentication.md
│   ├── components.md
│   ├── data-models.md
│   ├── production-deployment.md
│   ├── network-configuration.md
│   ├── uniqueness-and-identity.md
│   ├── reporting-layer.md
│   ├── mcp-server.md
│   ├── semantic-types.md
│   ├── bulk-import-tuning.md
│   ├── HOW-TO.md                         # Comprehensive curl examples
│   ├── project-structure.md              # This file
│   ├── glossary.md
│   ├── roadmap.md
│   ├── design/                           # Feature design documents
│   │   ├── ontology-support.md
│   │   ├── event-replay.md
│   │   ├── template-draft-mode.md
│   │   ├── namespace-scoped-data.md
│   │   ├── reference-fields.md
│   │   ├── template-reference-pinning.md
│   │   ├── distributed-deployment.md
│   │   ├── namespace-authorization.md
│   │   ├── natural-language-interface.md
│   │   ├── wip-tools-cli.md
│   │   └── wip-nano.md
│   ├── security/                         # Security documentation
│   │   ├── key-rotation.md
│   │   └── encryption-at-rest.md
│   ├── slash-commands/                   # Slash commands for app-building AI
│   └── guides/                           # Setup and operational guides
│
├── libs/                                  # Shared libraries
│   ├── wip-auth/                         # Python auth library (providers, middleware, identity)
│   │   ├── src/wip_auth/
│   │   └── tests/
│   ├── wip-client/                       # @wip/client TypeScript SDK
│   │   ├── src/
│   │   └── tests/
│   ├── wip-proxy/                        # @wip/proxy Express middleware
│   │   ├── src/
│   │   └── tests/
│   └── wip-react/                        # @wip/react hooks for WIP APIs
│       ├── src/
│       └── tests/
│
├── components/                            # Backend services
│   │
│   ├── registry/                         # ══════════════════════════════
│   │   │                                 # REGISTRY SERVICE (Port 8001)
│   │   │                                 # ID generation, namespaces
│   │   │                                 # ══════════════════════════════
│   │   ├── README.md
│   │   ├── docker-compose.override.yml
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── config/
│   │   ├── scripts/
│   │   │   └── seed_data.py
│   │   ├── src/
│   │   │   └── registry/
│   │   │       ├── __init__.py
│   │   │       ├── main.py               # FastAPI application
│   │   │       ├── api/
│   │   │       │   ├── namespaces.py
│   │   │       │   ├── entries.py
│   │   │       │   ├── synonyms.py
│   │   │       │   └── auth.py
│   │   │       ├── models/
│   │   │       └── services/
│   │   └── tests/
│   │
│   ├── def-store/                        # ══════════════════════════════
│   │   │                                 # DEF-STORE SERVICE (Port 8002)
│   │   │                                 # Terminologies & Terms
│   │   │                                 # ══════════════════════════════
│   │   ├── README.md
│   │   ├── docker-compose.override.yml
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── src/
│   │   │   └── def_store/
│   │   │       ├── __init__.py
│   │   │       ├── main.py
│   │   │       ├── api/
│   │   │       │   ├── terminologies.py
│   │   │       │   ├── terms.py
│   │   │       │   ├── validation.py
│   │   │       │   ├── import_export.py
│   │   │       │   └── auth.py
│   │   │       ├── models/
│   │   │       │   ├── terminology.py
│   │   │       │   ├── term.py
│   │   │       │   └── audit_log.py
│   │   │       └── services/
│   │   │           ├── registry_client.py
│   │   │           ├── terminology_service.py
│   │   │           └── import_export.py
│   │   └── tests/
│   │
│   ├── template-store/                   # ══════════════════════════════
│   │   │                                 # TEMPLATE STORE SERVICE (Port 8003)
│   │   │                                 # Document templates & validation
│   │   │                                 # ══════════════════════════════
│   │   ├── README.md
│   │   ├── docker-compose.override.yml
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── src/
│   │   │   └── template_store/
│   │   │       ├── __init__.py
│   │   │       ├── main.py
│   │   │       ├── api/
│   │   │       │   ├── templates.py
│   │   │       │   └── auth.py
│   │   │       ├── models/
│   │   │       │   ├── template.py
│   │   │       │   ├── field.py
│   │   │       │   └── rule.py
│   │   │       └── services/
│   │   │           ├── registry_client.py
│   │   │           ├── def_store_client.py
│   │   │           ├── template_service.py
│   │   │           └── inheritance_service.py
│   │   └── tests/
│   │
│   ├── document-store/                   # ══════════════════════════════
│   │   │                                 # DOCUMENT STORE SERVICE (Port 8004)
│   │   │                                 # Document storage & versioning
│   │   │                                 # ══════════════════════════════
│   │   ├── README.md
│   │   ├── docker-compose.override.yml
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── src/
│   │   │   └── document_store/
│   │   │       ├── __init__.py
│   │   │       ├── main.py
│   │   │       ├── api/
│   │   │       │   ├── documents.py
│   │   │       │   ├── table.py
│   │   │       │   └── auth.py
│   │   │       ├── models/
│   │   │       │   └── document.py
│   │   │       └── services/
│   │   │           ├── registry_client.py
│   │   │           ├── template_store_client.py
│   │   │           ├── def_store_client.py
│   │   │           ├── document_service.py
│   │   │           ├── validation_service.py
│   │   │           └── identity_service.py
│   │   └── tests/
│   │
│   ├── reporting-sync/                   # ══════════════════════════════
│   │   │                                 # REPORTING SYNC SERVICE (Port 8005)
│   │   │                                 # MongoDB → PostgreSQL sync
│   │   │                                 # ══════════════════════════════
│   │   ├── README.md
│   │   ├── docker-compose.override.yml
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── src/
│   │   │   └── reporting_sync/
│   │   │       ├── __init__.py
│   │   │       ├── main.py               # FastAPI app + health/metrics
│   │   │       ├── config.py
│   │   │       ├── models.py
│   │   │       ├── worker.py             # NATS consumer
│   │   │       ├── transformer.py        # Document → row transformation
│   │   │       ├── schema_manager.py     # PostgreSQL table management
│   │   │       ├── batch_sync.py         # Batch/recovery sync
│   │   │       └── metrics.py            # Metrics and alerts
│   │   └── tests/
│   │
│   ├── ingest-gateway/                    # ══════════════════════════════
│   │   │                                  # INGEST GATEWAY (Port 8006)
│   │   │                                  # Async bulk ingestion via NATS
│   │   │                                  # ══════════════════════════════
│   │   ├── src/ingest_gateway/
│   │   └── tests/
│   │
│   ├── mcp-server/                        # ══════════════════════════════
│   │   │                                  # MCP SERVER (stdio/SSE)
│   │   │                                  # 70+ tools, 5 resources
│   │   │                                  # ══════════════════════════════
│   │   ├── src/wip_mcp/
│   │   └── tests/
│   │
│   └── seed_data/                        # ══════════════════════════════
│       │                                 # SEED DATA MODULE
│       │                                 # Template-driven test data
│       │                                 # ══════════════════════════════
│       ├── __init__.py
│       ├── requirements.txt
│       ├── terminologies.py              # 15 terminology definitions
│       ├── templates.py                  # 24 template definitions
│       ├── documents.py                  # Document generation configs
│       ├── generators.py                 # Simple API for generation
│       ├── document_generator.py         # Template-driven generator
│       └── performance.py                # Benchmarking utilities
│
├── ui/                                   # Frontend applications
│   │
│   └── wip-console/                      # ══════════════════════════════
│       │                                 # WIP CONSOLE (Port 3000)
│       │                                 # Unified Web UI
│       │                                 # ══════════════════════════════
│       ├── README.md
│       ├── package.json
│       ├── vite.config.ts
│       ├── docker-compose.override.yml
│       ├── Dockerfile
│       ├── Dockerfile.dev
│       ├── nginx.conf
│       ├── src/
│       │   ├── main.ts
│       │   ├── App.vue
│       │   ├── api/                      # API clients
│       │   │   ├── defStoreClient.ts
│       │   │   ├── templateStoreClient.ts
│       │   │   └── documentStoreClient.ts
│       │   ├── components/
│       │   │   ├── layout/               # AppLayout, sidebar
│       │   │   ├── terminologies/        # Terminology components
│       │   │   ├── templates/            # Template components
│       │   │   └── documents/            # Document components
│       │   ├── router/
│       │   ├── stores/                   # Pinia stores
│       │   ├── types/                    # TypeScript interfaces
│       │   └── views/
│       │       ├── terminologies/
│       │       ├── templates/
│       │       └── documents/
│       └── tests/
│
├── config/                               # Configuration files
│   ├── presets/                          # Deployment presets
│   │   ├── core.conf
│   │   ├── standard.conf
│   │   ├── analytics.conf
│   │   └── full.conf
│   ├── caddy/                            # Reverse proxy config
│   │   ├── Caddyfile                     # Generated by setup.sh
│   │   └── Caddyfile.template
│   ├── dex/                              # OIDC provider config
│   │   └── config.yaml                   # Generated by setup.sh
│   └── api-keys.example.json             # Example API key config
│
├── scripts/                              # Utility scripts
│   ├── setup.sh                          # Unified setup script
│   ├── seed_comprehensive.py             # Test data seeding
│   ├── wipe-data.sh                      # Data cleanup
│   └── nuke.sh                           # Complete reset
│
├── data/                                 # Persistent data (gitignored)
│   ├── mongodb/                          # Document store
│   ├── postgres/                         # Reporting database
│   ├── nats/                             # Message queue
│   ├── dex/                              # OIDC state
│   └── caddy/                            # TLS certificates
│
├── docker-compose/                        # Modular compose configuration
│   ├── base.yml                          # Core services (MongoDB, services)
│   ├── modules/                          # Optional feature modules
│   │   ├── dev-tools.yml                 # Mongo Express, dev utilities
│   │   ├── files.yml                     # MinIO file storage
│   │   ├── nats.yml                      # NATS JetStream
│   │   ├── nats-auth.yml                 # NATS with auth
│   │   ├── oidc.yml                      # Dex OIDC provider
│   │   └── reporting.yml                 # PostgreSQL reporting
│   └── platforms/                        # Platform-specific overrides
│
├── WIP-Toolkit/                           # CLI toolkit (seed, export, etc.)
│
└── .env                                  # Generated by setup.sh
```

---

## Infrastructure vs Services

### Shared Infrastructure (docker-compose.infra.yml)

```yaml
services:
  mongodb:      # Document store - port 27017
  postgres:     # Reporting database - port 5432
  nats:         # Message queue - ports 4222, 8222
  dex:          # OIDC provider - port 5556
  caddy:        # Reverse proxy - ports 8080, 8443
  mongo-express: # MongoDB UI - port 8081 (optional)
```

### Application Services (docker-compose.yml per service)

Each service has its own compose file that:
- Builds the service container
- Connects to shared `wip-network`
- Uses environment from root `.env`

---

## Service Dependencies

```
Registry (8001)     ◄── First (generates IDs)
    │
Def-Store (8002)    ◄── Uses Registry for terminology/term IDs
    │
Template-Store (8003) ◄── Uses Registry for template IDs
    │                    References Def-Store terminologies
    │
Document-Store (8004) ◄── Uses Registry for document IDs
    │                     Validates against Template-Store
    │                     Validates terms against Def-Store
    │
Reporting-Sync (8005) ◄── Consumes events from NATS
                          Writes to PostgreSQL
```

---

## Running the Project

### Automated Setup (Recommended)

```bash
# Auto-detect platform and start everything
./scripts/setup.sh

# Or with specific preset
./scripts/setup.sh --preset standard --hostname wip-pi.local
```

### Manual Setup

```bash
# 1. Start infrastructure
podman-compose -f docker-compose.infra.yml up -d

# 2. Start Registry and initialize
cd components/registry
podman-compose -f docker-compose.yml up -d --build
curl -X POST http://localhost:8001/api/registry/namespaces/initialize-wip \
  -H "X-API-Key: dev_master_key_for_testing"

# 3. Start remaining services
cd ../def-store && podman-compose -f docker-compose.yml up -d --build
cd ../template-store && podman-compose -f docker-compose.yml up -d --build
cd ../document-store && podman-compose -f docker-compose.yml up -d --build
cd ../reporting-sync && podman-compose -f docker-compose.yml up -d --build

# 4. Start WIP Console
cd ../../ui/wip-console
podman-compose -f docker-compose.yml up -d --build
# Or: npm install && npm run dev
```

---

## Shared Library: wip-auth

All services use the shared authentication library:

```python
# Service main.py
from wip_auth import setup_auth

app = FastAPI()
setup_auth(app)  # Reads WIP_AUTH_* environment variables
```

### Installation (in each service's requirements.txt)

```
-e ../../libs/wip-auth
```

### Features

- Pluggable auth providers (none, api_key, oidc)
- Dual mode: API keys + JWT
- Named API keys with owner/groups
- FastAPI dependencies: `require_identity()`, `require_admin()`

---

## Configuration System

### Preset-Based Configuration

Presets in `config/presets/` define:
- `WIP_INCLUDE_DEX` - Enable OIDC
- `WIP_INCLUDE_CADDY` - Enable reverse proxy
- `WIP_INCLUDE_MONGO_EXPRESS` - Enable MongoDB UI
- `WIP_AUTH_MODE` - Authentication mode

### Generated Files

`scripts/setup.sh` generates:
- `.env` - Environment variables for all services
- `config/dex/config.yaml` - Dex OIDC configuration
- `config/caddy/Caddyfile` - Caddy routing configuration

---

## Test Data

### Seed Module (components/seed_data/)

Reusable test data definitions:
- 15 terminologies (GENDER, COUNTRY, CURRENCY, etc.)
- 24 templates (PERSON, EMPLOYEE, ORDER, etc.)
- Template-driven document generators

### Seeding Script

```bash
python scripts/seed_comprehensive.py --preset standard
```

---

## Benefits of This Structure

| Benefit | Description |
|---------|-------------|
| **Isolation** | Each service developed and tested independently |
| **Shared infrastructure** | Single MongoDB/PostgreSQL/NATS instance for all |
| **Clear dependencies** | Service compose files declare dependencies |
| **Flexible deployment** | From single Pi to distributed cloud |
| **Easy testing** | Each service has its own test suite |
| **Preset-based config** | Same code, different configurations |
