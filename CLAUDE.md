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
**Updated:** 2024-01-31 (Auth: Dex replaces Authelia/Authentik for all profiles)

We will develop the **Standard** deployment profile first, then expand to other profiles:

| Profile | Target | Document Store | Reporting | Auth | RAM |
|---------|--------|----------------|-----------|------|-----|
| **Minimal** | Pi 4 (1GB) | MongoDB | PostgreSQL | Dex | ~1GB |
| **Standard** | Pi 5 (8GB) | MongoDB | PostgreSQL | Dex | ~2GB |
| **Production** | Cloud/Server | MongoDB | PostgreSQL | Dex/Authentik | ~4GB+ |

**Target sweet spot:** Raspberry Pi 5 with 8GB RAM - plenty of headroom for the Standard profile.

**Why Standard first:**
1. Registry already uses MongoDB (working code and tests)
2. PostgreSQL for reporting is enterprise-ready
3. Dex provides full OIDC with minimal resources (~30MB RAM)
4. Pi 5 is a realistic primary target with excellent performance
5. Middle complexity - not too minimal, not over-engineered

**Why Dex for auth (development/minimal):**
- Lightweight (~30MB RAM) vs Authentik (~1.2GB)
- Works over HTTP (no HTTPS/certificate requirement for development)
- Full OIDC protocol - same as Authelia/Authentik, easy to switch later
- Static users via YAML config (no database required)

### Authentication Security by Profile

| Profile | OIDC Provider | Auth Mode | Network | Security Notes |
|---------|---------------|-----------|---------|----------------|
| **Minimal** | Dex | `dual` | Home/isolated | API keys OK for internal services |
| **Standard** | Dex | `dual` | Home/small office | Same as minimal, physically controlled network |
| **Production** | Authelia/Authentik | `dual` + reverse proxy | Cloud/internet | External traffic requires JWT only |

**Production security requirements:**
1. **Network isolation** - Services not directly exposed to internet
2. **Reverse proxy** - nginx/Traefik terminates TLS, enforces JWT for external requests
3. **Strong API keys** - Generate with `openssl rand -hex 32`, not dev keys
4. **OIDC provider** - Authelia (lightweight, requires HTTPS) or Authentik (enterprise features)
5. **Per-service keys** - Each service should have its own API key (future enhancement)

```
Internet → [Reverse Proxy: JWT required] → Private Network → [Services: API keys OK]
```

See `docs/authentication.md` for detailed security guidance and risk assessment.

### Development Phases

```
Phase 1: Standard profile end-to-end
         └── Registry ✅ → Def-Store ✅ → WIP Console ✅ → Template Store ✅ → Document Store ✅
         └── Reporting Layer (PostgreSQL sync) ✅ COMPLETE

Phase 2: Authentication & Authorization ✅ COMPLETE (Dex + wip-auth library)
         └── OIDC integration (Dex), dual auth (API key + JWT), RBAC via groups

Phase 3: Abstract storage layer based on learnings
         └── Extract interfaces where variation is needed

Phase 4: Add Minimal profile (SQLite backends)

Phase 5: Production profile (scaling, HA configs)
```

---

## Alternative Deployment Architectures (Not Planned)

**Status:** Hypothetical analysis only. Not currently planned for implementation.

The current architecture uses separate containers for each service. For extremely resource-constrained environments, two alternative approaches were analyzed:

### Option 1: Single Container for All Python Services

Combine registry, def-store, template-store, document-store, and reporting-sync into one container.

**Memory comparison:**

| Setup | Python Runtime | Dependencies | Container Overhead | Total |
|-------|---------------|--------------|-------------------|-------|
| 5 separate containers | 5 × ~50MB = 250MB | Duplicated ~100MB | 5 × ~15MB = 75MB | **~425MB** |
| 1 unified container | 1 × ~50MB = 50MB | Shared ~60MB | 1 × ~15MB = 15MB | **~125MB** |

**Savings: ~300MB RAM**

**Tradeoffs:**
- Pro: Inter-service calls become direct function calls (~0.01ms vs ~1-5ms)
- Pro: Shared database connection pools
- Pro: Faster startup (~5s vs ~20s)
- Con: Single point of failure (one crash takes everything down)
- Con: Can't scale services independently
- Con: Full restart required for any code change

### Option 2: Native (Non-Containerized) Deployment

Run all services directly on the Pi without containers.

**Memory comparison (1GB Pi 4):**

| Component | Containerized | Native |
|-----------|--------------|--------|
| Container runtime (Podman) | ~80MB | 0 |
| Container overhead (7 containers) | ~100MB | 0 |
| MongoDB | ~150MB | ~80MB (tuned) |
| PostgreSQL | ~100MB | ~50MB (tuned) |
| NATS | ~30MB | ~20MB |
| Dex | ~30MB | ~20MB |
| Python services | ~300MB (5 containers) | ~120MB (unified) |
| nginx + static | ~20MB | ~15MB |
| Linux OS | ~100MB | ~100MB |
| **Total** | **~910MB** | **~505MB** |

**Savings: ~400MB RAM**

**Tradeoffs:**
- Pro: Runs comfortably on 1GB Pi with ~500MB headroom
- Pro: Faster I/O (no overlay filesystem)
- Pro: Better for SD card longevity
- Con: No isolation between services
- Con: Dependency conflicts possible
- Con: Harder to reproduce environment
- Con: No easy "reset to clean state"
- Con: Manual updates (no container pull)

### Extreme Minimal Scenario

Combining both approaches (single native process) could theoretically run on ~385MB:

```
MongoDB (tuned): ~80MB
PostgreSQL (tuned): ~50MB
NATS: ~20MB
Dex: ~20MB
wip-unified (single Python): ~100MB
nginx (static Vue): ~15MB
Linux OS: ~100MB
────────────────────────
Total: ~385MB (would fit on 512MB Pi with swap)
```

### Why Not Implement These?

1. **Target is Pi 5 (8GB)** - Plenty of headroom, no need to optimize this aggressively
2. **Development convenience** - Containers provide isolation, easy reset, reproducible builds
3. **Maintainability** - Separate services are easier to develop, test, and update independently
4. **Future flexibility** - Can scale individual services if needed

