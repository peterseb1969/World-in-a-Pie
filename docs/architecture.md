# System Architecture

## Overview

World In a Pie (WIP) follows a microservices architecture with clear separation of concerns. Each component is a standalone FastAPI service with its own API, communicating via REST and NATS message queue.

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              PRESENTATION LAYER                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│                            WIP Console (Vue 3 + PrimeVue)                   │
│                                                                             │
│   ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐            │
│   │  Terminology    │  │    Template     │  │    Document     │            │
│   │   Management    │  │   Management    │  │   Management    │            │
│   │                 │  │                 │  │                 │            │
│   │  • List/CRUD    │  │  • List/CRUD    │  │  • List/CRUD    │            │
│   │  • Import/Export│  │  • Fields/Rules │  │  • Validation   │            │
│   │  • Validation   │  │  • Inheritance  │  │  • Versioning   │            │
│   └─────────────────┘  └─────────────────┘  └─────────────────┘            │
│                                                                             │
│   Access: https://localhost:8443 (via Caddy reverse proxy)                 │
│                                                                             │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           REVERSE PROXY (Caddy)                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   :8443 (HTTPS with auto-generated TLS certificate)                        │
│                                                                             │
│   Route Mapping:                                                            │
│   ├── /                      → WIP Console (:3000)                         │
│   ├── /api/registry/         → Registry (:8001)                            │
│   ├── /api/def-store/        → Def-Store (:8002)                           │
│   ├── /api/template-store/   → Template Store (:8003)                      │
│   ├── /api/document-store/   → Document Store (:8004)                      │
│   ├── /api/reporting-sync/   → Reporting Sync (:8005)                      │
│   └── /dex/                  → Dex OIDC Provider (:5556)                   │
│                                                                             │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           MICROSERVICES LAYER                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐       │
│   │  Registry   │  │  Def-Store  │  │  Template   │  │  Document   │       │
│   │   :8001     │  │   :8002     │  │   Store     │  │   Store     │       │
│   │             │  │             │  │   :8003     │  │   :8004     │       │
│   │ • Namespaces│  │ • Terms     │  │ • Templates │  │ • Documents │       │
│   │ • ID gen    │  │ • Aliases   │  │ • Fields    │  │ • Versions  │       │
│   │ • Synonyms  │  │ • Hierarchy │  │ • Rules     │  │ • Validation│       │
│   └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘       │
│          │                │                │                │               │
│          │         ◄──────┘                │                │               │
│          │◄────────────────────────────────┘                │               │
│          │◄─────────────────────────────────────────────────┘               │
│          │                                                                  │
│   ┌──────┴──────────────────────────────────────────────────────────┐      │
│   │                    Reporting Sync :8005                          │      │
│   │                                                                  │      │
│   │  • NATS consumer (document/template events)                      │      │
│   │  • PostgreSQL schema generation                                  │      │
│   │  • Document transformation and sync                              │      │
│   │  • Batch sync and recovery                                       │      │
│   │  • Metrics and alerting                                          │      │
│   └──────────────────────────────────────────────────────────────────┘      │
│                                                                             │
│   All services use wip-auth library for pluggable authentication           │
│                                                                             │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           PERSISTENCE LAYER                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌─────────────────────────┐  ┌─────────────────────────┐                 │
│   │   MongoDB :27017        │  │   PostgreSQL :5432      │                 │
│   │   (Document Store)      │  │   (Reporting Store)     │                 │
│   │                         │  │                         │                 │
│   │   Databases:            │  │   Database:             │                 │
│   │   • wip_registry        │  │   • wip_reporting       │                 │
│   │   • wip_def_store       │  │                         │                 │
│   │   • wip_template_store  │  │   Tables auto-generated │                 │
│   │   • wip_document_store  │  │   from templates:       │                 │
│   │                         │  │   • doc_<template_code> │                 │
│   └─────────────────────────┘  └─────────────────────────┘                 │
│                                                                             │
│   ┌─────────────────────────┐                                              │
│   │   MinIO :9000/:9001     │                                              │
│   │   (Object Storage)      │                                              │
│   │                         │                                              │
│   │   S3-compatible file    │                                              │
│   │   storage for binary    │                                              │
│   │   attachments           │                                              │
│   │   (FILE-XXXXXX IDs)     │                                              │
│   └─────────────────────────┘                                              │
│                                                                             │
│   ┌─────────────────────────┐  ┌─────────────────────────┐                 │
│   │   NATS :4222            │  │   Dex :5556             │                 │
│   │   (Message Queue)       │  │   (OIDC Provider)       │                 │
│   │                         │  │                         │                 │
│   │   JetStream enabled     │  │   Static users:         │                 │
│   │   for persistence       │  │   • admin@wip.local     │                 │
│   │                         │  │   • editor@wip.local    │                 │
│   │   Subjects:             │  │   • viewer@wip.local    │                 │
│   │   • wip.documents.*     │  │                         │                 │
│   │   • wip.templates.*     │  │   Groups for RBAC       │                 │
│   └─────────────────────────┘  └─────────────────────────┘                 │
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
│   │• id       │ │   │• id           │ │   │• document_id    │   │
│   │• code     │ │   │• code         │ │   │• template_id    │   │
│   │• name     │ │   │• name         │ │   │• version        │   │
│   │• status   │ │   │• version      │ │   │• identity_hash  │   │
│   └───────────┘ │   │• fields[]     │ │   │• data{}         │   │
│                 │   │• rules[]      │ │   │• term_references│   │
│   ┌───────────┐ │   │• identity_flds│ │   │• status         │   │
│   │   Term    │ │   │• extends      │ │   └─────────────────┘   │
│   ├───────────┤ │   │• reporting{}  │ │                         │
│   │• term_id  │ │   └───────────────┘ │                         │
│   │• code     │ │                     │                         │
│   │• value    │ │                     │                         │
│   │• aliases[]│ │                     │                         │
│   │• parent   │ │                     │                         │
│   └───────────┘ │                     │                         │
│                 │                     │                         │
├─────────────────┴─────────────────────┴─────────────────────────┤
│                                                                  │
│  DEPENDENCY FLOW:  Def-Store ──► Template Store ──► Document    │
│                                                      Store       │
│                                                                  │
│  • Templates MUST reference valid Def-Store terminologies       │
│  • Documents MUST conform to valid Templates                    │
│  • Each layer is versioned independently                        │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### Store Responsibilities

