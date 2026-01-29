# Technology Stack

This document details the technology choices for World In a Pie (WIP) and the rationale behind each decision.

---

## Stack Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                      TECHNOLOGY STACK                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  FRONTEND                                                        │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │  Vue 3          │  Composition API, reactive UI         │    │
│  │  PrimeVue       │  UI component library                 │    │
│  │  PrimeIcons     │  Icon set                             │    │
│  │  Vite           │  Build tool                           │    │
│  │  TypeScript     │  Type safety                          │    │
│  │  Pinia          │  State management                     │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
│  BACKEND                                                         │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │  Python 3.11+   │  Runtime                              │    │
│  │  FastAPI        │  Web framework                        │    │
│  │  Pydantic       │  Data validation                      │    │
│  │  Motor          │  Async MongoDB driver                 │    │
│  │  asyncpg        │  Async PostgreSQL driver              │    │
│  │  nats-py        │  NATS client                          │    │
│  │  uvicorn        │  ASGI server                          │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
│  AUTH                                                            │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │  Authentik      │  Full-featured (default)              │    │
│  │  Authelia       │  Lightweight alternative              │    │
│  │  PyJWT          │  JWT validation                       │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
│  DATA                                                            │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │  MongoDB        │  Document store (default)             │    │
│  │  PostgreSQL     │  Reporting store (default)            │    │
│  │  SQLite         │  Lightweight alternative              │    │
│  │  NATS           │  Message queue                        │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
│  INFRASTRUCTURE                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │  Docker         │  Containerization                     │    │
│  │  Docker Compose │  Local/Pi orchestration               │    │
│  │  MicroK8s       │  Demo/production orchestration        │    │
│  │  Traefik        │  Reverse proxy                        │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Frontend

### Vue 3

**Choice**: Vue 3 with Composition API

**Rationale**:
- **Reactive by design**: Two-way binding is natural for form-heavy applications
- **Composition API**: Better code organization and reusability
- **Smaller learning curve**: More approachable than React for contributors
- **TypeScript support**: First-class TS integration
- **Bundle size**: Smaller than React (~33KB vs ~42KB gzipped)

**Alternatives Considered**:

| Alternative | Why Not Chosen |
|-------------|----------------|
| React | Larger ecosystem but more boilerplate; JSX polarizing |
| Svelte | Smallest bundle but ecosystem too limited for complex editors |
| HTMX | Not suitable for rich interactive UIs needed for editors |

### PrimeVue

**Choice**: PrimeVue component library

**Rationale**:
- **Rich components**: TreeTable, DataTable, OrderList perfect for WIP UIs
- **Flexible theming**: Not locked into Material Design
- **Enterprise-ready**: Form validation, complex interactions built-in
- **Active development**: Regular updates, good documentation

**Key Components Used**:

| Component | Use Case |
|-----------|----------|
| TreeTable | Ontology hierarchy display |
| DataTable | Document browsing, results display |
| OrderList | Field arrangement in template editor |
| AutoComplete | Term/field selection |
| Dialog | Modal editing forms |
| Toast | Notifications |
| ConfirmDialog | Destructive action confirmation |
| FileUpload | Import functionality |

**Alternatives Considered**:

| Alternative | Why Not Chosen |
|-------------|----------------|
| Vuetify | Material Design too opinionated; heavier |
| Naive UI | Beautiful but smaller component set |
| Element Plus | Less feature-rich for complex scenarios |

### Vite

**Choice**: Vite build tool

**Rationale**:
- **Fast dev server**: Instant HMR, near-instant startup
- **ES modules**: Modern, no bundling during development
- **Optimized builds**: Efficient production bundles
- **Vue ecosystem**: First-class Vue support

### Pinia

**Choice**: Pinia for state management

**Rationale**:
- **Official Vue store**: Successor to Vuex, recommended by Vue team
- **TypeScript-first**: Full type inference
- **Modular**: Store composition without boilerplate
- **DevTools**: Excellent debugging support

---

## Backend

### Python 3.11+

**Choice**: Python 3.11 or higher

**Rationale**:
- **Productivity**: Rapid development, readable code
- **Ecosystem**: Excellent libraries for data validation, async I/O
- **Performance**: 3.11 brought significant speed improvements
- **Team familiarity**: Common knowledge base
- **Async support**: Native async/await for I/O-bound workloads

**Alternatives Considered**:

| Alternative | Why Not Chosen |
|-------------|----------------|
| Node.js | Good async but less type safety; callback patterns |
| Go | Excellent performance but slower development |
| Rust | Best performance but steepest learning curve |

### FastAPI

**Choice**: FastAPI web framework

**Rationale**:
- **Automatic OpenAPI**: API documentation generated from code
- **Pydantic integration**: Validation built-in, perfect for WIP's validation needs
- **Async native**: Non-blocking I/O for better Pi performance
- **Type hints**: Self-documenting, catches errors early
- **Performance**: One of the fastest Python frameworks

**Key Features Used**:

```python
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(
    title="World In a Pie API",
    description="Template-driven document storage",
    version="1.0.0"
)

class DocumentCreate(BaseModel):
    """Document creation request."""
    template_id: str = Field(..., description="Template to validate against")
    data: dict = Field(..., description="Document content")

@app.post("/documents", response_model=Document)
async def create_document(
    doc: DocumentCreate,
    validator: ValidationEngine = Depends(get_validator),
    store: DocumentStore = Depends(get_store)
):
    """Create a new document with validation."""
    result = await validator.validate(doc)
    if not result.valid:
        raise HTTPException(400, detail=result.errors)
    return await store.save(doc)
```

**Alternatives Considered**:

| Alternative | Why Not Chosen |
|-------------|----------------|
| Django + DRF | Too heavy; ORM not needed for document store |
| Flask | Too minimal; would need many extensions |
| Starlette | FastAPI is built on it and adds critical features |

### Pydantic

**Choice**: Pydantic v2 for data validation

**Rationale**:
- **JSON-native**: Perfect for validating JSON documents
- **Declarative**: Models define validation rules clearly
- **FastAPI integration**: Seamless request/response validation
- **Performance**: v2 rewritten in Rust, very fast
- **Error messages**: Detailed, user-friendly validation errors

**Usage Pattern**:

```python
from pydantic import BaseModel, field_validator

class TemplateField(BaseModel):
    name: str
    type: Literal["string", "number", "date", "term", "object", "array"]
    mandatory: bool = False
    terminology_ref: str | None = None

    @field_validator("terminology_ref")
    @classmethod
    def validate_term_ref(cls, v, info):
        if info.data.get("type") == "term" and not v:
            raise ValueError("terminology_ref required for term type")
        return v
```

### Database Drivers

**Motor** (MongoDB):
```python
from motor.motor_asyncio import AsyncIOMotorClient

client = AsyncIOMotorClient("mongodb://localhost:27017")
db = client.wip
documents = db.documents
```

**asyncpg** (PostgreSQL):
```python
import asyncpg

pool = await asyncpg.create_pool("postgresql://localhost/wip_reporting")
async with pool.acquire() as conn:
    rows = await conn.fetch("SELECT * FROM doc_person WHERE city = $1", "Berlin")
```

---

## Authentication

### Authentik (Default)

**Choice**: Authentik for full-featured deployments

**Rationale**:
- **Python-based**: Matches the stack, team can contribute
- **Self-hosted**: Complete control over auth infrastructure
- **Full-featured**: OIDC, SAML, LDAP, MFA, RBAC
- **Modern UI**: Good user experience for login flows
- **Audit logging**: Compliance-ready

**Resource Usage**: ~300-500MB RAM

**Configuration**:
```yaml
auth:
  provider: authentik
  authentik:
    url: http://authentik:9000
    client_id: wip-api
    client_secret: ${AUTHENTIK_CLIENT_SECRET}
```

### Authelia (Lightweight Alternative)

**Choice**: Authelia for constrained deployments

**Rationale**:
- **Minimal footprint**: ~30-50MB RAM
- **Simple config**: YAML-based, easy to understand
- **Sufficient features**: OIDC, 2FA, session management
- **Pi-friendly**: Runs comfortably on limited hardware

**Configuration**:
```yaml
auth:
  provider: authelia
  authelia:
    url: http://authelia:9091
```

### JWT Handling

```python
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer
import jwt

security = HTTPBearer()

async def get_current_user(token: str = Depends(security)):
    try:
        payload = jwt.decode(
            token.credentials,
            key=settings.jwt_public_key,
            algorithms=["RS256"],
            audience=settings.jwt_audience
        )
        return User(
            id=payload["sub"],
            email=payload.get("email"),
            roles=payload.get("roles", [])
        )
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")
```

---

## Data Stores

### MongoDB (Document Store)

**Choice**: MongoDB as default document store

**Rationale**:
- **Native JSON**: No impedance mismatch with document model
- **Flexible schema**: Aligns with "store anything" philosophy
- **Rich queries**: Query nested documents, arrays naturally
- **Indexing**: Compound indexes, text search, geospatial
- **ARM support**: Official Raspberry Pi builds available

**Connection**:
```python
from motor.motor_asyncio import AsyncIOMotorClient

class MongoDocumentStore(DocumentStore):
    def __init__(self, connection_string: str):
        self.client = AsyncIOMotorClient(connection_string)
        self.db = self.client.wip

    async def save(self, document: Document) -> str:
        result = await self.db.documents.insert_one(document.dict())
        return str(result.inserted_id)

    async def get(self, id: str) -> Document | None:
        doc = await self.db.documents.find_one({"_id": ObjectId(id)})
        return Document(**doc) if doc else None
```

### PostgreSQL (Reporting Store)

**Choice**: PostgreSQL as default reporting store

**Rationale**:
- **SQL power**: Complex queries, aggregations, window functions
- **JSONB**: Flexible JSON storage when needed
- **Reliability**: Proven, battle-tested
- **Tooling**: Every BI/reporting tool supports Postgres
- **ARM support**: Available on Raspberry Pi