These options remain available if targeting extremely constrained environments in the future.

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
   - Currently using Dex (lightweight OIDC provider)
   - Can switch to Authentik for enterprise features (same OIDC protocol)
   - API key auth for system-to-system communication
   - JWT/OIDC for user authentication
   - Configured via `WIP_AUTH_*` environment variables

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
  - Term aliases (multiple values resolve to same term_id)
  - Term audit log with REST API (tracks all changes without versioning)
  - Validation API with match type (code/value/alias)
  - Import/Export (JSON and CSV formats)
  - Multi-language support (translations)
  - Hierarchical terms (parent-child relationships)
  - API key authentication
  - Test suite (25 tests)
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
  - Document management (list, create, edit, delete)
  - Dynamic form generation based on template fields
  - Real-time validation feedback
  - Version history viewing and restore
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
- [x] Document Store service (document storage and validation)
  - Document CRUD with Registry integration (UUID7 IDs for time-ordering)
  - Template validation (fetches template from Template Store)
  - Six-stage validation pipeline (structural, template resolution, field validation, term validation, rule evaluation, identity computation)
  - Term validation via Def-Store bulk API
  - Term reference storage (stores both original value AND resolved term_id)
  - Identity-based upsert logic (SHA-256 hash of identity fields)
  - Automatic document versioning (deactivate old version, create new)
  - Version history (get all versions, get specific version, get latest version)
  - Latest version info in all document responses (is_latest_version, latest_document_id)
  - Soft-delete and archive operations
  - Complex query with filters
  - Bulk operations
  - API key authentication
  - Test suite (30+ tests)
- [x] Reporting Sync service (MongoDB → PostgreSQL sync)
  - NATS JetStream consumer for document/template events
  - Automatic PostgreSQL table creation from template definitions
  - Document transformation (flatten nested objects, handle arrays)
  - Upsert with version checking (handles out-of-order events)
  - Batch sync for initial population and recovery
  - Per-template sync configuration (strategy, table name, flatten options)
  - Schema evolution (add columns for new fields)
  - Monitoring and metrics (latency, throughput, per-template stats)
  - Alert system with configurable thresholds
  - Webhook notifications for alerts
  - API: http://localhost:8005

### Recently Completed
- [x] WIP Console OIDC support (browser-based login via Dex)
  - [x] oidc-client-ts library integration
  - [x] Dual auth mode (OIDC + API key)
  - [x] Configurable provider name for UI
  - [x] Auth callback routes and views
- [x] Authentication integration (wip-auth library)
  - [x] Shared auth library (`libs/wip-auth`)
  - [x] Pluggable provider architecture
  - [x] API key provider with backward compatibility
  - [x] OIDC provider for JWT authentication
  - [x] Service integration (all services use dual mode)
  - [x] Dex OIDC provider (replaces Authelia - works over HTTP, ~30MB RAM)

---

## Authentication Architecture

**Date:** 2025-01-30

### Overview

The authentication layer uses a **shared library** (`libs/wip-auth`) that provides pluggable authentication for all WIP services. This allows swapping auth providers via configuration.

### Auth Modes

| Mode | Use Case | Providers |
|------|----------|-----------|
| `none` | Development/testing | NoAuthProvider (pass-through) |
| `api_key_only` | Service-to-service (default) | APIKeyProvider |
| `jwt_only` | User authentication | OIDCProvider |
| `dual` | Both API keys and user login | APIKeyProvider + OIDCProvider |

### Configuration

All auth settings are read from environment variables with `WIP_AUTH_` prefix:

```bash
# API key only (backward compatible)
WIP_AUTH_MODE=api_key_only
WIP_AUTH_LEGACY_API_KEY=dev_master_key_for_testing

# Dual mode with Dex (current development setup)
WIP_AUTH_MODE=dual
WIP_AUTH_LEGACY_API_KEY=dev_master_key_for_testing
WIP_AUTH_JWT_ISSUER_URL=http://localhost:5556/dex
WIP_AUTH_JWT_JWKS_URI=http://host.containers.internal:5556/dex/keys
WIP_AUTH_JWT_AUDIENCE=wip-console
```

**Note:** The split issuer/JWKS configuration allows JWT tokens to work from both browser (localhost) and containers (host.containers.internal) without requiring `/etc/hosts` modifications.

Legacy environment variables (`API_KEY`, `MASTER_API_KEY`) are automatically mapped to `WIP_AUTH_LEGACY_API_KEY` for backward compatibility.

### Library Structure

```
libs/wip-auth/
├── src/wip_auth/
│   ├── __init__.py        # Main exports, setup_auth()
│   ├── config.py          # AuthConfig from environment
│   ├── models.py          # UserIdentity, APIKeyRecord
│   ├── identity.py        # Request-scoped identity context
│   ├── dependencies.py    # FastAPI dependencies (require_identity, etc.)
│   ├── middleware.py      # AuthMiddleware
│   └── providers/
│       ├── base.py        # AuthProvider protocol
│       ├── none.py        # NoAuthProvider (dev)
│       ├── api_key.py     # APIKeyProvider (service-to-service)
│       └── oidc.py        # OIDCProvider (user auth)
└── tests/
```

### Integration Pattern

Services integrate with wip-auth in two files:

**main.py:**
```python
from wip_auth import setup_auth

app = FastAPI()
setup_auth(app)  # Reads from WIP_AUTH_* env vars
```

**api/auth.py:**
```python
from wip_auth import require_identity, require_api_key, optional_identity

# These are re-exported for backward compatibility
```

### Dependencies

| Dependency | Purpose |
|------------|---------|
| `require_identity()` | Require any authenticated identity (401 if not) |
| `require_groups(["group"])` | Require specific group membership (403 if not) |
| `require_admin()` | Shortcut for require_groups(["wip-admins"]) |
| `optional_identity()` | Get identity if present, None otherwise |
| `require_api_key()` | Alias for require_identity (backward compat) |

### UserIdentity Model

```python
class UserIdentity:
    user_id: str          # Unique identifier
    username: str         # Display name
    email: str | None     # Email (if available)
    groups: list[str]     # Group memberships
    auth_method: str      # "jwt", "api_key", or "none"

    @property
    def identity_string(self) -> str:
        # "user:<id>", "apikey:<name>", or "anonymous"
        # For created_by/updated_by fields
```

### Provider Comparison

| Provider | Containers | RAM | HTTP OK | Use Case |
|----------|------------|-----|---------|----------|
| **Dex** (current) | 1 | ~30 MB | Yes | Development, Pi deployment |
| Authelia | 1 | ~30-100 MB | No (HTTPS) | Production with domain |
| Authentik | 3 | ~1.2 GB | Yes | Enterprise features, SSO |

**Why Dex:** Works over HTTP (no certificates needed), minimal RAM, full OIDC protocol (easy to switch providers later), static users via YAML config.

---

## Reporting Layer Architecture

**Date:** 2024-01-30

### Overview