| Store | Contains | Validates Against | Changed By |
|-------|----------|-------------------|------------|
| **Def-Store** | Terminologies, Terms, Aliases | Bootstrap schema | Administrators |
| **Template Store** | Templates, Fields, Rules | Def-Store | Data Architects |
| **Document Store** | Documents, Data, Versions | Templates + Def-Store | Users/Systems |

---

## Service Communication

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         COMMUNICATION PATTERNS                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   SYNCHRONOUS (REST over HTTP)                                               │
│   ════════════════════════════                                              │
│                                                                              │
│   Document Store ──► Template Store (fetch template for validation)         │
│        :8004              :8003                                             │
│                                                                              │
│   Document Store ──► Def-Store (validate term values)                       │
│        :8004            :8002                                               │
│                                                                              │
│   Template Store ──► Def-Store (validate terminology references)            │
│        :8003            :8002                                               │
│                                                                              │
│   All Services ──► Registry (generate IDs)                                  │
│                      :8001                                                   │
│                                                                              │
│   ────────────────────────────────────────────────────────────────────────  │
│                                                                              │
│   ASYNCHRONOUS (NATS JetStream)                                              │
│   ═════════════════════════════                                             │
│                                                                              │
│   Document Store ── publish ──► NATS ── subscribe ──► Reporting Sync        │
│       :8004       "wip.documents.created"              :8005                 │
│                   "wip.documents.updated"                  │                 │
│                   "wip.documents.deleted"                  ▼                 │
│                                                     PostgreSQL               │
│   Template Store ── publish ──► NATS ── subscribe ──► Reporting Sync        │
│       :8003       "wip.templates.created"              :8005                 │
│                   "wip.templates.updated"                  │                 │
│                                                            ▼                 │
│                                                     Schema Creation          │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Validation Flow

