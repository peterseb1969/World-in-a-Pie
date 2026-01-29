# Project Structure

This document defines the directory structure for World In a Pie (WIP), designed to keep components isolated and avoid conflicts.

---

## Design Principles

1. **Component Isolation**: Each component lives in its own directory with its own configuration
2. **Independent Deployment**: Components can be built and deployed independently
3. **No Compose Conflicts**: Each component has its own `docker-compose.yml`
4. **Shared Libraries**: Common code extracted to shared packages
5. **Clear Boundaries**: API contracts defined between components

---

## Directory Structure

```
world-in-a-pie/
│
├── README.md                           # Project overview
├── docs/                               # Documentation (this folder)
│   ├── philosophy.md
│   ├── architecture.md
│   ├── components.md
│   ├── technology-stack.md
│   ├── deployment.md
│   ├── data-models.md
│   ├── project-structure.md
│   └── glossary.md
│
├── shared/                             # Shared libraries and utilities
│   ├── wip-common/                     # Common Python package
│   │   ├── pyproject.toml
│   │   ├── src/
│   │   │   └── wip_common/
│   │   │       ├── __init__.py
│   │   │       ├── models/             # Shared Pydantic models
│   │   │       │   ├── __init__.py
│   │   │       │   ├── base.py         # Base model classes
│   │   │       │   └── identity.py     # Identity hash utilities
│   │   │       ├── auth/               # Auth utilities
│   │   │       │   ├── __init__.py
│   │   │       │   ├── jwt.py
│   │   │       │   └── api_key.py
│   │   │       └── config/             # Configuration utilities
│   │   │           ├── __init__.py
│   │   │           └── loader.py
│   │   └── tests/
│   │
│   └── wip-ui-common/                  # Shared Vue components
│       ├── package.json
│       └── src/
│           ├── components/
│           ├── composables/
│           └── utils/
│
├── components/                         # Individual components
│   │
│   ├── registry/                       # ══════════════════════════════
│   │   │                               # REGISTRY COMPONENT
│   │   │                               # First component to implement
│   │   │                               # ══════════════════════════════
│   │   ├── README.md
│   │   ├── docker-compose.yml          # Registry-specific compose
│   │   ├── docker-compose.dev.yml      # Development overrides
│   │   ├── Dockerfile
│   │   ├── config/
│   │   │   ├── config.yaml             # Default configuration
│   │   │   └── config.dev.yaml         # Development configuration
│   │   ├── pyproject.toml
│   │   ├── src/
│   │   │   └── registry/
│   │   │       ├── __init__.py
│   │   │       ├── main.py             # FastAPI application
│   │   │       ├── api/
│   │   │       │   ├── __init__.py
│   │   │       │   ├── namespaces.py
│   │   │       │   ├── entries.py
│   │   │       │   ├── synonyms.py
│   │   │       │   └── search.py
│   │   │       ├── models/
│   │   │       │   ├── __init__.py
│   │   │       │   ├── namespace.py
│   │   │       │   ├── entry.py
│   │   │       │   └── synonym.py
│   │   │       ├── services/
│   │   │       │   ├── __init__.py
│   │   │       │   ├── id_generator.py
│   │   │       │   ├── hash.py
│   │   │       │   └── search.py
│   │   │       └── storage/
│   │   │           ├── __init__.py
│   │   │           ├── base.py
│   │   │           ├── mongodb.py
│   │   │           └── sqlite.py
│   │   └── tests/
│   │       ├── conftest.py
│   │       ├── test_api/
│   │       ├── test_services/
│   │       └── test_storage/
│   │
│   ├── def-store/                      # ══════════════════════════════
│   │   │                               # DEF-STORE COMPONENT
│   │   │                               # Terminologies & Terms
│   │   │                               # ══════════════════════════════
│   │   ├── README.md
│   │   ├── docker-compose.yml
│   │   ├── docker-compose.dev.yml
│   │   ├── Dockerfile
│   │   ├── config/
│   │   ├── pyproject.toml
│   │   ├── src/
│   │   │   └── def_store/
│   │   │       ├── __init__.py
│   │   │       ├── main.py
│   │   │       ├── api/
│   │   │       ├── models/
│   │   │       ├── services/
│   │   │       └── storage/
│   │   └── tests/
│   │
│   ├── template-store/                 # ══════════════════════════════
│   │   │                               # TEMPLATE STORE COMPONENT
│   │   │                               # ══════════════════════════════
│   │   ├── README.md
│   │   ├── docker-compose.yml
│   │   ├── docker-compose.dev.yml
│   │   ├── Dockerfile
│   │   ├── config/
│   │   ├── pyproject.toml
│   │   ├── src/
│   │   │   └── template_store/
│   │   │       ├── __init__.py
│   │   │       ├── main.py
│   │   │       ├── api/
│   │   │       ├── models/
│   │   │       ├── services/
│   │   │       │   ├── validation.py   # Template validation
│   │   │       │   └── inheritance.py  # Template inheritance
│   │   │       └── storage/
│   │   └── tests/
│   │
│   ├── document-store/                 # ══════════════════════════════
│   │   │                               # DOCUMENT STORE COMPONENT
│   │   │                               # ══════════════════════════════
│   │   ├── README.md
│   │   ├── docker-compose.yml
│   │   ├── docker-compose.dev.yml
│   │   ├── Dockerfile
│   │   ├── config/
│   │   ├── pyproject.toml
│   │   ├── src/
│   │   │   └── document_store/
│   │   │       ├── __init__.py
│   │   │       ├── main.py
│   │   │       ├── api/
│   │   │       ├── models/
│   │   │       ├── services/
│   │   │       │   ├── validation.py   # Document validation engine
│   │   │       │   └── versioning.py   # Version management
│   │   │       └── storage/
│   │   └── tests/
│   │
│   ├── reporting/                      # ══════════════════════════════
│   │   │                               # REPORTING COMPONENT
│   │   │                               # Sync & SQL projection
│   │   │                               # ══════════════════════════════
│   │   ├── README.md
│   │   ├── docker-compose.yml
│   │   ├── Dockerfile
│   │   ├── config/
│   │   ├── pyproject.toml
│   │   ├── src/
│   │   │   └── reporting/
│   │   │       ├── __init__.py
│   │   │       ├── main.py
│   │   │       ├── sync/
│   │   │       │   ├── batch.py
│   │   │       │   ├── event.py
│   │   │       │   └── queue.py
│   │   │       └── transform/
│   │   └── tests/
│   │
│   └── gateway/                        # ══════════════════════════════
│       │                               # API GATEWAY (optional)
│       │                               # Unified entry point
│       │                               # ══════════════════════════════
│       ├── README.md
│       ├── docker-compose.yml
│       ├── Dockerfile
│       └── config/
│           └── traefik.yml
│
├── ui/                                 # Frontend applications
│   │
│   ├── admin/                          # Admin UI
│   │   ├── README.md
│   │   ├── package.json
│   │   ├── vite.config.ts
│   │   ├── Dockerfile
│   │   └── src/
│   │
│   ├── ontology-editor/                # Ontology/Terminology Editor
│   │   ├── README.md
│   │   ├── package.json
│   │   ├── vite.config.ts
│   │   ├── Dockerfile
│   │   └── src/
│   │
│   ├── template-editor/                # Template Editor
│   │   ├── README.md
│   │   ├── package.json
│   │   ├── vite.config.ts
│   │   ├── Dockerfile
│   │   └── src/
│   │
│   └── query-builder/                  # Query Builder
│       ├── README.md
│       ├── package.json
│       ├── vite.config.ts
│       ├── Dockerfile
│       └── src/
│
├── deploy/                             # Deployment configurations
│   │
│   ├── docker-compose/                 # Full stack compose files
│   │   ├── docker-compose.yml          # Production stack
│   │   ├── docker-compose.dev.yml      # Development stack
│   │   ├── docker-compose.pi.yml       # Raspberry Pi stack
│   │   ├── docker-compose.minimal.yml  # Minimal (SQLite) stack
│   │   └── .env.example
│   │
│   ├── k8s/                            # Kubernetes manifests
│   │   ├── namespace.yaml
│   │   ├── configmaps/
│   │   ├── secrets/
│   │   ├── registry/
│   │   ├── def-store/
│   │   ├── template-store/
│   │   ├── document-store/
│   │   ├── reporting/
│   │   └── ingress.yaml
│   │
│   └── scripts/                        # Deployment scripts
│       ├── deploy.sh
│       ├── backup.sh
│       └── restore.sh
│
├── tools/                              # Development tools
│   ├── bootstrap/                      # Bootstrap scripts
│   │   ├── seed-registry.py
│   │   └── seed-definitions.py
│   └── migration/                      # Migration tools
│       └── migrate.py
│
└── .github/                            # CI/CD
    └── workflows/
        ├── ci.yml
        ├── build.yml
        └── deploy.yml
```

