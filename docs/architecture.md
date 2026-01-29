# System Architecture

## Overview

World In a Pie (WIP) follows a layered architecture with clear separation of concerns. This document describes the system architecture, component interactions, and data flows.

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              PRESENTATION LAYER                             │
├────────────────┬────────────────┬─────────────────┬────────────────────────┤
│   Ontology     │    Template    │     Admin       │     Query              │
│    Editor      │     Editor     │      UI         │    Builder             │
│                │                │                 │                        │
│  (Def-Store    │  (Template     │  (All stores    │  (Document Store       │
│   management)  │   management)  │   curation)     │   queries)             │
└───────┬────────┴───────┬────────┴────────┬────────┴───────────┬────────────┘
        │                │                 │                    │
        └────────────────┴────────┬────────┴────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                               API LAYER                                     │
│                                                                             │
│                         FastAPI Application                                 │
│                                                                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐       │
│  │  Def-Store  │  │  Template   │  │  Document   │  │  Registry   │       │
│  │  Endpoints  │  │  Endpoints  │  │  Endpoints  │  │  Endpoints  │       │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘       │
│         │                │                │                │               │
│         └────────────────┴───────┬────────┴────────────────┘               │
│                                  │                                          │
│                                  ▼                                          │
│                      ┌─────────────────────┐                               │
│                      │  Validation Engine  │                               │
│                      └─────────────────────┘                               │
│                                                                             │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                             SERVICE LAYER                                   │
│                                                                             │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐          │
│  │   Auth Service   │  │  Version Service │  │  Archive Service │          │
│  │                  │  │                  │  │                  │          │
│  │  • JWT validate  │  │  • Version mgmt  │  │  • Policy engine │          │
│  │  • API key auth  │  │  • History track │  │  • Age-based     │          │
│  │  • RBAC enforce  │  │  • Deactivation  │  │  • Volume-based  │          │
│  └──────────────────┘  └──────────────────┘  └──────────────────┘          │
│                                                                             │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐          │
│  │  Identity Svc    │  │  Sync Service    │  │  Event Service   │          │
│  │                  │  │                  │  │                  │          │
│  │  • Hash compute  │  │  • Batch sync    │  │  • NATS publish  │          │
│  │  • Key normalize │  │  • Event-driven  │  │  • Event consume │          │
│  │  • Upsert logic  │  │  • Queue-based   │  │  • Notifications │          │
│  └──────────────────┘  └──────────────────┘  └──────────────────┘          │
│                                                                             │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           PERSISTENCE LAYER                                 │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                     Storage Abstraction Layer                        │   │
│  │                                                                      │   │
│  │   DocumentStore interface    │    ReportingStore interface          │   │
│  │   • save(doc) → ID           │    • sync(documents) → void          │   │
│  │   • get(id) → Document       │    • query(sql) → ResultSet          │   │
│  │   • query(filter) → []       │                                      │   │
│  │   • getVersions(id) → []     │                                      │   │
│  └──────────────┬───────────────┴──────────────────┬────────────────────┘   │
│                 │                                  │                        │
│                 ▼                                  ▼                        │
│  ┌──────────────────────────────┐  ┌──────────────────────────────┐        │
│  │      Document Stores         │  │      Reporting Stores        │        │
│  │                              │  │                              │        │
│  │  • MongoDB (default)         │  │  • PostgreSQL (default)      │        │
│  │  • PostgreSQL JSONB          │  │  • MySQL                     │        │
│  │  • SQLite + JSON             │  │  • SQLite                    │        │
│  │  • CouchDB                   │  │                              │        │
│  └──────────────────────────────┘  └──────────────────────────────┘        │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Core Data Stores

### Three-Tier Persistence Model