The Reporting Layer syncs document data from MongoDB to PostgreSQL, enabling SQL-based analytics and BI tool integration. This is a **one-way sync** - PostgreSQL is read-only for external consumers.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         REPORTING SYNC ARCHITECTURE                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   WRITE PATH (Real-time via NATS)                                           │
│   ───────────────────────────────                                           │
│                                                                              │
│   Document Store                                                             │
│        │                                                                     │
│        ├──► MongoDB (primary store)                                          │
│        │                                                                     │
│        └──► NATS ──► Sync Worker ──► PostgreSQL                             │
│         (event with    │              (reporting)                            │
│          full doc)     │                                                     │
│                 ┌──────┴──────┐                                             │
│                 │  Transform  │                                             │
│                 │  • Flatten  │                                             │
│                 │  • Upsert   │                                             │
│                 └─────────────┘                                             │
│                                                                              │
│   RECOVERY PATH (Batch)                                                      │
│   ─────────────────────                                                     │
│                                                                              │
│   Scheduler ──► Sync Worker ──► Document Store API ──► PostgreSQL           │
│   (catchup)          │              (batch fetch)                            │
│                      │                                                       │
│               GET /sync/changes?since=...                                   │
│                                                                              │
│   QUERY PATHS                                                                │
│   ───────────                                                               │
│                                                                              │
│   WIP Console ──► Document Store API ──► MongoDB (Table View - existing)    │
│                                                                              │
│   BI Tools ──────────────────────────► PostgreSQL (direct SQL)              │
│   (Metabase, Grafana, Tableau, etc.)                                         │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Key Architectural Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Sync Trigger** | Event-driven via NATS + Batch fallback | Real-time sync with recovery capability |
| **Update Strategy** | Upsert by document_id with version check | Simple, handles out-of-order events |
| **Data Source** | NATS event contains full document | Self-contained events, no extra fetches |
| **Schema Creation** | Triggered by template save, not document arrival | Schema ready before docs arrive, no race conditions |
| **BI Interface** | Hybrid (direct PostgreSQL + BI tools) | PostgreSQL is data source; Metabase/Grafana are BI tools |

### Important Clarifications

1. **Table View is NOT stored** - The existing `/table/{template_id}` endpoint computes flat rows on-demand from MongoDB. There's no "update" step.

2. **Sync Worker is a separate service** - A new `reporting-sync` service subscribes to NATS and handles all PostgreSQL operations.

3. **Schema creation is proactive** - Tables are created when templates are saved, not reactively on first document. This avoids race conditions and ensures schema is ready.

4. **PostgreSQL is a data source, not a BI tool** - BI tools (Metabase, Grafana, Tableau) connect to PostgreSQL for visualization.

### Complete Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              COMPLETE FLOW                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   TEMPLATE CREATION (schema setup)                                           │
│   ────────────────────────────────                                          │
│   Template Store ──► MongoDB                                                 │
│        │                                                                     │
│        └──► NATS (wip.templates.created) ──► Sync Worker                    │
│                                                      │                       │
│                                                      ▼                       │
│                                              CREATE TABLE doc_{code}         │
│                                              (PostgreSQL schema ready)       │
│                                                                              │
│   DOCUMENT CREATION (data sync)                                              │
│   ─────────────────────────────                                             │
│   Document Store ──► MongoDB (primary store)                                 │
│        │                                                                     │
│        └──► NATS (wip.documents.created) ──► Sync Worker                    │
│                                                      │                       │
│                                              Check template config:          │
│                                              ├─ disabled? → discard          │
│                                              ├─ latest_only? → UPSERT        │
│                                              └─ all_versions? → INSERT       │
│                                                      │                       │
│                                                      ▼                       │
│                                              PostgreSQL (flattened data)     │
│                                                                              │
│   QUERYING                                                                   │
│   ────────                                                                  │
│   WIP Console ──► Document Store API ──► MongoDB (real-time, operational)   │
│                                                                              │
│   BI Tools ──────► PostgreSQL ──────────────► Analytics, cross-template     │
│   (Metabase,                                  joins, aggregations,           │
│    Grafana,                                   dashboards, reports            │
│    Tableau)                                                                  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Sync Trigger: Event-Driven via NATS

Document Store publishes events to NATS on every document change:

```
Subject: wip.documents.created
Subject: wip.documents.updated
Subject: wip.documents.deleted
```

**Event Payload:**
```json
{
  "event_id": "evt-123",
  "event_type": "document.created",
  "timestamp": "2024-01-30T10:00:00Z",
  "document": {
    "document_id": "0192abc...",
    "template_id": "TPL-000001",
    "template_code": "PERSON",
    "version": 1,
    "status": "active",
    "identity_hash": "a1b2c3...",
    "data": { ... },
    "term_references": { ... },
    "created_at": "2024-01-30T10:00:00Z",
    "created_by": "user-123"
  }
}
```

**Why full document in event:**
- Self-contained: Sync worker doesn't need MongoDB access
- No race conditions: Document state at event time is captured
- Simpler worker: Just flatten and upsert

**Batch fallback** for:
- Initial population of PostgreSQL
- Recovery after sync worker downtime
- Periodic consistency checks

### Update Strategy: Upsert by document_id

PostgreSQL tables use `document_id` as primary key with version checking:

```sql
INSERT INTO doc_person (document_id, version, status, first_name, last_name, ...)
VALUES ($1, $2, $3, $4, $5, ...)
ON CONFLICT (document_id)
DO UPDATE SET
  version = EXCLUDED.version,
  status = EXCLUDED.status,
  first_name = EXCLUDED.first_name,
  ...
WHERE doc_person.version < EXCLUDED.version;  -- Only if newer
```

**Version check** handles:
- Out-of-order event delivery
- Duplicate events (idempotent)
- Retry scenarios

### Template-Level Sync Configuration