---

## Component Independence

Each component in `components/` is designed to be **independently deployable**:

### Component docker-compose.yml Pattern

```yaml
# components/registry/docker-compose.yml
version: "3.8"

services:
  registry:
    build: .
    ports:
      - "8001:8000"
    environment:
      - WIP_CONFIG=/app/config/config.yaml
    volumes:
      - ./config:/app/config
    depends_on:
      - mongodb

  mongodb:
    image: mongo:7
    volumes:
      - registry-mongodb-data:/data/db

volumes:
  registry-mongodb-data:
```

### Development with Shared Services

For development, components can share infrastructure:

```yaml
# components/registry/docker-compose.dev.yml
version: "3.8"

services:
  registry:
    build: .
    ports:
      - "8001:8000"
    environment:
      - WIP_MONGODB_URI=mongodb://shared-mongodb:27017/wip_registry
    networks:
      - wip-dev

networks:
  wip-dev:
    external: true
```

---

## Full Stack Deployment

The `deploy/docker-compose/` directory contains compose files that orchestrate all components:

```yaml
# deploy/docker-compose/docker-compose.yml
version: "3.8"

services:
  # Infrastructure
  traefik:
    image: traefik:v3.0
    # ...

  mongodb:
    image: mongo:7
    # ...

  postgres:
    image: postgres:16
    # ...

  nats:
    image: nats:2.10
    # ...

  # Components
  registry:
    build: ../../components/registry
    # ...

  def-store:
    build: ../../components/def-store
    depends_on:
      - registry
    # ...

  template-store:
    build: ../../components/template-store
    depends_on:
      - registry
      - def-store
    # ...

  document-store:
    build: ../../components/document-store
    depends_on:
      - registry
      - template-store
    # ...

  # UIs
  admin-ui:
    build: ../../ui/admin
    # ...
```