```
                    DOCUMENT SUBMISSION
                           │
                           ▼
              ┌────────────────────────┐
              │  1. Structural Check   │
              │  (JSON validation)     │
              └───────────┬────────────┘
                          │
                          ▼
              ┌────────────────────────┐
              │  2. Template Resolution│
              │  (fetch from Template  │
              │   Store, resolve       │
              │   inheritance)         │
              └───────────┬────────────┘
                          │
                          ▼
              ┌────────────────────────┐
              │  3. Field Validation   │
              │  • Required present?   │
              │  • Types correct?      │
              │  • Nested objects?     │
              └───────────┬────────────┘
                          │
                          ▼
              ┌────────────────────────┐
              │  4. Term Validation    │
              │  (bulk API call to     │
              │   Def-Store)           │
              └───────────┬────────────┘
                          │
                          ▼
              ┌────────────────────────┐
              │  5. Rule Evaluation    │
              │  • conditional_required│
              │  • conditional_value   │
              │  • mutual_exclusion    │
              │  • dependency          │
              └───────────┬────────────┘
                          │
                          ▼
              ┌────────────────────────┐
              │  6. Identity Compute   │
              │  • Extract ID fields   │
              │  • Sort & SHA-256 hash │
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
              │  7. Publish Event      │
              │  (NATS: wip.documents  │
              │   .created/updated)    │
              └────────────────────────┘
```

---

## Registry Architecture

