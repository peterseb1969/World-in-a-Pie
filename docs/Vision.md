# World In a Pie: Vision

## The Problem

Modern applications often face a fundamental tension:

1. **Rigid schemas** provide consistency but resist change
2. **Schemaless storage** provides flexibility but sacrifices consistency
3. **Domain-specific systems** solve one problem well but don't generalize
4. **Enterprise platforms** generalize but are heavy and expensive

Organizations end up with multiple disconnected systems, inconsistent data quality, difficulty querying across silos, and vendor lock-in.

**WIP resolves this** by separating *structure definition* from *data storage*: store anything, as long as it conforms to a template that references a shared vocabulary.

## Philosophy

World In a Pie (WIP) is a **generic, domain-agnostic storage and reporting engine**. It is not an application—it is a foundation upon which applications are built.

### WIP Supports Nothing Out of the Box

This is intentional. WIP provides **primitives**, not solutions:

| Primitive | Purpose |
|-----------|---------|
| **Terminologies** | Controlled vocabularies (codes, values, aliases) |
| **Templates** | Document schemas with validation rules |
| **Documents** | Validated, versioned data storage |
| **Reporting** | SQL-accessible data via PostgreSQL sync |

These primitives are domain-agnostic. WIP doesn't know what a "patient", "invoice", or "sensor reading" is. You define that through terminologies and templates.

### The Console is Admin UI Only

The WIP Console (`/ui/wip-console`) is for **system administrators**:
- Define and manage terminologies
- Create and version templates
- Browse and validate documents
- Monitor reporting sync

It is **not** an end-user application. End users interact with custom applications built on top of WIP.

### Demo Apps are Built ON TOP of WIP

To demonstrate WIP's capabilities, demo applications must be built as **separate projects** that consume WIP's APIs. They should not modify WIP's core code.

```
┌─────────────────────────────────────────────┐
│           Custom Applications               │
│  (Patient Portal, Invoice System, IoT Hub)  │
├─────────────────────────────────────────────┤
│              WIP APIs                       │
│  Terminologies │ Templates │ Documents │ SQL│
├─────────────────────────────────────────────┤
│           WIP Infrastructure                │
│  MongoDB │ PostgreSQL │ NATS │ Dex/OIDC     │
└─────────────────────────────────────────────┘
```

This separation ensures:
- WIP remains generic and reusable
- Each demo showcases real-world integration patterns
- The same WIP instance can power multiple unrelated applications

---

## Target Audience

WIP is designed for a diverse audience:

| Audience | Use Case |
|----------|----------|
| **Hobbyists** | Personal data management, home automation logs |
| **Small businesses** | Custom forms, inventory tracking, simple workflows |
| **Developers** | Rapid prototyping with validated data storage |
| **Enterprises** | Standardized data layer with SQL reporting |
| **AI/Automation** | Backend for AI-generated solutions |

The common requirement: **willingness to learn**. WIP rewards understanding of its primitives with flexibility and power.

---

## Integration Patterns

### Custom User Interfaces

Applications connect to WIP via its REST APIs:

**Single Page Applications (SPAs):**
```
Browser → Custom SPA → WIP APIs
                   ↘ Same OIDC provider (Dex/Authentik)
```

**Server-Side Applications:**
```
Browser → App Server → WIP APIs (with API key)
              ↓
        Business Logic Layer
```

### Authentication

- **User authentication**: OIDC via Dex (development) or Authentik (enterprise)
- **Service authentication**: API keys for system-to-system calls
- **Custom apps**: Share the same OIDC provider for SSO experience

### Event-Driven Integration

NATS powers internal event-driven sync (MongoDB → PostgreSQL). Future enhancements:

| Direction | Status | Use Case |
|-----------|--------|----------|
| WIP → External | Available via NATS subscription | React to document changes |
| External → WIP | Planned (streaming ingest) | High-volume data ingestion |

### Business Logic

**All business logic lives in the application layer**, not in WIP.