**CRITICAL:** Sync behavior is configured per template but is **version-independent** (doesn't change when template is updated).

Templates gain a new `reporting` configuration section:

```json
{
  "code": "PERSON",
  "name": "Person",
  "identity_fields": ["email"],
  "fields": [...],
  "rules": [...],
  "reporting": {
    "sync_enabled": true,
    "sync_strategy": "latest_only",
    "table_name": "doc_person",
    "include_metadata": true,
    "flatten_arrays": true,
    "max_array_elements": 10
  }
}
```

**Sync Strategy Options:**

| Strategy | Behavior | Use Case |
|----------|----------|----------|
| `latest_only` | UPSERT overwrites, one row per document_id | Most common - current state only |
| `all_versions` | INSERT all versions, keep history | Audit requirements |
| `disabled` | Don't sync to PostgreSQL | Transient/temporary data |

**Why version-independent:**
- Sync strategy affects PostgreSQL table structure
- Changing strategy mid-stream would require table migration
- Template versions can change fields; reporting config stays stable
- Stored separately or as non-versioned metadata on template

### PostgreSQL Schema Management

**Auto-generated tables** from template definitions:

```python
# Template: PERSON with fields [first_name, last_name, email, birth_date, gender, address]

# Generated table:
CREATE TABLE doc_person (
    -- System columns (always present)
    document_id TEXT PRIMARY KEY,
    template_id TEXT NOT NULL,
    template_version INTEGER NOT NULL,
    version INTEGER NOT NULL,
    status VARCHAR(20) NOT NULL,
    identity_hash TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    created_by TEXT,
    updated_at TIMESTAMP,
    updated_by TEXT,

    -- Data columns (from template fields)
    first_name TEXT,
    last_name TEXT,
    email TEXT,
    birth_date DATE,
    gender TEXT,                    -- Term value (original)
    gender_term_id TEXT,            -- Resolved term_id

    -- Nested objects flattened
    address_street TEXT,
    address_city TEXT,
    address_postal_code TEXT,
    address_country TEXT,
    address_country_term_id TEXT,

    -- Original JSON for complex queries
    data_json JSONB,
    term_references_json JSONB,

    -- Indexes
    CONSTRAINT doc_person_identity_hash_idx UNIQUE (identity_hash)
        WHERE status = 'active'  -- Partial index for active docs only
);

CREATE INDEX doc_person_template_id_idx ON doc_person(template_id);
CREATE INDEX doc_person_status_idx ON doc_person(status);
CREATE INDEX doc_person_created_at_idx ON doc_person(created_at);
```

**Type Mapping:**

| WIP Field Type | PostgreSQL Type |
|----------------|-----------------|
| string | TEXT |
| number | NUMERIC |
| integer | INTEGER |
| boolean | BOOLEAN |
| date | DATE |
| datetime | TIMESTAMP |
| term | TEXT (value) + TEXT (term_id) |
| object | Flattened with prefix |
| array | See Array Handling |

**Array Handling:**

| Option | Behavior | Config |
|--------|----------|--------|
| Flatten | Multiple rows per document | `flatten_arrays: true` |
| JSON | Store as JSONB column | `flatten_arrays: false` |
| Truncate | First N elements only | `max_array_elements: 10` |

Default: Flatten arrays (consistent with existing Table View behavior).

### Schema Evolution

When template fields change:

1. **New field added:** `ALTER TABLE ADD COLUMN` (nullable)
2. **Field removed:** Column remains (historical data preserved)
3. **Field type changed:** New column with suffix `_v{version}`, old preserved
4. **Template deleted/deactivated:** Table remains, no new inserts

**Migration tracking table:**
```sql
CREATE TABLE _wip_schema_migrations (
    template_code TEXT NOT NULL,
    template_version INTEGER NOT NULL,
    migration_sql TEXT NOT NULL,
    applied_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (template_code, template_version)
);
```

### BI Interface: Hybrid Approach

**1. Direct PostgreSQL Access** (power users):
- Any SQL client: psql, DBeaver, DataGrip
- BI tools: Metabase, Grafana, Tableau, Power BI
- Custom applications: JDBC/ODBC connections

**2. WIP Console Enhancements** (casual users):
- Existing Table View (queries MongoDB) - already implemented
- New: Simple dashboard with document counts per template
- New: Field value distributions (charts for term fields)
- Future: Saved queries, scheduled reports

**Not building:**
- Full BI tool (use Metabase/Superset for that)
- Complex query builder (SQL is the query language)

### New Components

```
components/
├── reporting-sync/              # NEW: Sync Worker Service
│   ├── src/reporting_sync/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI app (health endpoint)
│   │   ├── config.py            # Configuration
│   │   ├── worker.py            # NATS consumer + sync logic
│   │   ├── transformer.py       # Document → flat row transformation
│   │   ├── schema_manager.py    # PostgreSQL table management
│   │   └── batch_sync.py        # Batch/recovery sync
│   ├── tests/
│   ├── Dockerfile
│   ├── docker-compose.dev.yml
│   └── requirements.txt
```

**Infrastructure additions to docker-compose.infra.yml:**
```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: wip_reporting
      POSTGRES_USER: wip
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - wip-postgres-data:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    networks:
      - wip-network

  nats:
    image: nats:2.10
    command: ["--js"]  # Enable JetStream
    ports:
      - "4222:4222"
      - "8222:8222"  # Monitoring
    networks:
      - wip-network
```

### Implementation Phases

**Phase 1: Infrastructure** ✅ COMPLETE
- [x] Add PostgreSQL to docker-compose.infra.yml
- [x] Add NATS to docker-compose.infra.yml
- [x] Create reporting-sync service skeleton
  - FastAPI app with health endpoints
  - NATS connection with JetStream stream creation
  - PostgreSQL connection with schema initialization
  - Schema manager for table generation
  - Transformer for document flattening
  - Worker skeleton for event processing

**Phase 2: Event Publishing** ✅ COMPLETE
- [x] Document Store publishes events to NATS on create/update/delete
- [x] Template Store publishes events on create/update
- [x] Event contains full document/template (self-contained)
- [x] JetStream for persistence (replay on worker restart)

**Phase 3: Schema Management** ✅ COMPLETE
- [x] Read template definitions from Template Store
- [x] Generate PostgreSQL table DDL from template
- [x] Handle schema evolution (new fields added as nullable columns)
- [x] Migration tracking table (_wip_schema_migrations)

**Phase 4: Sync Worker** ✅ COMPLETE
- [x] NATS consumer subscribes to document events
- [x] Transformer: Document → flat row(s)
- [x] Upsert to PostgreSQL with version check
- [x] Error handling with retry (NATS redelivery)

**Phase 5: Batch Sync** ✅ COMPLETE
- [x] POST /sync/batch/{template_code} - Sync all documents for a template
- [x] POST /sync/batch - Sync all templates
- [x] Job management (list, get, cancel jobs)
- [x] Progress tracking (total, synced, failed)

**Phase 6: Template Configuration** ✅ COMPLETE
- [x] Add `reporting` config to template model
- [x] UI for configuring sync behavior per template (WIP Console)
- [x] Settings: sync_enabled, sync_strategy, table_name, include_metadata, flatten_arrays, max_array_elements

**Phase 7: Monitoring & Alerts** ✅ COMPLETE
- [x] Metrics collection (latency percentiles, throughput, per-template stats)
- [x] NATS consumer info (queue depth, pending messages)
- [x] Alert system with configurable thresholds
- [x] Alert types: queue_lag, error_rate, processing_stalled, connection_lost
- [x] Webhook notifications for alerts
- [x] Background alert checking task

### Configuration

```yaml
# reporting-sync/config.yaml
nats:
  url: nats://localhost:4222
  stream: WIP_DOCUMENTS
  consumer: reporting-sync

postgresql:
  host: localhost
  port: 5432
  database: wip_reporting
  user: wip
  password: ${POSTGRES_PASSWORD}

sync:
  batch_size: 100
  retry_attempts: 3
  retry_delay_ms: 1000

template_store:
  url: http://localhost:8003
  api_key: ${API_KEY}
```

### API Endpoints (Reporting Sync Service)

**Base URL:** http://localhost:8005

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check (nats/postgres status) |
| GET | `/status` | Sync worker status |
| GET | `/metrics` | Comprehensive metrics (latency, throughput, per-template stats) |
| GET | `/metrics/consumer` | NATS consumer info (queue depth, pending) |
| GET | `/alerts` | Active alerts and configuration |
| PUT | `/alerts/config` | Update alert thresholds and webhook |
| POST | `/alerts/test` | Manually trigger alert check |
| GET | `/schema/{template_code}` | View generated schema for template |
| POST | `/sync/batch/{template_code}` | Trigger batch sync for one template |
| POST | `/sync/batch` | Trigger batch sync for all templates |
| GET | `/sync/batch/jobs` | List all batch sync jobs |
| GET | `/sync/batch/jobs/{job_id}` | Get specific job status |
| DELETE | `/sync/batch/jobs/{job_id}` | Cancel a running job |
| DELETE | `/sync/batch/jobs` | Clear completed jobs from memory |

---

### Future Requirements

#### Cross-Entity Audit Dashboard

Add a unified audit/history view to WIP Console that shows changes across all entity types:
- Terminologies (created, updated, deleted)
- Terms (created, updated, deprecated, deleted) - already has audit log API
- Templates (version history)
- Documents (version history)

Features needed:
- Timeline view of recent changes across all entities
- Filter by entity type, date range, user/API key
- Drill-down to see detailed change history
- Compare versions (diff view)

#### API Key Identity Tracking

**Problem:** Currently `created_by`/`updated_by` fields are self-reported by clients. The actual API key used for authentication is not recorded, making audit trails unreliable.

**Required changes:**

1. **API Key Registry** - Store API keys with metadata:
   ```
   api_key_id: string (hash of key)
   name: string (human-readable identifier)
   owner: string (user or system)
   created_at: datetime
   last_used_at: datetime
   permissions: list[string]
   ```

2. **Automatic Identity Injection** - When a request is authenticated:
   - Look up the API key's identity
   - Automatically set `created_by`/`updated_by` from the key's owner
   - Override any self-reported value (or reject if mismatch)

3. **Audit Log Enhancement** - Record:
   - `api_key_id` - Which key was used (hashed)
   - `api_key_name` - Human-readable key name
   - `claimed_by` - What the client claimed (if different)

4. **Integration with Authentik** - When OAuth/OIDC is added:
   - Use authenticated user identity instead of API key
   - Support both API key (system-to-system) and user auth (UI)

This ensures audit trails are trustworthy and tamper-evident.

#### Referential Integrity and Data Protection

**Problem:** The system has cross-service references that are not validated:
- Templates reference terminologies (via `terminology_ref` in term fields)
- Documents reference templates (via `template_id`)
- Documents store `term_references` pointing to term IDs

If referenced entities are missing (due to test cleanup, data corruption, or bugs), the system has orphaned references with no way to detect or recover.

**Core Principle:** Data is never deleted, and references must always resolve (even to inactive entities).

**Required changes:**

1. **Integrity Health Check API** - New endpoint(s) to scan for orphaned references:
   ```
   GET /api/health/integrity
   Response:
   {
     "status": "warning",
     "orphaned_template_refs": [...],    // Templates referencing missing terminologies
     "orphaned_document_refs": [...],    // Documents referencing missing templates
     "orphaned_term_refs": [...],        // Documents with term_references to missing terms
     "inactive_refs": [...]              // References to inactive (but existing) entities
   }
   ```

2. **Startup Validation** - Services log warnings about missing references on startup:
   - Document Store checks that referenced templates exist
   - Template Store checks that referenced terminologies exist
   - Non-blocking (service starts anyway) but visible in logs

3. **Cascade Status Warnings** - When deactivating an entity:
   - Warn about downstream dependencies
   - Example: "This terminology is used by 3 templates and 47 documents"
   - Require `force=true` or show confirmation in UI

4. **Reference Protection** - Prevent deactivating entities with active references:
   - Option A: Block deactivation, require deactivating dependents first
   - Option B: Allow with `force` flag, log warning
   - Option C: Cascade deactivation (dangerous, needs confirmation)

5. **Recovery Tools** - Scripts/endpoints to:
   - List all orphaned references
   - Export orphaned documents for analysis
   - Re-link orphaned data if source is restored

6. **Test Isolation** - Ensure development data safety:
   - Tests MUST use separate database (e.g., `wip_def_store_test` not `wip_def_store`)
   - Never run cleanup on non-test databases
   - CI/CD uses ephemeral databases

**Inactive vs Deleted:**
- `inactive` status means "soft-deleted" - still retrievable, still resolvable
- Validation should **warn** (not error) when referencing inactive entities
- Historical documents must always be able to resolve their references

#### Streaming Endpoint (TBD)

Real-time streaming endpoint for document changes.

**Note:** The Reporting Layer adds NATS infrastructure which enables this feature. Once NATS is in place:
- Clients can subscribe to `wip.documents.>` for all changes
- Or `wip.documents.{template_code}.>` for template-specific changes
- WebSocket bridge could expose this to browser clients

#### Per-Template Table View (Transactional Access) ✅ IMPLEMENTED

**Goal:** Provide immediate, transactional table views for each template - enabling traditional apps to use WIP as a validated data backend.

**Status:** Implemented in Document Store API.

**Endpoints:**
```
GET /api/document-store/table/{template_id}
  ?page=1&page_size=100
  ?status=active
  ?max_cross_product=1000

GET /api/document-store/table/{template_id}/csv
  ?status=active
  ?include_metadata=true
```

**Features:**
- JSON response with column metadata and flattened rows
- CSV export with proper escaping
- Metadata columns (_document_id, _version, _identity_hash, _status, _created_at, _updated_at)
- Array flattening with configurable cross-product threshold
- Nested objects serialized as JSON strings

**Array Handling (implemented):**

| Scenario | Behavior |
|----------|----------|
| 0 arrays | 1 row per document |
| 1 array | Flatten into multiple rows |
| 2+ arrays, cross-product ≤1000 rows | Cross-product (flatten all) |
| 2+ arrays, cross-product >1000 rows | Keep arrays as JSON fields, no flatten |

**Example output:**
```json
{
  "template_id": "TPL-000029",
  "template_code": "PERSON",
  "columns": [{"name": "first_name", "type": "string", "is_array": false}, ...],
  "rows": [
    {"_document_id": "...", "first_name": "John", "languages": "English"},
    {"_document_id": "...", "first_name": "John", "languages": "Spanish"}
  ],
  "total_documents": 100,
  "total_rows": 150,
  "array_handling": "flattened"
}
```

#### Per-Template Reporting (Aggregations)

**Goal:** Auto-generated dashboards with aggregations for each template.

**Features:**
- Document count per template
- Field value distributions (term fields: pie charts, numbers: histograms)
- Timeline of document creation/updates
- Filter by date range, status, field values
- Export to CSV/Excel

**Implementation options:**
1. **Live queries** - Query Document Store on demand (simple, slow for large datasets)
2. **Materialized views in PostgreSQL** - Sync to reporting DB, pre-aggregate (faster)
3. **Embedded analytics** - Lightweight BI tool (Metabase, Superset)

#### UI Gap: Term References Not Displayed ✅ FIXED

**Issue:** The Document Store API returns both `data` (original values) and `term_references` (resolved term IDs), but the WIP Console UI only displayed `data`.

**Fix:** Added "Term References" section in DocumentDetailView.vue Metadata tab:
- Table showing field path, original value, and resolved term_id
- Handles both single terms and arrays of terms
- Also added to Raw JSON tab for complete data visibility

---

## Technical Decisions

### Technology Stack
- **Backend:** Python 3.11+ / FastAPI
- **Document Store:** MongoDB (Standard/Production), SQLite (Minimal - future)
- **Reporting Store:** PostgreSQL (Standard/Production), SQLite (Minimal - future)
- **Message Queue:** NATS with JetStream
- **Auth:** Dex (all profiles), Authentik (optional for enterprise)
- **Auth Library:** wip-auth (shared library with pluggable providers)
- **Frontend:** Vue 3 + PrimeVue
- **Container:** Docker/Podman

### API Conventions
- All endpoints support bulk operations
- Dual authentication supported:
  - API key via `X-API-Key` header (service-to-service)
  - JWT via `Authorization: Bearer <token>` header (user auth)
- Development API key: `dev_master_key_for_testing`
- Development users: admin@wip.local, editor@wip.local, viewer@wip.local (password: `<user>123`)
- RESTful design with OpenAPI documentation at `/docs`

### Data Principles
- Never delete, only deactivate (soft-delete sets status to `inactive`)
- Inactive entities must remain retrievable (for historical reference resolution)
- References must always resolve (even to inactive entities)
- Composite keys with SHA-256 hashing for identity
- Namespaces for ID isolation across systems

---

## Document Identity and Upsert Model

### CRITICAL: Identity Fields Drive Versioning

The Document Store uses a **single POST endpoint** for both creating new documents and updating existing ones. The behavior is determined by the **identity hash**:

```
POST /api/document-store/documents
  ↓
Compute identity_hash = SHA-256(identity_field_1 + identity_field_2 + ...)
  ↓
Find active document with same identity_hash?
  → NO:  Create NEW document (version 1)
  → YES: Create NEW VERSION (version N+1), deactivate old version
```

### Identity Fields Configuration

Templates define which fields form the document's identity:

```json
{
  "code": "PERSON",
  "name": "Person",
  "identity_fields": ["email"],           // Single field identity
  "fields": [...]
}

{
  "code": "ORDER_LINE",
  "name": "Order Line Item",
  "identity_fields": ["order_id", "product_sku"],  // Composite identity
  "fields": [...]
}
```

**Important considerations:**
- Identity fields MUST be mandatory (enforced by validation)
- Choose fields that naturally identify the entity (email, SKU, order_id+line_number)
- Templates without identity fields cannot use upsert logic (each POST creates a new document)

### How Versioning Works

| Scenario | identity_hash | Result |
|----------|---------------|--------|
| First document with email="john@example.com" | `abc123...` | New document v1 |
| Update document with email="john@example.com" | `abc123...` | New version v2, v1 deactivated |
| New document with email="jane@example.com" | `def456...` | New document v1 (different identity) |

### Key Implementation Details

1. **Identity Hash**: SHA-256 of concatenated identity field values
2. **Document ID**: Unique UUID7 per version (for time-ordering)
3. **Version Number**: Incremented for each new version of same identity
4. **Status**: Only one `active` version per identity_hash; old versions become `inactive`

### Querying Versions

```bash
# Get all versions of a document
GET /api/document-store/documents/{document_id}/versions

# Get specific version
GET /api/document-store/documents/{document_id}/versions/{version}
```

All versions share the same `identity_hash`, making it easy to trace document history.

---

## Template Versioning

### CRITICAL: Multiple Template Versions Can Be Active Simultaneously

Templates support **multi-version operation** where different versions of the same template can be active at the same time. This is essential for gradual migration scenarios.

**Use Case Example:**
You have contracts with different companies about data sheet formats. When you update a template, you can't migrate all companies at once. Different companies use different template versions during the migration period.

### How Template Versioning Works

```
Template Update Flow:
  PUT /api/template-store/templates/{template_id}
    ↓
  Create NEW template document with:
    - NEW template_id (from Registry)
    - Same code
    - Incremented version number
    ↓
  Original template remains unchanged and active
```

| Operation | Result |
|-----------|--------|
| Create template (code=PERSON) | TPL-000001, version=1 |
| Update TPL-000001 | NEW TPL-000002, version=2, original unchanged |
| Update TPL-000002 | NEW TPL-000003, version=3, both originals unchanged |

### Template IDs vs Codes

- **template_id**: Unique per version (e.g., TPL-000001, TPL-000002)
- **code**: Shared across versions (e.g., PERSON)

Documents reference **template_id** which uniquely identifies the exact template version they conform to.

### API Endpoints for Versions

```bash
# List all versions of a template by code
GET /api/template-store/templates/by-code/{code}/versions

# Get specific version
GET /api/template-store/templates/by-code/{code}/versions/{version}

# Get latest version (default behavior)
GET /api/template-store/templates/by-code/{code}

# List all templates (shows all versions by default)
GET /api/template-store/templates

# List only latest version of each template
GET /api/template-store/templates?latest_only=true
```

### Document-Template Relationship

Documents store both `template_id` and `template_version`:

```json
{
  "document_id": "0192abc...",
  "template_id": "TPL-000001",     // Exact version reference
  "template_version": 1,           // Redundant but useful for queries
  "data": {...}
}
```

This allows:
- Documents to continue referencing older template versions
- Queries to filter documents by template version
- Validation to use the exact template the document was created with

### Key Differences from Document Versioning

| Aspect | Documents | Templates |
|--------|-----------|-----------|
| Trigger | Same identity_hash | PUT request |
| Old version status | `inactive` | Remains `active` |
| Use case | Audit trail | Gradual migration |
| Identity | identity_hash groups versions | code groups versions |

---

## Term Storage and Reference Resolution

### CRITICAL: Documents Store Both Value and Term ID

The system stores **both** the original submitted value **and** the resolved term_id for term fields. This is essential for data integrity and audit compliance.

**Philosophy:** We never migrate data. Mapping, ETL, and transformation happen downstream in PostgreSQL if needed. Data duplication is acceptable for preservation.

### How It Works

```json
// Document stored in MongoDB
{
  "document_id": "0192abc...",
  "template_id": "TPL-000001",
  "data": {
    "salutation": "Mr.",          // Original submitted value
    "country": "United Kingdom"   // Original submitted value
  },
  "term_references": {
    "salutation": "T-000001",     // Resolved term ID
    "country": "T-000042"         // Resolved term ID
  }
}
```

### Term Aliases

Terms support **aliases** - alternative values that resolve to the same term_id:

```json
{
  "term_id": "T-000001",
  "code": "MR",
  "value": "Mr",
  "aliases": ["MR", "MR.", "Mr.", "mr"]
}
```

When validating, all of these inputs resolve to the same term_id:
- "Mr" → matched via `value`
- "MR" → matched via `code`
- "Mr." → matched via `alias`
- "MR." → matched via `alias`

The validation response includes `matched_via` indicating how the match was made: "code", "value", or "alias".

### Why This Matters

1. **Audit Trail**: Original values are preserved for compliance
2. **Flexibility**: Aliases handle common variations without requiring exact matches
3. **Stable References**: term_id is stable even if code/value/aliases change
4. **No Migration**: Old documents retain their original data; only term_references link to the term

---

## Term Audit Log (No Versioning)

### CRITICAL: Terms Don't Have Versioning

Unlike documents and templates, **terms do not have versioning**. The term_id represents a stable **concept**, not a point-in-time snapshot.

### Rationale

- term_id represents a concept (e.g., "Mr" as a salutation)
- Changing the display value or adding aliases doesn't change the concept
- Documents reference term_id for the concept, not for exact historical state
- Versioning would create unnecessary complexity (two IDs per term)

### Audit Log Instead

All changes to terms are recorded in an audit log:

```json
{
  "term_id": "T-000001",
  "terminology_id": "TERM-000001",
  "action": "updated",
  "changed_at": "2024-01-30T10:00:00Z",
  "changed_by": "admin",
  "changed_fields": ["aliases"],
  "previous_values": {"aliases": ["MR", "MR."]},
  "new_values": {"aliases": ["MR", "MR.", "Mr.", "mr"]}
}
```

The audit log tracks:
- `created` - new term added
- `updated` - term modified
- `deprecated` - term marked deprecated
- `deleted` - term soft-deleted

---

## Document Version References

### CRITICAL: Old Document References Still Work

When a document is updated, a new version is created with a **new document_id**. Old references to previous versions must still resolve correctly.

### Every Document Response Includes Latest Version Info

When retrieving any document (including old versions), the response includes:

```json
{
  "document_id": "0192abc-old...",
  "version": 1,
  "is_latest_version": false,
  "latest_version": 3,
  "latest_document_id": "0192xyz-new...",
  // ... rest of document
}
```

This allows:
- Old references to still resolve to valid data
- Clients to discover if they have a stale reference
- Easy navigation to the latest version

### Get Latest Version Endpoint

```bash
# Given any document_id (even an old version), get the latest version
GET /api/document-store/documents/{document_id}/latest
```

This endpoint:
- Takes any document_id from the version chain
- Returns the latest version of that document
- Response includes the new document_id for future reference

### Use Cases

| Scenario | Behavior |
|----------|----------|
| Reference to current version | Returns document, `is_latest_version=true` |
| Reference to old version | Returns document, `is_latest_version=false`, shows latest_version and latest_document_id |
| Call /latest on old reference | Returns the current version with new document_id |

---

## Running the Project

### Development Setup

All services share infrastructure via `docker-compose.infra.yml`:

```bash
# 1. Start shared infrastructure
podman-compose -f docker-compose.infra.yml up -d

# MongoDB (document store): localhost:27017
# Mongo Express UI: http://localhost:8081 (admin/admin)
# PostgreSQL (reporting): localhost:5432 (wip/wip_dev_password, db: wip_reporting)
#   CLI access: podman exec -it wip-postgres psql -U wip -d wip_reporting
# NATS (message queue): localhost:4222
# NATS Monitoring: http://localhost:8222

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

# 6. Start Document Store service
cd ../document-store
podman-compose -f docker-compose.dev.yml up -d

# Document Store API: http://localhost:8004
# Document Store Swagger: http://localhost:8004/docs

# 7. Start Reporting Sync service
cd ../reporting-sync
podman-compose -f docker-compose.dev.yml up -d

# Reporting Sync API: http://localhost:8005
# Reporting Sync Swagger: http://localhost:8005/docs
# Metrics: http://localhost:8005/metrics
# Alerts: http://localhost:8005/alerts

# 9. Start WIP Console UI (optional - local dev)
cd ../../ui/wip-console
npm install
npm run dev

# WIP Console: http://localhost:3000
# Enter API key: dev_master_key_for_testing
# Manages terminologies (Def-Store), templates (Template-Store), and documents (Document-Store)

# 9b. Or run UI in container (uses shared network)
podman-compose -f docker-compose.dev.yml up -d

# Container connects to all services via wip-network
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

# Document Store tests (requires infra + document-store running)
# Note: Tests mock Registry, Template Store, and Def-Store clients
podman exec -it wip-document-store-dev bash -c \
  "pip install pytest pytest-asyncio httpx && \
   pytest /app/tests -v"
```

### Seed Dummy Data

#### Registry Seed (basic namespace demo)
```bash
source .venv/bin/activate
python components/registry/scripts/seed_data.py
```

#### Comprehensive Seed (all services)
```bash
# Install requirements
pip install faker requests

# Seed with standard profile (recommended for development)
python scripts/seed_comprehensive.py

# Options:
#   --profile minimal     Quick dev testing (50 docs)
#   --profile standard    Full functional testing (500 docs)
#   --profile full        Comprehensive coverage (2000 docs)
#   --profile performance Benchmarking (100k docs)

# Seed specific services only
python scripts/seed_comprehensive.py --services def-store,template-store

# Skip terminologies/templates (seed documents only)
python scripts/seed_comprehensive.py --skip-terminologies --skip-templates

# Run with benchmarks
python scripts/seed_comprehensive.py --benchmark --output benchmark.json

# Dry run (see what would be created)
python scripts/seed_comprehensive.py --dry-run
```

**Data Profile Summary:**

| Profile | Terminologies | Terms | Templates | Documents |
|---------|---------------|-------|-----------|-----------|
| minimal | 5 | ~50 | 5 | 50 |
| standard | 15 | ~500 | 24 | 500 |
| full | 15 | ~500 | 24 | 2,000 |
| performance | 15 | ~500 | 24 | 100,000 |

**Test Data Includes:**
- 15 terminologies (SALUTATION, GENDER, COUNTRY, CURRENCY, LANGUAGE, etc.)
- Term aliases for validation testing (Mr, MR, Mr., MR. all resolve to same term)
- Hierarchical terms (DEPARTMENT with parent-child relationships)
- 24 templates covering all field types and validation rules
- Inheritance chain testing (MANAGER -> EMPLOYEE -> PERSON)
- Edge case templates (MINIMAL, ALL_TYPES, DEEP_NEST, LARGE_FIELDS)
- Realistic document data using Faker library

**Template-Driven Document Generation:**

The seed data module uses a template-driven approach where document generators read template definitions and terminology values to automatically produce valid documents:

```python
from seed_data import generators

# Generate a document for any template
person = generators.generate_document("PERSON", index=0)
employee = generators.generate_document("EMPLOYEE", index=1)

# The generator automatically:
# 1. Reads template field definitions
# 2. Resolves template inheritance (EMPLOYEE -> PERSON)
# 3. Generates appropriate values per field type
# 4. Uses valid terminology values for term fields
# 5. Applies validation rules (conditional_required, conditional_value, etc.)
```

This approach ensures generated documents always satisfy validation rules without manually coding rule logic into each generator.

---

## File Structure

```
WorldInPie/
├── CLAUDE.md              # This file - session context
├── README.md              # Project overview
├── scripts/               # Project-wide scripts
│   └── seed_comprehensive.py  # Comprehensive test data seeding
├── libs/                  # Shared libraries
│   └── wip-auth/          # Pluggable authentication library
│       ├── src/wip_auth/
│       │   ├── __init__.py        # Main exports, setup_auth()
│       │   ├── config.py          # AuthConfig from environment
│       │   ├── models.py          # UserIdentity, APIKeyRecord
│       │   ├── identity.py        # Request-scoped identity context
│       │   ├── dependencies.py    # FastAPI dependencies
│       │   ├── middleware.py      # AuthMiddleware
│       │   └── providers/         # Auth provider implementations
│       ├── tests/
│       ├── pyproject.toml
│       └── README.md
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
│   ├── seed_data/         # Shared test data module
│   │   ├── __init__.py
│   │   ├── terminologies.py       # 15 terminology definitions
│   │   ├── templates.py           # 24 template definitions
│   │   ├── generators.py          # Simple API for document generation
│   │   ├── document_generator.py  # Template-driven generator core
│   │   ├── documents.py           # Document generation configs
│   │   ├── performance.py         # Benchmarking utilities
│   │   └── requirements.txt
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
│   │   │   ├── models/    # terminology, term, audit_log, api_models
│   │   │   └── services/  # registry_client, terminology_service, import_export
│   │   ├── tests/
│   │   ├── docker-compose.yml
│   │   ├── docker-compose.dev.yml
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   ├── template-store/    # Template Schema Store (complete)
│   │   ├── src/template_store/
│   │   │   ├── api/       # templates, auth
│   │   │   ├── models/    # template, field, rule, api_models
│   │   │   └── services/  # registry_client, def_store_client, template_service, inheritance_service
│   │   ├── tests/
│   │   ├── docker-compose.yml
│   │   ├── docker-compose.dev.yml
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   ├── document-store/    # Document Store (complete)
│   │   ├── src/document_store/
│   │   │   ├── api/       # documents, validation, auth
│   │   │   ├── models/    # document, api_models
│   │   │   └── services/  # registry_client, template_store_client, def_store_client, document_service, validation_service, identity_service
│   │   ├── tests/
│   │   ├── docker-compose.yml
│   │   ├── docker-compose.dev.yml
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   └── reporting-sync/    # Reporting Sync Worker (complete)
│       ├── src/reporting_sync/
│       │   ├── main.py           # FastAPI app with health, metrics, alerts endpoints
│       │   ├── config.py         # Configuration (NATS, PostgreSQL, etc.)
│       │   ├── models.py         # Pydantic models for events, metrics, alerts
│       │   ├── worker.py         # NATS consumer + sync logic
│       │   ├── transformer.py    # Document → flat row transformation
│       │   ├── schema_manager.py # PostgreSQL table management
│       │   ├── batch_sync.py     # Batch/recovery sync
│       │   └── metrics.py        # Metrics collection and alert management
│       ├── tests/
│       ├── docker-compose.yml
│       ├── docker-compose.dev.yml
│       ├── Dockerfile
│       └── requirements.txt
└── ui/
    └── wip-console/       # Unified Web UI (Vue 3 + PrimeVue)
        ├── src/
        │   ├── api/       # Unified API clients (defStoreClient, templateStoreClient, documentStoreClient)
        │   ├── components/
        │   │   ├── layout/        # AppLayout with sidebar navigation
        │   │   ├── terminologies/ # Terminology components
        │   │   ├── templates/     # Template components
        │   │   └── documents/     # Document components (FieldInput, DocumentForm, VersionHistory)
        │   ├── router/    # Vue Router config (terminologies + templates + documents routes)
        │   ├── stores/    # Pinia stores (auth, ui, terminology, term, template, document)
        │   ├── types/     # TypeScript interfaces
        │   └── views/
        │       ├── terminologies/ # Terminology views
        │       ├── templates/     # Template views
        │       └── documents/     # Document views (list, detail/create)
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