The Registry is a **standalone service** that provides ID generation and namespace management.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              REGISTRY :8001                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   NAMESPACES                                                                 │
│   ══════════                                                                │
│                                                                              │
│   ┌────────────────────────────────────────────────────────────────┐        │
│   │  WIP Internal Namespaces                                        │        │
│   ├────────────────────────────────────────────────────────────────┤        │
│   │  • wip-terminologies  │  Prefix: TERM-  │  For Def-Store       │        │
│   │  • wip-terms          │  Prefix: T-     │  For Def-Store       │        │
│   │  • wip-templates      │  Prefix: TPL-   │  For Template Store  │        │
│   │  • wip-documents      │  UUID7          │  For Document Store  │        │
│   │  • wip-files          │  Prefix: FILE-  │  For File Storage    │        │
│   │  • default            │  UUID4          │  General use         │        │
│   └────────────────────────────────────────────────────────────────┘        │
│                                                                              │
│   ID GENERATION                                                              │
│   ═════════════                                                             │
│                                                                              │
│   POST /api/registry/ids                                                     │
│   {                                                                          │
│     "pool_id": "wip-templates",                                            │
│     "composite_key": {"code": "PERSON", "version": 1}                       │
│   }                                                                          │
│                                                                              │
│   Response: {"id": "TPL-000001", "composite_key_hash": "abc123..."}         │
│                                                                              │
│   SYNONYMS (Federated Identity)                                              │
│   ═════════════════════════════                                             │
│                                                                              │
│   Link external IDs to WIP IDs:                                              │
│   • legacy_system:EMP-12345 → TPL-000042                                    │
│   • external_api:user_abc   → DOC-0192abc...                                │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Versioning Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           VERSION MANAGEMENT                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   DOCUMENT VERSIONING (Identity-based)                                       │
│   ═════════════════════════════════════                                     │
│                                                                              │
│   Same identity_hash = new version, old version deactivated                 │
│                                                                              │
│   ┌─────────┐   update    ┌─────────┐   update    ┌─────────┐              │
│   │ Doc v1  │ ──────────► │ Doc v2  │ ──────────► │ Doc v3  │              │
│   │ ACTIVE  │             │ ACTIVE  │             │ ACTIVE  │              │
│   │ id: A   │             │ id: B   │             │ id: C   │              │
│   └────┬────┘             └────┬────┘             └─────────┘              │
│        │                       │                                            │
│        ▼                       ▼                                            │
│   ┌─────────┐             ┌─────────┐                                       │
│   │ Doc v1  │             │ Doc v2  │                                       │
│   │INACTIVE │             │INACTIVE │                                       │
│   │ id: A   │             │ id: B   │                                       │
│   └─────────┘             └─────────┘                                       │
│                                                                              │
│   All versions share same identity_hash                                     │
│   Each version has unique document_id (UUID7)                               │
│                                                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   TEMPLATE VERSIONING (Multi-version active)                                 │
│   ══════════════════════════════════════════                                │
│                                                                              │
│   Multiple versions can be active simultaneously for gradual migration      │
│                                                                              │
│   ┌─────────┐   update    ┌─────────┐   update    ┌─────────┐              │
│   │ TPL v1  │ ──────────► │ TPL v2  │ ──────────► │ TPL v3  │              │
│   │ ACTIVE  │             │ ACTIVE  │             │ ACTIVE  │              │
│   │TPL-0001 │             │TPL-0002 │             │TPL-0003 │              │
│   └─────────┘             └─────────┘             └─────────┘              │
│        │                       │                       │                    │
│   Still active!           Still active!           Current                   │
│   (legacy docs)           (migrating)                                       │
│                                                                              │
│   All versions share same code (e.g., "PERSON")                             │
│   Each version has unique template_id                                       │
│                                                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   TERM TRACKING (No versioning, audit log instead)                          │
│   ═════════════════════════════════════════════════                         │
│                                                                              │
│   Terms represent concepts - changes tracked in audit log                   │
│   term_id is stable; value/aliases can change                               │
│                                                                              │
│   Audit log records: created, updated, deprecated, deleted                  │
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
│   │   WIP Console   │◄────────────►│       Dex OIDC          │              │
│   │   (Vue 3)       │    OIDC      │       :5556             │              │
│   └────────┬────────┘   (PKCE)     │                         │              │
│            │                       │   • Static users (YAML) │              │
│            │ JWT (Authorization    │   • Groups for RBAC     │              │
│            │      Bearer token)    │   • ~30MB RAM           │              │
│            │                       │   • Works over HTTP     │              │
│            ▼                       └─────────────────────────┘              │
│   ┌─────────────────────────────────────────────────────────┐               │
│   │                    wip-auth Library                      │               │
│   │                 (shared by all services)                 │               │
│   │                                                          │               │
│   │  Auth Modes:                                             │               │
│   │  • none         - No auth (development)                  │               │
│   │  • api_key_only - X-API-Key header                       │               │
│   │  • jwt_only     - Bearer token from OIDC                 │               │
│   │  • dual         - Both (default)                         │               │
│   │                                                          │               │
│   │  Providers:                                              │               │
│   │  ┌──────────────────┐  ┌──────────────────┐             │               │
│   │  │  APIKeyProvider  │  │   OIDCProvider   │             │               │
│   │  │                  │  │                  │             │               │
│   │  │  • Named keys    │  │  • JWT validate  │             │               │
│   │  │  • Owner/groups  │  │  • JWKS fetch    │             │               │
│   │  │  • Config-based  │  │  • Claims extract│             │               │
│   │  └──────────────────┘  └──────────────────┘             │               │
│   │                                                          │               │
│   │  Dependencies:                                           │               │
│   │  • require_identity()  - Any auth required               │               │
│   │  • require_groups([])  - Specific group required         │               │
│   │  • require_admin()     - wip-admins group required       │               │
│   │  • optional_identity() - Auth optional                   │               │
│   │                                                          │               │
│   └─────────────────────────────────────────────────────────┘               │
│                                                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   TEST USERS (Dex static config)                                             │
│                                                                              │
│   ┌──────────────────┬──────────────┬──────────────┐                        │
│   │    Email         │   Password   │    Group     │                        │
│   ├──────────────────┼──────────────┼──────────────┤                        │
│   │ admin@wip.local  │ admin123     │ wip-admins   │                        │
│   │ editor@wip.local │ editor123    │ wip-editors  │                        │
│   │ viewer@wip.local │ viewer123    │ wip-viewers  │                        │
│   └──────────────────┴──────────────┴──────────────┘                        │
│                                                                              │
│   API Key: dev_master_key_for_testing (development)                         │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Reporting Sync Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         REPORTING SYNC :8005                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   EVENT-DRIVEN SYNC (Primary)                                                │
│   ═══════════════════════════                                               │
│                                                                              │
│   Document Store                     Reporting Sync                          │
│       :8004                             :8005                                │
│         │                                 │                                  │
│         │  save doc                       │                                  │
│         ├──► MongoDB                      │                                  │
│         │                                 │                                  │
│         └──► NATS ────────────────────────┤                                  │
│              wip.documents.created        │                                  │
│              (full document in event)     │                                  │
│                                           ▼                                  │
│                                    ┌──────────────┐                          │
│                                    │  Transform   │                          │
│                                    │  • Flatten   │                          │
│                                    │  • Type map  │                          │
│                                    └──────┬───────┘                          │
│                                           │                                  │
│                                           ▼                                  │
│                                    PostgreSQL                                │
│                                    UPSERT with                               │
│                                    version check                             │
│                                                                              │
│   ────────────────────────────────────────────────────────────────────────  │
│                                                                              │
│   BATCH SYNC (Recovery/Initial Load)                                         │
│   ══════════════════════════════════                                        │
│                                                                              │
│   POST /sync/batch/{template_code}                                           │
│   POST /sync/batch                    (all templates)                        │
│                                                                              │
│   Reporting Sync ──► Document Store API ──► PostgreSQL                      │
│        :8005              :8004                                              │
│                    GET /documents?template_id=...                            │
│                                                                              │
│   ────────────────────────────────────────────────────────────────────────  │
│                                                                              │
│   SCHEMA MANAGEMENT                                                          │
│   ═════════════════                                                         │
│                                                                              │
│   Template Store ──► NATS ──► Reporting Sync                                │
│       :8003        wip.templates.created    :8005                           │
│                                               │                              │
│                                               ▼                              │
│                                    CREATE TABLE doc_<code>                   │
│                                    (columns from template fields)            │
│                                                                              │
│   Per-template configuration in template.reporting:                          │
│   • sync_enabled: true/false                                                 │
│   • sync_strategy: latest_only | all_versions | disabled                    │
│   • table_name: custom table name                                            │
│   • flatten_arrays: true/false                                               │
│                                                                              │
│   ────────────────────────────────────────────────────────────────────────  │
│                                                                              │
│   MONITORING                                                                 │
│   ══════════                                                                │
│                                                                              │
│   GET /metrics          - Latency, throughput, per-template stats           │
│   GET /metrics/consumer - NATS queue depth, pending messages                │
│   GET /alerts           - Active alerts and configuration                   │
│   PUT /alerts/config    - Configure thresholds and webhooks                 │
│                                                                              │
│   Alert types: queue_lag, error_rate, processing_stalled, connection_lost   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Deployment Architecture