**Reporting Table Generation**:
```python
async def generate_table(template: Template, conn: asyncpg.Connection):
    columns = ["id UUID PRIMARY KEY", "version INTEGER", "status VARCHAR(20)"]

    for field in template.fields:
        sql_type = TYPE_MAP.get(field.type, "TEXT")
        columns.append(f"{field.name} {sql_type}")

    sql = f"CREATE TABLE IF NOT EXISTS doc_{template.name} ({', '.join(columns)})"
    await conn.execute(sql)
```

### SQLite (Lightweight Alternative)

For minimal deployments where MongoDB + PostgreSQL is too heavy:

```python
import aiosqlite

class SQLiteDocumentStore(DocumentStore):
    async def save(self, document: Document) -> str:
        async with aiosqlite.connect(self.path) as db:
            data_json = json.dumps(document.data)
            await db.execute(
                "INSERT INTO documents (id, template_id, data) VALUES (?, ?, ?)",
                (document.id, document.template_id, data_json)
            )
            await db.commit()
            return document.id
```

---

## Message Queue

### NATS

**Choice**: NATS as message queue

**Rationale**:
- **Lightweight**: 10-20MB RAM footprint
- **Fast**: Millions of messages per second
- **Simple**: Easy to understand pub/sub model
- **JetStream**: Persistence when needed
- **Request/Reply**: Supports synchronous patterns too

**Alternatives Considered**:

| Alternative | Why Not Chosen |
|-------------|----------------|
| RabbitMQ | Too heavy for Pi (~150MB RAM) |
| Kafka | Way too heavy; overkill for WIP |
| Redis Streams | Good but NATS is more purpose-built |

**Usage**:
```python
import nats

async def publish_event(event: Event):
    nc = await nats.connect("nats://localhost:4222")
    await nc.publish(
        f"wip.documents.{event.type}",
        event.json().encode()
    )
    await nc.close()

async def subscribe_events():
    nc = await nats.connect("nats://localhost:4222")
    sub = await nc.subscribe("wip.documents.>")
    async for msg in sub.messages:
        event = Event.parse_raw(msg.data)
        await handle_event(event)
```

---

## Infrastructure

### Docker

**Choice**: Docker for containerization

**Rationale**:
- **Portability**: Same containers run everywhere
- **Isolation**: Dependencies don't conflict
- **Reproducibility**: Same environment dev to prod
- **ARM support**: Multi-arch images available

**Base Images**:
```dockerfile
# API
FROM python:3.11-slim

# UI
FROM node:20-alpine AS build
FROM nginx:alpine AS runtime

# MongoDB
FROM mongo:7

# PostgreSQL
FROM postgres:16

# NATS
FROM nats:2.10
```

### Docker Compose

**Choice**: Docker Compose for local/Pi deployments

**Rationale**:
- **Simple**: Single file defines entire stack
- **Portable**: Works on Pi, laptop, server
- **Networking**: Automatic service discovery
- **Volumes**: Persistent data management

### MicroK8s

**Choice**: MicroK8s for demo/production

**Rationale**:
- **Lightweight**: Single-node Kubernetes that actually works
- **Snap install**: Easy setup
- **Add-ons**: Built-in ingress, storage, registry
- **Production-ready**: Can scale when needed

### Traefik

**Choice**: Traefik as reverse proxy

**Rationale**:
- **Auto-discovery**: Detects services automatically
- **Let's Encrypt**: Automatic HTTPS certificates
- **Dashboard**: Visual route management
- **Lightweight**: Lower resource usage than nginx + config

---

## Development Tools

### Code Quality

| Tool | Purpose |
|------|---------|
| **Ruff** | Fast Python linter and formatter |
| **mypy** | Static type checking |
| **pytest** | Testing framework |
| **pytest-asyncio** | Async test support |
| **ESLint** | JavaScript/TypeScript linting |
| **Prettier** | Code formatting |
| **Vitest** | Frontend testing |

### Development Workflow

```bash
# Backend
cd backend
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check .
mypy .

# Frontend
cd frontend
npm install
npm run dev
npm run test
npm run lint
```

### Pre-commit Hooks

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.1.0
    hooks:
      - id: ruff
      - id: ruff-format

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.0.0
    hooks:
      - id: mypy

  - repo: https://github.com/pre-commit/mirrors-eslint
    rev: v8.0.0
    hooks:
      - id: eslint
```

---

## Version Summary

| Component | Version | Notes |
|-----------|---------|-------|
| Python | 3.11+ | Required for performance improvements |
| FastAPI | 0.100+ | Latest stable |
| Pydantic | 2.0+ | v2 for Rust-based performance |
| Vue | 3.4+ | Composition API |
| PrimeVue | 3.40+ | Latest stable |
| MongoDB | 7.0+ | Document store |
| PostgreSQL | 16+ | Reporting store |
| NATS | 2.10+ | With JetStream |
| Docker | 24+ | BuildKit enabled |
| Authentik | 2024.1+ | Auth provider |