WIP provides:
- ✅ Data validation (type checking, term validation, cross-field rules)
- ✅ Identity-based versioning
- ✅ Audit trail (what changed, when, by whom)

WIP does **not** provide:
- ❌ Business workflows
- ❌ Notifications
- ❌ Custom computed fields
- ❌ Domain-specific logic

These belong in your application.

---

## The Registry: Federated Identity Foundation

At the heart of WIP lies the **Registry**—a service that may seem simple but enables powerful capabilities: **universal identity, cross-system integration, and federation**.

### Centralized ID Generation

Every entity in WIP receives its ID from the Registry:

| Namespace | ID Format | Used By |
|-----------|-----------|---------|
| `wip-terminologies` | `TERM-000001` | Terminologies |
| `wip-terms` | `T-000001` | Terms |
| `wip-templates` | `TPL-000001` | Templates |
| `wip-documents` | UUID7 (time-ordered) | Documents |

This centralization provides:
- **Guaranteed uniqueness** across all services
- **Predictable formats** (prefixed IDs are human-readable)
- **Time-ordering** for documents (UUID7 enables chronological sorting)
- **Single source of truth** for identity

### The Synonym Philosophy

Here's where the Registry becomes powerful: **multiple identifiers can resolve to the same entity**.

```
Registry ID: TPL-000001 (preferred/canonical)
    │
    ├── Synonym: legacy_system:OLD-TPL-42
    ├── Synonym: external_api:template_abc
    └── Synonym: partner_org:contract-template-v3
```

**Why this matters:**

1. **Legacy Integration**: When migrating from an old system, the old IDs continue to work. Both `legacy_system:OLD-TPL-42` and `TPL-000001` resolve to the same template.

2. **External References**: Partner systems can reference entities using their own naming conventions. The Registry maps these to canonical WIP IDs.

3. **Code Changes Without Migration**: If a term code changes from `M` to `MALE`, add the old code as a synonym. Existing documents remain valid.

4. **Cross-System Identity**: The same real-world entity (a customer, a product) can be referenced by different IDs in different systems—all mapped to one canonical ID.

**Example: Term Aliases as Synonyms**

The Def-Store uses this pattern for term aliases:

```json
{
  "term_id": "T-000001",
  "code": "M",
  "value": "Male",
  "aliases": ["MR", "Mr", "Mr.", "MALE", "mr"]
}
```

All these inputs resolve to `T-000001`:
- "Male" → matched via `value`
- "M" → matched via `code`
- "Mr." → matched via `alias`

The validation response tells you **how** it matched:

```json
{
  "input_value": "Mr.",
  "valid": true,
  "term_id": "T-000001",
  "matched_via": "alias",
  "normalized_value": "Male"
}
```

### Federation Potential

The Registry is designed for a future where **multiple WIP instances share identity coordination**:

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

**Current state**: Single Registry per WIP instance.

**Future vision**: A single Registry coordinating multiple autonomous WIP instances:

| Operation | How It Works |
|-----------|--------------|
| **Lookup** | "Who has entity X?" → "Instance #2 in London" |
| **Proxy** | "Get entity X" → forwards request to Instance #2 → returns result |
| **Sync** | Terminologies and templates can be shared across instances |
| **Local autonomy** | Each instance operates independently; Registry provides coordination |

This enables:
- **Distributed deployments** across geographic regions
- **Data sovereignty** (data stays where it was created)
- **Cross-instance queries** without data replication
- **Organizational separation** with shared vocabularies

**Not yet implemented**, but the Registry's architecture is designed for it. The synonym mechanism and namespace isolation are building blocks for federation.

### Composite Key Registration

Every entity is registered with a **composite key**—a set of fields that uniquely identify it:

```python
# Terminology composite key
{
    "pool_id": "wip-terminologies",
    "composite_key": {
        "code": "GENDER",
        "name": "Gender"
    }
}
# → Generates: TERM-000001

# Term composite key
{
    "pool_id": "wip-terms",
    "composite_key": {
        "terminology_id": "TERM-000001",
        "code": "M",
        "value": "Male"
    }
}
# → Generates: T-000001
```