### Deployment Profiles

| Profile | Target | Services | Auth | RAM |
|---------|--------|----------|------|-----|
| **mac** | Mac development | All + Mongo Express | Dex OIDC | ~1.2GB |
| **dev-minimal** | Quick testing | Core only | API keys | ~800MB |
| **pi-minimal** | Pi 4 (1-2GB) | Core only | API keys | ~800MB |
| **pi-standard** | Pi 4 (2-4GB) | All services | Dex OIDC | ~1GB |
| **pi-large** | Pi 5 (8GB+) | All + Mongo Express | Dex OIDC | ~1.2GB |

### Docker/Podman Compose Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         CONTAINER ARCHITECTURE                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   Network: wip-network (bridge)                                              │
│                                                                              │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │  Infrastructure (docker-compose.infra.yml)                          │   │
│   │                                                                      │   │
│   │  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐        │   │
│   │  │  mongodb  │  │ postgres  │  │   nats    │  │    dex    │        │   │
│   │  │  :27017   │  │  :5432    │  │  :4222    │  │  :5556    │        │   │
│   │  └───────────┘  └───────────┘  └───────────┘  └───────────┘        │   │
│   │                                                                      │   │
│   │  ┌───────────┐  ┌───────────┐                                       │   │
│   │  │   caddy   │  │mongo-expr │ (optional)                            │   │
│   │  │:8080/:8443│  │  :8081    │                                       │   │
│   │  └───────────┘  └───────────┘                                       │   │
│   │                                                                      │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │  Services (per-component docker-compose.yml)                        │   │
│   │                                                                      │   │
│   │  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐        │   │
│   │  │ registry  │  │ def-store │  │ template- │  │ document- │        │   │
│   │  │  :8001    │  │  :8002    │  │   store   │  │   store   │        │   │
│   │  │           │  │           │  │  :8003    │  │  :8004    │        │   │
│   │  └───────────┘  └───────────┘  └───────────┘  └───────────┘        │   │
│   │                                                                      │   │
│   │  ┌───────────┐  ┌───────────┐                                       │   │
│   │  │ reporting │  │    wip    │                                       │   │
│   │  │   -sync   │  │  console  │                                       │   │
│   │  │  :8005    │  │  :3000    │                                       │   │
│   │  └───────────┘  └───────────┘                                       │   │
│   │                                                                      │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│   Volumes (configurable via WIP_DATA_DIR):                                  │
│   • ${WIP_DATA_DIR}/mongodb    - Document store data                        │
│   • ${WIP_DATA_DIR}/postgres   - Reporting database                         │
│   • ${WIP_DATA_DIR}/nats       - Message queue persistence                  │
│   • ${WIP_DATA_DIR}/minio      - Binary file storage                        │
│   • ${WIP_DATA_DIR}/dex        - OIDC token storage                         │
│   • ${WIP_DATA_DIR}/caddy      - TLS certificates                           │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Kubernetes Deployment