---

## Implementation Order

Based on dependencies, the recommended implementation order is:

```
1. Registry          ◄── First (generates IDs for everything)
   │
2. Def-Store         ◄── Depends on Registry for IDs
   │
3. Template-Store    ◄── Depends on Registry + Def-Store
   │
4. Document-Store    ◄── Depends on Registry + Template-Store
   │
5. Reporting         ◄── Depends on Document-Store
   │
6. UIs               ◄── Can be developed in parallel once APIs exist
```

---

## Shared Code

### Python Package (wip-common)

Installed as editable dependency in each component:

```toml
# components/registry/pyproject.toml
[project]
name = "wip-registry"
dependencies = [
    "wip-common @ file://../../shared/wip-common",
    "fastapi>=0.100.0",
    "motor>=3.0.0",
    # ...
]
```

### Vue Package (wip-ui-common)

Shared UI components installed via npm workspace or local reference:

```json
// ui/admin/package.json
{
  "dependencies": {
    "wip-ui-common": "file:../../shared/wip-ui-common"
  }
}
```

---

## Running Components

### Individual Component (Development)

```bash
# Start just the Registry
cd components/registry
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up
```

### Full Stack (Development)

```bash
# Start everything
cd deploy/docker-compose
docker-compose -f docker-compose.dev.yml up
```

### Production

```bash
cd deploy/docker-compose
docker-compose -f docker-compose.yml up -d
```

### Raspberry Pi

```bash
cd deploy/docker-compose
docker-compose -f docker-compose.yml -f docker-compose.pi.yml up -d
```

---

## Benefits of This Structure

| Benefit | Description |
|---------|-------------|
| **Isolation** | Each component can be developed and tested independently |
| **Flexibility** | Deploy all components or just what you need |
| **Clear ownership** | Each component directory is self-contained |
| **No conflicts** | No docker-compose file collisions |
| **Scalability** | Individual components can be scaled independently |
| **Testability** | Each component has its own test suite |
| **CI/CD friendly** | Build and deploy components separately |
