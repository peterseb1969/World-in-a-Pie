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
         └── Registry ✅ → Def-Store → Template Store → Document Store → UI

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

### Next Steps
- [ ] Def-Store service (terminologies/ontologies)
- [ ] Template Store service
- [ ] Document Store service
- [ ] Reporting sync to PostgreSQL
- [ ] Web UI (Vue 3 + PrimeVue)
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

### Registry Service (Development)
```bash
cd components/registry
podman-compose -f docker-compose.dev.yml up -d

# API: http://localhost:8001
# Swagger: http://localhost:8001/docs
# MongoDB UI: http://localhost:8081
```

### Running Tests
```bash
podman exec -it wip_registry_app_dev bash -c \
  "pip install pytest pytest-asyncio httpx && \
   /home/appuser/.local/bin/pytest /app/tests -v"
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
└── components/
    └── registry/          # First component (complete)
        ├── src/registry/
        ├── tests/
        ├── scripts/
        ├── config/
        ├── docker-compose.yml
        └── docker-compose.dev.yml
```
