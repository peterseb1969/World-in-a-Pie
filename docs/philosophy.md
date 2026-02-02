# Philosophy & Vision

## The Problem

Modern applications often face a fundamental tension:

1. **Rigid schemas** provide consistency but resist change
2. **Schemaless storage** provides flexibility but sacrifices consistency
3. **Domain-specific systems** solve one problem well but don't generalize
4. **Enterprise platforms** generalize but are heavy and expensive

Organizations end up with:
- Multiple disconnected systems for different data types
- Inconsistent data quality across systems
- Difficulty querying across data silos
- Vendor lock-in and high infrastructure costs

---

## The Vision

**World In a Pie (WIP)** resolves this tension by separating *structure definition* from *data storage*:

> Store anything, as long as it conforms to a template that references a shared vocabulary.

This creates a system that is:
- **As flexible as a document database** — any structure can be stored
- **As consistent as a relational database** — all data is validated
- **As portable as a file** — runs on a Raspberry Pi
- **As scalable as needed** — same architecture from Pi to cloud

---

## Core Principles

### 1. Separation of Concerns

WIP separates data management into three distinct layers:

| Layer | Purpose | Changes |
|-------|---------|---------|
| **Definitions** | What concepts exist | Rarely |
| **Templates** | How concepts combine | Occasionally |
| **Documents** | Actual data | Frequently |

This separation means:
- Changing a template doesn't require migrating existing data
- Adding new document types doesn't require new code
- Vocabulary remains consistent across all documents

### 2. Validate at the Gate

All data is validated at ingestion time against its declared template:

```
Document → Validation Engine → Template → Definitions
              │
              ├─ Valid: Store document
              └─ Invalid: Reject with detailed errors
```

Benefits:
- **Data quality is guaranteed**, not hoped for
- **Errors are caught early**, not discovered during analysis
- **Garbage in, garbage rejected** — not garbage stored

### 3. Never Delete, Always Version

Nothing in WIP is ever truly deleted:

| Action | What Happens |
|--------|--------------|
| Update | New version created; old version deactivated |
| Delete | Item marked as inactive (soft delete) |

Benefits:
- **Full audit trail** of all changes
- **Time travel** — query data as it existed at any point
- **Recovery** — accidental changes can be reversed
- **Compliance** — meets regulatory requirements for data retention

### 4. Identity is Fundamental

Every document has an identity based on template-defined fields:

```
Template defines: identity_fields = ["name", "city", "birthdate"]
                           ↓
Document submitted: {name: "Alice", city: "Berlin", birthdate: "1990-01-15"}
                           ↓
Identity hash: sha256("birthdate=1990-01-15|city=Berlin|name=Alice")
                           ↓
Same identity = Same entity = Update, not duplicate
```

The Registry extends this by:
- Mapping composite keys to stable IDs (UUID by default)
- Tracking which system owns each identity
- Enabling cross-system identity resolution

### 5. Federated by Design

WIP is designed to work as a single instance or as a federated network:

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   WIP #1    │     │   WIP #2    │     │   WIP #3    │
│  (Berlin)   │     │  (London)   │     │   (Tokyo)   │
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                   │
       └───────────────────┼───────────────────┘
                           │
                    ┌──────┴──────┐
                    │  Registry   │
                    │  (Central)  │
                    └─────────────┘
```

Each instance is autonomous; the Registry provides coordination:
- **Lookup**: "Who has entity X?" → "System Y"
- **Proxy**: "Get entity X" → (forwards to System Y) → result

### 6. Lightweight First

WIP must run on a Raspberry Pi. This constraint drives design decisions:

| Decision | Rationale |
|----------|-----------|
| MongoDB 4.4 for Pi | Supports ARMv8.0 architecture on Pi 4 |
| Dex for auth | ~30MB RAM vs Authentik's ~1.2GB |
| NATS over Kafka | ~30MB vs 500MB+ footprint |
| Caddy over nginx | Auto-TLS, simple config |
| Vue over React | Slightly smaller bundle |
| FastAPI over Django | Lighter, async by default |

If it runs on a Pi, it runs anywhere.

---

## Benefits

### For Developers

- **No schema migrations** — templates define structure, not database schemas
- **Self-documenting** — templates and definitions serve as documentation
- **API consistency** — same REST API regardless of data type
- **Type safety** — Pydantic validation catches errors at compile time

### For Data Managers

- **Single source of truth** — all definitions in one place
- **Controlled vocabulary** — terminology enforced across all data
- **Data quality** — validation prevents bad data entry
- **Audit trail** — full history of all changes

### For Organizations

- **Reduced silos** — one system for many data types
- **Lower costs** — runs on minimal infrastructure
- **Vendor independence** — open source, portable
- **Future-proof** — add new data types without system changes

### For Analysts

- **Consistent data** — same field means the same thing everywhere
- **Query flexibility** — JSON queries or SQL via reporting layer
- **Historical analysis** — query data at any point in time
- **Cross-system queries** — Registry enables federated queries

---

## Design Tradeoffs

Every architecture involves tradeoffs. WIP makes these deliberately:

| We Prioritize | Over | Because |
|---------------|------|---------|
| Flexibility | Raw performance | Most systems are I/O bound, not CPU bound |
| Validation | Ingestion speed | Catching errors early saves time later |
| Portability | Deep platform integration | Independence is more valuable long-term |
| Simplicity | Feature richness | Simple systems are maintainable systems |
| JSON | Binary formats | Human-readable is debuggable |

---

## What WIP is NOT

- **Not a relational database replacement** — use the reporting layer for complex SQL
- **Not a real-time system** — designed for consistency, not microsecond latency
- **Not a data lake** — structured, validated data only
- **Not a BI tool** — provides data for BI tools, isn't one itself

---

## Use Cases

WIP is well-suited for:

| Use Case | Why WIP Works |
|----------|---------------|
| **Research data management** | Flexible schemas, full provenance |
| **Configuration management** | Versioned, validated configs |
| **Master data management** | Single source of truth, cross-system identity |
| **Content management** | Template-driven content types |
| **IoT data collection** | Lightweight, runs at the edge |
| **Compliance records** | Audit trail, never-delete policy |
| **Multi-tenant SaaS** | Same engine, different templates per tenant |

---

## Summary

World In a Pie embodies a simple idea:

> **Define your vocabulary. Define your templates. Store your data. Query with confidence.**

The result is a system that adapts to your needs rather than forcing your data into predefined shapes — while still guaranteeing consistency and quality.
