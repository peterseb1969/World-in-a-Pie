# WIP Nano Design

A minimal, embedded variant of World In a Pie for resource-constrained and offline use cases.

## Status: Future / Not Planned for Implementation

This document captures design discussions for a potential ultra-lightweight WIP variant. It is **not currently planned for implementation** but serves as a reference for future consideration.

---

## Motivation

The standard WIP deployment targets Raspberry Pi 5 with 8GB RAM and uses:
- 6 service pods (Registry, Def-Store, Template-Store, Document-Store, Reporting-Sync, Console)
- 4+ infrastructure pods (MongoDB, PostgreSQL, NATS, optionally Dex, Caddy, MinIO)
- ~1-2GB RAM total

For certain edge cases, this is still too heavy:
- **Pi Zero / Pi 3** with 512MB-1GB RAM
- **Embedded systems** with severe resource constraints
- **Air-gapped deployments** requiring minimal dependencies
- **Single-user scenarios** that don't need multi-tenancy
- **Data collection endpoints** that sync to a central WIP instance periodically

---

## Target Specifications

| Aspect | Standard WIP | WIP Nano |
|--------|--------------|----------|
| **RAM** | 1-2GB | 200-500MB |
| **Containers** | 10+ | 2-3 |
| **Database** | MongoDB + PostgreSQL | SQLite (embedded) |
| **Message Queue** | NATS JetStream | None (direct) |
| **Concurrent Users** | Many | 1-2 |
| **Target Device** | Pi 4/5, servers | Pi Zero, embedded |
| **Network Required** | Yes | No (offline capable) |

---

## Architecture Options

### Option A: Merged Services + SQLite

Consolidate all Python services into a single FastAPI application with SQLite backend.

```
┌─────────────────────────────────────┐
│ wip-nano (single binary/container)  │
│ ┌─────────────────────────────────┐ │
│ │ FastAPI                         │ │
│ │ - /api/registry/*               │ │
│ │ - /api/def-store/*              │ │
│ │ - /api/template-store/*         │ │
│ │ - /api/document-store/*         │ │
│ └─────────────────────────────────┘ │
│                 │                   │
│ ┌───────────────▼───────────────┐   │
│ │         SQLite                │   │
│ │  data/wip.db                  │   │
│ └───────────────────────────────┘   │
└─────────────────────────────────────┘
```

**Pros:**
- Single process, ~50-100MB RAM
- Zero infrastructure dependencies
- File = database (trivial backup)
- Works completely offline
- No container runtime needed (can run as systemd service)

**Cons:**
- Single-writer limitation (no concurrent writes)
- No remote database access
- Limited JSON query capabilities vs MongoDB
- Requires significant code rewrite
- No real-time sync (no NATS)

### Option B: Merged Services + MongoDB

Keep MongoDB but merge all Python services.

```
┌──────────────────┐     ┌─────────────┐
│ wip-nano-api     │────▶│   MongoDB   │
│ (all routes)     │     │  (embedded) │
└──────────────────┘     └─────────────┘
```

**Pros:**
- Minimal rewrite (just merge FastAPI apps)
- Keep MongoDB's document model
- ~300-500MB RAM

**Cons:**
- MongoDB still requires ~200-300MB RAM
- Still needs container for MongoDB
- Not truly embedded

### Option C: SQLite + Periodic Sync to Central WIP

Use SQLite locally but sync to a full WIP instance periodically.

```
┌─────────────────────┐          ┌───────────────────┐
│ WIP Nano (edge)     │          │ WIP Central       │
│ ┌─────────────────┐ │   sync   │ (full deployment) │
│ │ SQLite          │─┼─────────▶│                   │
│ │ (local data)    │ │  weekly  │                   │
│ └─────────────────┘ │          │                   │
└─────────────────────┘          └───────────────────┘
```

**Pros:**
- Best of both worlds
- Offline-first operation
- Eventually consistent with central system
- Edge devices can be very lightweight

**Cons:**
- Complexity of sync logic
- Conflict resolution needed
- Two different storage engines

---

## SQLite Schema Design

If pursuing Option A or C, documents would be stored as JSONB-like blobs:

```sql
-- Core tables
CREATE TABLE namespaces (
    id TEXT PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    id_generator TEXT NOT NULL,
    prefix TEXT,
    counter INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE terminologies (
    terminology_id TEXT PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'active',
    metadata_json TEXT,  -- JSON blob
    created_at TEXT NOT NULL,
    created_by TEXT
);

CREATE TABLE terms (
    term_id TEXT PRIMARY KEY,
    terminology_id TEXT NOT NULL REFERENCES terminologies(terminology_id),
    code TEXT NOT NULL,
    value TEXT NOT NULL,
    aliases_json TEXT,  -- JSON array
    description TEXT,
    status TEXT DEFAULT 'active',
    parent_id TEXT REFERENCES terms(term_id),
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(terminology_id, code)
);

CREATE TABLE templates (
    template_id TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT DEFAULT 'active',
    extends TEXT,  -- parent template code
    identity_fields_json TEXT,  -- JSON array
    fields_json TEXT NOT NULL,  -- JSON array of field definitions
    rules_json TEXT,  -- JSON array of validation rules
    reporting_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(code, version)
);

CREATE TABLE documents (
    document_id TEXT PRIMARY KEY,
    template_id TEXT NOT NULL REFERENCES templates(template_id),
    template_code TEXT NOT NULL,
    template_version INTEGER NOT NULL,
    identity_hash TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT DEFAULT 'active',
    is_latest_version INTEGER DEFAULT 1,
    data_json TEXT NOT NULL,  -- The actual document data
    term_references_json TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT,
    updated_at TEXT,
    updated_by TEXT
);

-- Indexes
CREATE INDEX idx_terms_terminology ON terms(terminology_id);
CREATE INDEX idx_terms_value ON terms(value);
CREATE INDEX idx_templates_code ON templates(code);
CREATE INDEX idx_documents_template ON documents(template_id);
CREATE INDEX idx_documents_identity ON documents(identity_hash);
CREATE INDEX idx_documents_status ON documents(status, is_latest_version);
```