The composite key is hashed (SHA-256) and stored. If the same composite key is registered again, the **existing ID is returned** (idempotent). This prevents duplicates and enables upsert behavior.

---

## Multi-Tenancy

WIP supports multi-tenancy for scenarios where multiple independent use cases share infrastructure:

### For Hobbyists and Small Deployments

A single WIP instance can serve multiple unrelated purposes:

```
WIP Instance (Raspberry Pi)
├── Namespace: home-automation
│   ├── Terminologies: SENSOR_TYPE, ROOM, ...
│   ├── Templates: SENSOR_READING, DEVICE_STATUS
│   └── Documents: sensor readings, device logs
│
├── Namespace: personal-finance
│   ├── Terminologies: CATEGORY, ACCOUNT_TYPE, ...
│   ├── Templates: TRANSACTION, BUDGET
│   └── Documents: transactions, budgets
│
└── Namespace: recipe-collection
    ├── Terminologies: CUISINE, DIFFICULTY, ...
    ├── Templates: RECIPE, INGREDIENT
    └── Documents: recipes
```

### Isolation Mechanisms

| Mechanism | Level | Description |
|-----------|-------|-------------|
| **Namespaces** | ID generation | Separate ID sequences per namespace |
| **Template codes** | Schema | Use prefixes (e.g., `FINANCE_TRANSACTION`) |
| **Groups** | Access control | OIDC groups for authorization |
| **PostgreSQL schemas** | Reporting | Separate schemas per tenant (future) |

---

## Design Principles

### 1. Never Delete, Only Deactivate

Data is never physically deleted. Documents, templates, and terms are soft-deleted (`status: inactive`). This ensures:
- Historical references always resolve
- Audit trails remain complete
- Recovery is always possible

### 2. References Must Resolve

Every reference (term_id, template_id, document_id) must point to an existing entity. The system validates references and warns about inactive targets.

### 3. Preserve Original Values

Documents store both the original submitted value AND the resolved reference. This supports:
- Audit compliance (what was actually submitted)
- Data recovery (original values available)
- ETL flexibility (transform in reporting layer, not source)

### 4. Configuration Over Code

Backend selection, auth providers, and sync behavior are configured via environment variables and config files—not code changes.

### 5. Pluggable Architecture

Every major component has an abstraction layer:
- Storage: MongoDB now, SQLite later
- Auth: Dex now, Authentik/other OIDC providers interchangeable
- Reporting: PostgreSQL now, other SQL databases possible

---

## Benefits

### For Developers

- **No schema migrations** — templates define structure, not database schemas
- **Self-documenting** — templates and definitions serve as documentation
- **API consistency** — same REST API regardless of data type

### For Data Managers

- **Single source of truth** — all definitions in one place
- **Controlled vocabulary** — terminology enforced across all data
- **Data quality** — validation prevents bad data entry
- **Audit trail** — full history of all changes

### For Organizations

- **Reduced silos** — one system for many data types
- **Lower costs** — runs on minimal infrastructure
- **Vendor independence** — open source, portable

### For Analysts

- **Consistent data** — same field means the same thing everywhere
- **Query flexibility** — JSON queries or SQL via reporting layer
- **Historical analysis** — query data at any point in time

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

## What WIP Is Not

| WIP is NOT... | Instead... |
|---------------|------------|
| A CRM | Build a CRM on WIP |
| A CMS | Build a CMS on WIP |
| An ERP | Build ERP modules on WIP |
| A workflow engine | Integrate with workflow tools that consume WIP APIs |
| A low-code platform | It's a backend; build your own UI or use AI-generated code |

---

## Summary

World In a Pie is a **meta-system**: a validated, versioned, SQL-queryable data layer that knows nothing about your domain until you teach it through terminologies and templates.

Its power comes from what it **doesn't** do—by staying generic, it becomes a foundation for anything.