WIP also supports Kubernetes deployment via manifests in the `k8s/` directory. Key differences from compose:
- Images must be self-contained (wip-auth baked in via `k8s/build-images.sh`)
- NGINX Ingress replaces Caddy for routing and TLS
- StatefulSets for infrastructure (MongoDB, PostgreSQL, NATS, MinIO)
- Deployments for application services
- `kubectl apply -k k8s/` for one-command deployment

See `k8s/README.md` for full instructions.

### Network Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| `localhost` | Only accessible from local machine | Mac development |
| `remote` | Accessible from network, localhost redirects to hostname | Pi, network access |

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
│   │   • Caddy reverse proxy (:8443 HTTPS)                               │   │
│   │   • WIP Console static assets                                        │   │
│   │   • Dex login page                                                   │   │
│   │                                                                      │   │
│   └───────────────────────────────┬─────────────────────────────────────┘   │
│                                   │ Authenticated requests only             │
│                                   │ (JWT or API Key)                        │
│                                   ▼                                         │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │                         API ZONE                                     │   │
│   │                                                                      │   │
│   │   • FastAPI microservices                                            │   │
│   │   • wip-auth middleware on each service                              │   │
│   │   • JWT validation via JWKS                                          │   │
│   │   • API key validation                                               │   │
│   │                                                                      │   │
│   └───────────────────────────────┬─────────────────────────────────────┘   │
│                                   │ Internal only (wip-network)             │
│                                   ▼                                         │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │                         DATA ZONE                                    │   │
│   │                                                                      │   │
│   │   • MongoDB (no external port exposure in production)               │   │
│   │   • PostgreSQL (no external port exposure in production)            │   │
│   │   • NATS (internal message passing)                                 │   │
│   │   • Dex (internal OIDC provider)                                    │   │
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
- [Authentication](authentication.md) — auth configuration and security
