# Claude Session Context

This file preserves key decisions and context for AI assistant sessions working on the World In a Pie (WIP) project.

---

## Project Overview

World In a Pie (WIP) is a universal template-driven document storage system designed to run on resource-constrained devices (Raspberry Pi) up to cloud deployments.

**Repository:** http://192.168.1.17:3000/peter/World-In-A-Pie.git

---

## Development Strategy

### Decision: Focus on Standard Profile First

**Date:** 2024-01-29

We will develop the **Standard** deployment profile first, then expand to other profiles:

| Profile | Target | Document Store | Reporting | Auth | RAM |
|---------|--------|----------------|-----------|------|-----|
| **Minimal** | Pi Zero/3 | SQLite | SQLite | Authelia | ~512MB |
| **Standard** | Pi 4/5 | MongoDB | PostgreSQL | Authentik | ~2GB |
| **Production** | Cloud/Server | MongoDB | PostgreSQL | Authentik | ~4GB+ |

**Why Standard first:**
1. Registry already uses MongoDB (working code and tests)
2. PostgreSQL for reporting is enterprise-ready
3. Authentik is full-featured (won't hit auth limitations)
4. Pi 4/5 is a realistic primary target
5. Middle complexity - not too minimal, not over-engineered

### Development Phases

```
Phase 1: Standard profile end-to-end
         └── Registry ✅ → Def-Store ✅ → Ontology Editor UI ✅ → Template Store ✅ → Document Store

Phase 2: Abstract storage layer based on learnings
         └── Extract interfaces where variation is needed

Phase 3: Add Minimal profile (SQLite backends)

Phase 4: Production profile (scaling, HA configs)
```

---

## CRITICAL: Pluggable Architecture

**All components MUST be designed with pluggability in mind from the start.**

Even though we're implementing only the Standard profile initially, the architecture must support swapping backends without major refactoring.

### Requirements

1. **Storage Abstraction**
   - Document Store: Must support MongoDB AND SQLite (later)
   - Reporting Store: Must support PostgreSQL AND SQLite (later)
   - Use repository pattern or similar abstraction

2. **Authentication Abstraction**
   - Must support Authentik AND Authelia (later)
   - API key auth for system-to-system (Registry)
   - OAuth2/OIDC for user auth

3. **Configuration-Driven**
   - Backend selection via config file, not code changes
   - Example:
     ```yaml
     storage:
       document_store:
         type: mongodb  # or: sqlite
         uri: mongodb://localhost:27017/wip
     ```

4. **Interface-First Design**
   - Define interfaces/protocols before implementation
   - Implement ONE backend initially
   - Other backends can be added without changing consumers

### Anti-Patterns to Avoid

- Direct MongoDB/PostgreSQL imports in business logic
- Hardcoded connection strings
- Backend-specific code outside adapter modules
- "We'll abstract it later" - abstract NOW, implement ONE

---

## Current Status

### Completed
- [x] Project documentation (philosophy, architecture, components, data models, deployment, glossary)
- [x] Registry service implementation
  - Namespace management with pluggable ID generation
  - Composite key registration with SHA-256 hashing
  - Synonym support for federated identity
  - Cross-namespace search
  - Bulk operations on all endpoints
  - API key authentication
  - Test suite (10 passing tests)
  - Seed script for dummy data
- [x] Def-Store service (terminologies/ontologies)
  - Terminology CRUD with Registry integration (TERM-XXXXXX IDs)
  - Term CRUD with Registry integration (T-XXXXXX IDs)
  - Validation API for checking values against terminologies
  - Import/Export (JSON and CSV formats)
  - Multi-language support (translations)
  - Hierarchical terms (parent-child relationships)
  - API key authentication
  - Test suite (24 tests)
- [x] WIP Console (Unified Web UI - Vue 3 + PrimeVue)
  - Consolidated UI replacing ontology-editor and template-editor
  - Terminology management (list, create, edit, delete)
  - Term management with bulk import (JSON/CSV)
  - Export terminologies to JSON/CSV
  - Value validation (single and bulk)
  - Template management (list, create, edit, delete)
  - Field definitions with terminology references
  - Validation rules
  - Cross-linking: shows which templates use each terminology
  - Sidebar-based navigation
  - API key authentication with localStorage persistence
  - Docker support for dev and production
- [x] Template Store service (document schemas)
  - Template CRUD with Registry integration (TPL-XXXXXX IDs)
  - Field definitions with types: string, number, integer, boolean, date, datetime, term, object, array
  - Terminology references (term type fields link to Def-Store)
  - Template references (nested objects, array items)
  - Template inheritance (extends) with field override and rule merging
  - Cross-field validation rules (conditional_required, conditional_value, mutual_exclusion, dependency)
  - Field-level validation (pattern, min/max length, min/max value, enum)
  - Template validation endpoint (checks terminology and template references)
  - Bulk operations
  - API key authentication
  - Test suite (30+ tests)

### Next Steps
- [ ] Run Def-Store tests (requires Registry service)
- [ ] Document Store service
- [ ] Reporting sync to PostgreSQL
- [ ] Authentication integration (Authentik)

---

## Technical Decisions

### Technology Stack
- **Backend:** Python 3.11+ / FastAPI
- **Document Store:** MongoDB (Standard/Production), SQLite (Minimal)
- **Reporting Store:** PostgreSQL (Standard/Production), SQLite (Minimal)
- **Message Queue:** NATS with JetStream
- **Auth:** Authentik (Standard/Production), Authelia (Minimal)
- **Frontend:** Vue 3 + PrimeVue
- **Container:** Docker/Podman

### API Conventions
- All endpoints support bulk operations
- API key auth via `X-API-Key` header
- Development API key: `dev_master_key_for_testing`
- RESTful design with OpenAPI documentation at `/docs`

### Data Principles
- Never delete, only deactivate (versioning policy)
- Composite keys with SHA-256 hashing for identity
- Namespaces for ID isolation across systems

---

## Running the Project

### Development Setup

All services share a single MongoDB instance via `docker-compose.infra.yml`:

```bash
# 1. Start shared infrastructure (MongoDB + Mongo Express)
podman-compose -f docker-compose.infra.yml up -d

# MongoDB: localhost:27017
# Mongo Express UI: http://localhost:8081 (admin/admin)

# 2. Start Registry service
cd components/registry
podman-compose -f docker-compose.dev.yml up -d

# Registry API: http://localhost:8001
# Registry Swagger: http://localhost:8001/docs

# 3. Initialize WIP namespaces (one-time setup)
curl -X POST http://localhost:8001/api/registry/namespaces/initialize-wip \
  -H "X-API-Key: dev_master_key_for_testing"

# 4. Start Def-Store service
cd ../def-store
podman-compose -f docker-compose.dev.yml up -d

# Def-Store API: http://localhost:8002
# Def-Store Swagger: http://localhost:8002/docs

# 5. Start Template Store service
cd ../template-store
podman-compose -f docker-compose.dev.yml up -d

# Template Store API: http://localhost:8003
# Template Store Swagger: http://localhost:8003/docs

# 6. Start WIP Console UI (optional - local dev)
cd ../../ui/wip-console
npm install
npm run dev

# WIP Console: http://localhost:3000
# Enter API key: dev_master_key_for_testing
# Manages both terminologies (Def-Store) and templates (Template-Store)

# 6b. Or run UI in container (uses shared network)
podman-compose -f docker-compose.dev.yml up -d

# Container connects to both def-store and template-store via wip-network
```

### Production Setup

```bash
# Set required environment variables
export MONGO_PASSWORD=$(openssl rand -hex 16)
export MASTER_API_KEY=$(openssl rand -hex 32)
export API_KEY=$MASTER_API_KEY
export REGISTRY_API_KEY=$MASTER_API_KEY

# Start infrastructure
podman-compose -f docker-compose.infra.prod.yml up -d

# Start services
cd components/registry && podman-compose up -d
cd ../def-store && podman-compose up -d
cd ../template-store && podman-compose up -d
```

### Running Tests
```bash
# Registry tests (requires infra + registry running)
podman exec -it wip-registry-dev bash -c \
  "pip install pytest pytest-asyncio httpx && \
   pytest /app/tests -v"

# Def-Store tests (requires infra + def-store running)
# Note: Tests mock the Registry client, so Registry doesn't need to be running
podman exec -it wip-def-store-dev bash -c \
  "pip install pytest pytest-asyncio httpx && \
   pytest /app/tests -v"

# Template Store tests (requires infra + template-store running)
# Note: Tests mock Registry and Def-Store clients
podman exec -it wip-template-store-dev bash -c \
  "pip install pytest pytest-asyncio httpx && \
   pytest /app/tests -v"
```

### Seed Dummy Data
```bash
source .venv/bin/activate
python components/registry/scripts/seed_data.py
```

---

## File Structure

```
WorldInPie/
├── CLAUDE.md              # This file - session context
├── README.md              # Project overview
├── docs/                  # Documentation
│   ├── philosophy.md
│   ├── architecture.md
│   ├── components.md
│   ├── data-models.md
│   ├── deployment.md
│   ├── glossary.md
│   ├── project-structure.md
│   └── technology-stack.md
├── components/
│   ├── registry/          # ID & Namespace Registry (complete)
│   │   ├── src/registry/
│   │   ├── tests/
│   │   ├── scripts/
│   │   ├── config/
│   │   ├── docker-compose.yml
│   │   └── docker-compose.dev.yml
│   ├── def-store/         # Terminology & Ontology Store (complete)
│   │   ├── src/def_store/
│   │   │   ├── api/       # terminologies, terms, import_export, validation, auth
│   │   │   ├── models/    # terminology, term, api_models
│   │   │   └── services/  # registry_client, terminology_service, import_export
│   │   ├── tests/
│   │   ├── docker-compose.yml
│   │   ├── docker-compose.dev.yml
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   └── template-store/    # Template Schema Store (complete)
│       ├── src/template_store/
│       │   ├── api/       # templates, auth
│       │   ├── models/    # template, field, rule, api_models
│       │   └── services/  # registry_client, def_store_client, template_service, inheritance_service
│       ├── tests/
│       ├── docker-compose.yml
│       ├── docker-compose.dev.yml
│       ├── Dockerfile
│       └── requirements.txt
└── ui/
    └── wip-console/       # Unified Web UI (Vue 3 + PrimeVue)
        ├── src/
        │   ├── api/       # Unified API clients (defStoreClient, templateStoreClient)
        │   ├── components/
        │   │   ├── layout/        # AppLayout with sidebar navigation
        │   │   ├── terminologies/ # Terminology components
        │   │   └── templates/     # Template components
        │   ├── router/    # Vue Router config (terminologies + templates routes)
        │   ├── stores/    # Pinia stores (auth, ui, terminology, term, template)
        │   ├── types/     # TypeScript interfaces
        │   └── views/
        │       ├── terminologies/ # Terminology views
        │       └── templates/     # Template views
        ├── docker-compose.yml
        ├── docker-compose.dev.yml
        ├── Dockerfile
        ├── Dockerfile.dev
        └── nginx.conf
```

## WIP Namespaces

The Registry service manages these WIP-internal namespaces:

| Namespace | ID Generator | Purpose | Service |
|-----------|--------------|---------|---------|
| `default` | UUID4 | General use | - |
| `wip-terminologies` | Prefixed (TERM-) | Terminology IDs | Def-Store |
| `wip-terms` | Prefixed (T-) | Term IDs | Def-Store |
| `wip-templates` | Prefixed (TPL-) | Template IDs | Template Store |
| `wip-documents` | UUID7 | Document IDs | Document Store |