### JSON Querying in SQLite

SQLite 3.38+ supports `json_extract()` for querying JSON fields:

```sql
-- Find documents where data.country = 'Germany'
SELECT * FROM documents
WHERE json_extract(data_json, '$.country') = 'Germany';

-- Find terms with specific alias
SELECT * FROM terms
WHERE json_extract(aliases_json, '$') LIKE '%"DE"%';
```

**Limitations vs MongoDB:**
- No nested array queries
- No compound JSON indexes
- Manual JSON path construction

---

## Implementation Effort

| Component | Effort | Notes |
|-----------|--------|-------|
| Merge services into one | Low | Combine FastAPI routers |
| SQLite repository layer | High | New implementations for all repositories |
| Remove NATS dependency | Medium | Direct function calls instead of events |
| Simplified validation | Medium | Keep core validation, skip event publishing |
| CLI interface | Medium | Command-line tools for import/export |
| Sync mechanism | High | If implementing Option C |

**Estimated total:** 2-4 weeks for a basic implementation

---

## Use Cases

### 1. Field Data Collection

A researcher collects plant observations in remote locations without internet.

```
┌─────────────────────────────────────────────────────────┐
│ Field Tablet (Pi Zero + touchscreen)                    │
│ ┌─────────────────────────────────────────────────────┐ │
│ │ WIP Nano                                            │ │
│ │ - Templates: PLANT_OBSERVATION, SITE               │ │
│ │ - Terminologies: SPECIES, SOIL_TYPE, WEATHER       │ │
│ │ - Collects 50-100 observations per day             │ │
│ └─────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
                     │
                     │ Weekly sync (USB or WiFi)
                     ▼
┌─────────────────────────────────────────────────────────┐
│ Research Lab (full WIP)                                 │
│ - Consolidates data from multiple field devices        │
│ - PostgreSQL reporting for analysis                    │
│ - Long-term storage and backup                         │
└─────────────────────────────────────────────────────────┘
```

### 2. Embedded Industrial Sensor

A machine controller validates and stores sensor readings.

```
┌─────────────────────────────┐
│ Industrial Controller       │
│ (256MB RAM, ARM)           │
│ ┌─────────────────────────┐ │
│ │ WIP Nano                │ │
│ │ - SENSOR_READING        │ │
│ │ - Validates ranges      │ │
│ │ - Buffers locally       │ │
│ └─────────────────────────┘ │
└─────────────────────────────┘
        │
        │ MQTT bridge (when connected)
        ▼
┌─────────────────────────────┐
│ Factory WIP Server          │
└─────────────────────────────┘
```

### 3. Personal Knowledge Base

Single-user note-taking with structured data.

```
$ wip-nano init ~/notes
$ wip-nano template create NOTE --fields "title:string,content:string,tags:array"
$ wip-nano doc create NOTE --data '{"title": "Meeting notes", "content": "..."}'
$ wip-nano query NOTE --where "tags contains 'project-x'"
```

---

## Decision: Not Implementing Now

**Reasons to defer:**

1. **Core WIP not yet complete** - Event replay, file storage phases pending
2. **Limited demand** - Pi 5 with 8GB is the stated target
3. **High effort** - SQLite rewrite is significant work
4. **Maintenance burden** - Two storage backends to maintain

**When to reconsider:**

1. Specific user request with clear use case
2. WIP core features complete and stable
3. Demand for truly embedded/offline scenarios

---

## Alternative: Minimal Profile

Instead of WIP Nano, consider a "WIP Minimal" profile that:
- Uses the existing codebase
- Drops optional modules (reporting, files, OIDC)
- Runs on lower-resource hardware

This is already partially supported via `--preset core`:

```bash
./scripts/setup.sh --preset core  # API key only, no OIDC/PostgreSQL/MinIO
```

This gets WIP down to:
- MongoDB + NATS + 4 services + Console
- ~800MB-1GB RAM
- Still requires Pi 4+ class hardware

---

## Summary

WIP Nano would be a valuable addition for edge/embedded use cases, but the implementation effort is substantial and the current target (Pi 5/8GB) is well-served by the standard deployment.

**Recommendation:** Document this design, defer implementation until there's clear demand or the core system is fully mature. In the meantime, users with constrained resources can use the `core` preset to minimize footprint.