```
┌─────────────────────────────────────────────────────────────────┐
│                     PERSISTENCE HIERARCHY                        │
├─────────────────┬─────────────────────┬─────────────────────────┤
│                 │                     │                         │
│   DEF-STORE     │   TEMPLATE STORE    │   DOCUMENT STORE        │
│   (Foundation)  │   (Schema)          │   (Data)                │
│                 │                     │                         │
│   ┌───────────┐ │   ┌───────────────┐ │   ┌─────────────────┐   │
│   │Terminology│ │   │   Template    │ │   │    Document     │   │
│   ├───────────┤ │   ├───────────────┤ │   ├─────────────────┤   │
│   │• id       │ │   │• id           │ │   │• id             │   │
│   │• name     │ │   │• name         │ │   │• template_id    │   │
│   │• version  │ │   │• version      │ │   │• version        │   │
│   │• terms[]  │ │   │• fields[]     │ │   │• identity_hash  │   │
│   └───────────┘ │   │• rules[]      │ │   │• data{}         │   │
│                 │   │• identity_flds│ │   │• status         │   │
│   ┌───────────┐ │   │• extends      │ │   └─────────────────┘   │
│   │   Term    │ │   └───────────────┘ │                         │
│   ├───────────┤ │                     │                         │
│   │• id       │ │   ┌───────────────┐ │                         │
│   │• code     │ │   │ Template Field│ │                         │
│   │• label    │ │   ├───────────────┤ │                         │
│   │• parent   │ │   │• name         │ │                         │
│   │• metadata │ │   │• term_ref     │ │                         │
│   └───────────┘ │   │• mandatory    │ │                         │
│                 │   │• conditions[] │ │                         │
│                 │   └───────────────┘ │                         │
│                 │                     │                         │
├─────────────────┴─────────────────────┴─────────────────────────┤
│                                                                  │
│  DEPENDENCY FLOW:  Def-Store ──► Template Store ──► Document    │
│                                                      Store       │
│                                                                  │
│  • Templates MUST reference valid Def-Store entries             │
│  • Documents MUST conform to valid Templates                    │
│  • Each layer is versioned independently                        │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### Store Responsibilities

| Store | Contains | Validates Against | Changed By |
|-------|----------|-------------------|------------|
| **Def-Store** | Ontologies, Terminologies, Terms | Bootstrap schema | Administrators |
| **Template Store** | Templates, Fields, Rules | Def-Store | Data Architects |
| **Document Store** | Documents, Data | Templates | Users/Systems |

---

## Validation Flow

```
                    DOCUMENT SUBMISSION
                           │
                           ▼
              ┌────────────────────────┐
              │   1. Parse Document    │
              │   (JSON validation)    │
              └───────────┬────────────┘
                          │
                          ▼
              ┌────────────────────────┐
              │  2. Resolve Template   │
              │  (exists? active?)     │
              └───────────┬────────────┘
                          │
                          ▼
              ┌────────────────────────┐
              │  3. Validate Fields    │
              │  • Required present?   │
              │  • Types correct?      │
              │  • Terms valid?        │
              └───────────┬────────────┘
                          │
                          ▼
              ┌────────────────────────┐
              │   4. Evaluate Rules    │
              │  • Conditional logic   │
              │  • Cross-field rules   │
              └───────────┬────────────┘
                          │
                          ▼
              ┌────────────────────────┐
              │  5. Compute Identity   │
              │  • Extract ID fields   │
              │  • Sort & hash         │
              └───────────┬────────────┘
                          │
                          ▼
              ┌────────────────────────┐
              │   6. Check Existing    │
              │  • Same identity?      │
              │  • Update or Create?   │
              └───────────┬────────────┘
                          │
              ┌───────────┴───────────┐
              ▼                       ▼
     ┌──────────────┐        ┌──────────────┐
     │    CREATE    │        │    UPDATE    │
     │              │        │              │
     │ New document │        │ New version  │
     │ New identity │        │ Old → inactive│
     └──────────────┘        └──────────────┘
                          │
                          ▼
              ┌────────────────────────┐
              │    7. Emit Event       │
              │  (for sync/reporting)  │
              └────────────────────────┘
```

---

## Registry Architecture

The Registry is a **standalone service** that provides federated identity management.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              REGISTRY                                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   REGISTRATION                                                               │
│   ═══════════                                                               │
│                                                                              │
│   ┌─────────────────┐         ┌─────────────────┐                           │
│   │ Composite Key   │         │   Registration  │                           │
│   │                 │ ──────► │                 │                           │
│   │ {name: "Alice", │         │ • Generate ID   │                           │
│   │  city: "Berlin"}│         │ • Store mapping │                           │
│   │                 │         │ • Record source │                           │
│   │ Source: wip-eu  │         │                 │                           │
│   └─────────────────┘         └────────┬────────┘                           │
│                                        │                                     │
│                                        ▼                                     │
│                               ┌─────────────────┐                           │
│                               │ Return: UUID    │                           │
│                               │ 550e8400-e29b...│                           │
│                               └─────────────────┘                           │
│                                                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   LOOKUP                                                                     │
│   ══════                                                                    │
│                                                                              │
│   Mode 1: Source Lookup                Mode 2: Proxy Query                  │
│   ─────────────────────                ─────────────────────                │
│                                                                              │
│   ┌──────────┐    ┌──────────┐        ┌──────────┐    ┌──────────┐         │
│   │  Query   │    │ Response │        │  Query   │    │ Response │         │
│   │          │    │          │        │          │    │          │         │
│   │ ID: xyz  │───►│ Source:  │        │ ID: xyz  │───►│ Document │         │
│   │          │    │ wip-eu   │        │ proxy:   │    │ data     │         │
│   └──────────┘    └──────────┘        │ true     │    │ from     │         │
│                                       └──────────┘    │ source   │         │
│   Client then queries                                 └──────────┘         │
│   wip-eu directly                     Registry forwards                    │
│                                       to source, returns result            │
│                                                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   STORAGE                                                                    │
│   ═══════                                                                   │
│                                                                              │
│   ┌────────────────────────────────────────────────────────────────┐        │
│   │  Registry Entry                                                 │        │
│   ├────────────────────────────────────────────────────────────────┤        │
│   │  • id: UUID (or pluggable format)                              │        │
│   │  • composite_key_hash: sha256 of normalized key                │        │
│   │  • composite_key_values: {name: "Alice", city: "Berlin"}       │        │
│   │  • source_system: "wip-eu"                                     │        │
│   │  • source_endpoint: "https://eu.wip.example.com/api"           │        │
│   │  • registered_at: timestamp                                    │        │
│   │  • status: active | inactive                                   │        │
│   └────────────────────────────────────────────────────────────────┘        │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Registry Use Cases

| Scenario | Registry Role |
|----------|---------------|
| **Single WIP instance** | Optional; provides stable IDs |
| **Multiple WIP instances** | Central identity resolution |
| **System migration** | Update source mapping; IDs remain stable |
| **System merger** | Point old sources to new combined system |
| **Cross-system query** | Locate which system has a given entity |

---

## Versioning Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           VERSION MANAGEMENT                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   DOCUMENT LIFECYCLE                                                         │
│                                                                              │
│   ┌─────────┐   update    ┌─────────┐   update    ┌─────────┐              │
│   │ Doc v1  │ ──────────► │ Doc v2  │ ──────────► │ Doc v3  │              │
│   │ ACTIVE  │             │ ACTIVE  │             │ ACTIVE  │              │
│   └────┬────┘             └────┬────┘             └─────────┘              │
│        │                       │                                            │
│        ▼                       ▼                                            │
│   ┌─────────┐             ┌─────────┐                                       │
│   │ Doc v1  │             │ Doc v2  │                                       │
│   │INACTIVE │             │INACTIVE │                                       │
│   └────┬────┘             └─────────┘                                       │
│        │                                                                     │
│        ▼ (optional, policy-based)                                           │
│   ┌─────────┐                                                               │
│   │ Doc v1  │                                                               │
│   │ARCHIVED │                                                               │
│   └─────────┘                                                               │
│                                                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   VERSION RECORD                                                             │
│                                                                              │
│   ┌──────────────────────────────────────────────────────────────────┐      │
│   │  Document Version                                                 │      │
│   ├──────────────────────────────────────────────────────────────────┤      │
│   │  • id: unique version ID                                         │      │
│   │  • identity_hash: links all versions of same entity              │      │
│   │  • version_number: sequential (1, 2, 3...)                       │      │
│   │  • status: active | inactive | archived                          │      │
│   │  • created_at: timestamp                                         │      │
│   │  • created_by: user/system ID                                    │      │
│   │  • template_id: template used (may differ across versions)       │      │
│   │  • data: actual document content                                 │      │
│   └──────────────────────────────────────────────────────────────────┘      │
│                                                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ARCHIVE POLICIES                                                           │
│                                                                              │
│   ┌───────────────────────────────────────────────────────────────────┐     │
│   │                                                                    │     │
│   │  Age-based:     "Archive versions older than 2 years"             │     │
│   │                                                                    │     │
│   │  Volume-based:  "Archive when store exceeds 100GB"                │     │
│   │                                                                    │     │
│   │  Template-based: "Archive 'log-entry' versions after 30 days"    │     │
│   │                                                                    │     │
│   │  Combined:      "Archive 'audit-log' older than 1 year OR        │     │
│   │                  when audit store exceeds 50GB"                   │     │
│   │                                                                    │     │
│   └───────────────────────────────────────────────────────────────────┘     │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Authentication & Authorization

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              AUTH ARCHITECTURE                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ┌─────────────────┐              ┌─────────────────────────┐              │
│   │    Vue UI       │◄────────────►│   Authentik / Authelia  │              │
│   │                 │    OIDC      │                         │              │
│   └────────┬────────┘              │   • User authentication │              │
│            │                       │   • Role management     │              │
│            │ JWT                   │   • Group management    │              │
│            │                       │   • Session management  │              │
│            ▼                       └─────────────────────────┘              │
│   ┌─────────────────────────────────────────────────────────┐               │
│   │                    FastAPI Backend                       │               │
│   │                                                          │               │
│   │  ┌──────────────────────────────────────────────────┐   │               │
│   │  │              Auth Middleware                      │   │               │
│   │  │                                                   │   │               │
│   │  │  User requests:                                   │   │               │
│   │  │  • Validate JWT signature                         │   │               │
│   │  │  • Extract roles from claims                      │   │               │
│   │  │  • Enforce RBAC on endpoints                      │   │               │
│   │  │                                                   │   │               │
│   │  │  System requests (Registry):                      │   │               │
│   │  │  • Validate API key                               │   │               │
│   │  │  • Map to system identity                         │   │               │
│   │  └──────────────────────────────────────────────────┘   │               │
│   │                                                          │               │
│   └─────────────────────────────────────────────────────────┘               │
│                                                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ROLE-BASED ACCESS CONTROL                                                  │
│                                                                              │
│   ┌──────────────┬──────────────┬──────────────┬──────────────┐             │
│   │    Role      │  Def-Store   │  Templates   │  Documents   │             │
│   ├──────────────┼──────────────┼──────────────┼──────────────┤             │
│   │ admin        │ CRUD         │ CRUD         │ CRUD         │             │
│   │ architect    │ Read         │ CRUD         │ Read         │             │
│   │ editor       │ Read         │ Read         │ CRUD         │             │
│   │ viewer       │ Read         │ Read         │ Read         │             │
│   │ system       │ Read         │ Read         │ CRUD (API)   │             │
│   └──────────────┴──────────────┴──────────────┴──────────────┘             │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Reporting Layer Sync

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         REPORTING SYNC ARCHITECTURE                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ┌─────────────────┐                              ┌─────────────────┐      │
│   │  Document Store │                              │ Reporting Store │      │
│   │    (MongoDB)    │ ────── SYNC MODES ─────────► │  (PostgreSQL)   │      │
│   └─────────────────┘                              └─────────────────┘      │
│                                                                              │
│   ══════════════════════════════════════════════════════════════════════    │
│                                                                              │
│   MODE 1: BATCH                                                              │
│   ─────────────────                                                         │
│   ┌──────────┐     Scheduled      ┌──────────┐     Transform    ┌────────┐ │
│   │  Source  │ ──────────────────►│  Extract │ ───────────────► │  Load  │ │
│   └──────────┘     (cron)         └──────────┘                  └────────┘ │
│                                                                              │
│   • Simple implementation                                                    │
│   • Suitable for reporting that tolerates hours of lag                      │
│   • Low resource usage                                                       │
│                                                                              │
│   ══════════════════════════════════════════════════════════════════════    │
│                                                                              │
│   MODE 2: EVENT-DRIVEN                                                       │
│   ─────────────────────                                                     │
│   ┌──────────┐     Document      ┌──────────┐     Sync         ┌────────┐  │
│   │  Source  │ ── Created/   ───►│  Handler │ ───────────────► │  Load  │  │
│   └──────────┘     Updated       └──────────┘                  └────────┘  │
│                                                                              │
│   • Moderate latency (seconds to minutes)                                   │
│   • Good balance of freshness and resource usage                            │
│                                                                              │
│   ══════════════════════════════════════════════════════════════════════    │
│                                                                              │
│   MODE 3: MESSAGE QUEUE (Near Real-Time)                                    │
│   ──────────────────────────────────────                                    │
│   ┌──────────┐                  ┌──────────┐                  ┌────────┐   │
│   │  Source  │ ── publish ────► │   NATS   │ ── subscribe ──► │  Sync  │   │
│   └──────────┘                  └──────────┘                  │  Worker│   │
│                                                               └────────┘   │
│                                                                              │
│   • Lowest latency (sub-second possible)                                    │
│   • Client responsible for choosing and configuring                         │
│   • Higher resource usage                                                    │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Deployment Architecture

### Docker Compose (Raspberry Pi / Development)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              DOCKER COMPOSE                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │                           docker-compose.yml                         │   │
│   ├─────────────────────────────────────────────────────────────────────┤   │
│   │                                                                      │   │
│   │   ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐       │   │
│   │   │  traefik  │  │    api    │  │    ui     │  │ authentik │       │   │
│   │   │  (proxy)  │  │ (FastAPI) │  │  (Vue)    │  │  (auth)   │       │   │
│   │   │  :80/:443 │  │  :8000    │  │  :3000    │  │  :9000    │       │   │
│   │   └───────────┘  └───────────┘  └───────────┘  └───────────┘       │   │
│   │                                                                      │   │
│   │   ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐       │   │
│   │   │  mongodb  │  │ postgres  │  │   nats    │  │  registry │       │   │
│   │   │ (docs)    │  │(reporting)│  │ (events)  │  │ (identity)│       │   │
│   │   │  :27017   │  │  :5432    │  │  :4222    │  │  :8001    │       │   │
│   │   └───────────┘  └───────────┘  └───────────┘  └───────────┘       │   │
│   │                                                                      │   │
│   │   Volumes:                                                           │   │
│   │   • wip-mongodb-data                                                 │   │
│   │   • wip-postgres-data                                                │   │
│   │   • wip-authentik-data                                               │   │
│   │                                                                      │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### MicroK8s (Demo / Production)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                               MICROK8S                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   Namespace: wip                                                             │
│                                                                              │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │  Deployments                                                         │   │
│   │                                                                      │   │
│   │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                  │   │
│   │  │ wip-api     │  │ wip-ui      │  │ wip-registry│                  │   │
│   │  │ replicas: 2 │  │ replicas: 2 │  │ replicas: 1 │                  │   │
│   │  └─────────────┘  └─────────────┘  └─────────────┘                  │   │
│   │                                                                      │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │  StatefulSets                                                        │   │
│   │                                                                      │   │
│   │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                  │   │
│   │  │ mongodb     │  │ postgresql  │  │ nats        │                  │   │
│   │  │ replicas: 1 │  │ replicas: 1 │  │ replicas: 1 │                  │   │
│   │  └─────────────┘  └─────────────┘  └─────────────┘                  │   │
│   │                                                                      │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │  Services & Ingress                                                  │   │
│   │                                                                      │   │
│   │  Ingress: wip.local                                                  │   │
│   │    /        → wip-ui                                                 │   │
│   │    /api     → wip-api                                                │   │
│   │    /auth    → authentik                                              │   │
│   │                                                                      │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Component Communication

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         COMMUNICATION PATTERNS                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   SYNCHRONOUS (REST)                                                         │
│   ══════════════════                                                        │
│                                                                              │
│   UI ──────► API ──────► Document Store                                     │
│      HTTP        Driver                                                      │
│                                                                              │
│   API ──────► Registry (lookup/register)                                    │
│       HTTP                                                                   │
│                                                                              │
│   Registry ──────► Source WIP (proxy queries)                               │
│            HTTP                                                              │
│                                                                              │
│   ────────────────────────────────────────────────────────────────────────  │
│                                                                              │
│   ASYNCHRONOUS (NATS)                                                        │
│   ═══════════════════                                                       │
│                                                                              │
│   API ── publish ──► NATS ── subscribe ──► Sync Worker                      │
│      "doc.created"              │              │                            │
│      "doc.updated"              │              ▼                            │
│                                 │       Reporting Store                     │
│                                 │                                            │
│                                 └── subscribe ──► Notification Worker       │
│                                                          │                  │
│                                                          ▼                  │
│                                                     External Systems        │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Security Boundaries

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           SECURITY BOUNDARIES                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │                         PUBLIC ZONE                                  │   │
│   │                                                                      │   │
│   │   • UI (static assets)                                               │   │
│   │   • Auth provider login page                                         │   │
│   │                                                                      │   │
│   └───────────────────────────────┬─────────────────────────────────────┘   │
│                                   │ Authenticated requests only             │
│                                   ▼                                         │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │                         API ZONE                                     │   │
│   │                                                                      │   │
│   │   • FastAPI application                                              │   │
│   │   • JWT validation                                                   │   │
│   │   • RBAC enforcement                                                 │   │
│   │   • API key validation (system-to-system)                            │   │
│   │                                                                      │   │
│   └───────────────────────────────┬─────────────────────────────────────┘   │
│                                   │ Internal only                           │
│                                   ▼                                         │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │                         DATA ZONE                                    │   │
│   │                                                                      │   │
│   │   • MongoDB                                                          │   │
│   │   • PostgreSQL                                                       │   │
│   │   • NATS                                                             │   │
│   │   • No external access                                               │   │
│   │                                                                      │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Next Steps

See related documentation:
- [Components](components.md) — detailed component specifications
- [Technology Stack](technology-stack.md) — technology choices and rationale
- [Deployment](deployment.md) — deployment configurations
- [Data Models](data-models.md) — conceptual data structures
