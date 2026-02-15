# Component Specifications

This document provides detailed specifications for each component in the World In a Pie (WIP) system.

---

## Table of Contents

1. [Def-Store](#def-store)
2. [Template Store](#template-store)
3. [Document Store](#document-store)
4. [Registry](#registry)
5. [Reporting Sync](#reporting-sync)
6. [WIP Console](#wip-console)
7. [Infrastructure](#infrastructure)

---

## Def-Store

**Port:** 8002 | **API Base:** `/api/def-store`

### Purpose

The Def-Store is the **foundational layer** containing all terminologies and terms. It defines *what concepts exist* in the system.

### Data Structures

#### Terminology

A collection of related terms forming a controlled vocabulary.

```json
{
  "terminology_id": "019abc12-def3-7abc-8def-123456789abc",
  "value": "DOC_STATUS",
  "label": "Document Status",
  "description": "Controlled vocabulary for document lifecycle states",
  "status": "active",
  "namespace": "wip",
  "created_at": "2024-01-15T10:00:00Z",
  "created_by": "apikey:legacy",
  "updated_at": "2024-01-15T10:00:00Z",
  "updated_by": "apikey:legacy"
}
```

#### Term

An individual concept within a terminology. **Terms do not have versioning** — changes are tracked via audit log.

```json
{
  "term_id": "019abc13-def4-7abc-8def-123456789abc",
  "terminology_id": "019abc12-def3-7abc-8def-123456789abc",
  "value": "approved",
  "label": "Approved",
  "aliases": ["APPROVED", "Approved", "OK"],
  "description": "Document has been reviewed and approved",
  "status": "active",
  "parent_id": null,
  "metadata": {
    "workflow_order": "3"
  },
  "translations": {
    "de": "Genehmigt",
    "fr": "Approuvé"
  },
  "created_at": "2024-01-15T10:00:00Z",
  "created_by": "apikey:legacy"
}
```

**Key difference from other entities:** Terms represent stable concepts. Instead of versioning, all changes are recorded in an **audit log**.

### Term Aliases

Multiple input values can resolve to the same term:

```json
{
  "term_id": "019abc13-def4-7abc-8def-123456789abc",
  "value": "approved",
  "label": "Approved",
  "aliases": ["APPROVED", "Approved", "OK", "ok"]
}
```

When validating, all these inputs resolve to the same term:
- "approved" → matched via `value`
- "APPROVED" → matched via `alias`
- "OK" → matched via `alias`

### Hierarchical Terms

Terms can form hierarchies (e.g., location taxonomies, departments):

```
DEPARTMENT (terminology)
├── Engineering (parent: null)
│   ├── Frontend (parent: Engineering)
│   ├── Backend (parent: Engineering)
│   └── DevOps (parent: Engineering)
└── Sales (parent: null)
    ├── Enterprise (parent: Sales)
    └── SMB (parent: Sales)
```

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| **Terminologies** | | |
| GET | `/api/def-store/terminologies` | List all terminologies |
| GET | `/api/def-store/terminologies/{id}` | Get terminology details |
| POST | `/api/def-store/terminologies` | Create terminology |
| PUT | `/api/def-store/terminologies/{id}` | Update terminology |
| DELETE | `/api/def-store/terminologies/{id}` | Deactivate terminology |
| POST | `/api/def-store/terminologies/{id}/restore` | Restore inactive terminology |
| GET | `/api/def-store/terminologies/by-value/{value}` | Get terminology by value |
| POST | `/api/def-store/terminologies/bulk` | Bulk create terminologies |
| GET | `/api/def-store/terminologies/{id}/dependencies` | Get dependent templates |
| **Terms** | | |
| GET | `/api/def-store/terminologies/{term_id}/terms` | List terms in terminology |
| GET | `/api/def-store/terms/{id}` | Get single term |
| POST | `/api/def-store/terminologies/{term_id}/terms` | Create term |
| PUT | `/api/def-store/terms/{id}` | Update term |
| DELETE | `/api/def-store/terms/{id}` | Deactivate term |
| POST | `/api/def-store/terms/bulk` | Bulk create/update terms |
| GET | `/api/def-store/terms/{id}/audit` | Get term audit log |
| **Validation** | | |
| POST | `/api/def-store/validate` | Validate single value |
| POST | `/api/def-store/validate/bulk` | Bulk validate values |
| **Import/Export** | | |
| GET | `/api/def-store/terminologies/{id}/export` | Export to JSON/CSV |
| POST | `/api/def-store/terminologies/{id}/import` | Import from JSON/CSV |
| **Health** | | |
| GET | `/api/def-store/health/integrity` | Check referential integrity |

### Validation Response

```json
{
  "terminology_value": "DOC_STATUS",
  "input_value": "OK",
  "valid": true,
  "term_id": "019abc13-def4-7abc-8def-123456789abc",
  "matched_via": "alias",
  "normalized_value": "approved"
}
```

### Audit Log (Instead of Term Versioning)

All term changes are recorded:

```json
{
  "term_id": "019abc13-def4-7abc-8def-123456789abc",
  "terminology_id": "019abc12-def3-7abc-8def-123456789abc",
  "action": "updated",
  "changed_at": "2024-01-30T10:00:00Z",
  "changed_by": "user:admin-001",
  "changed_fields": ["aliases"],
  "previous_values": {"aliases": ["APPROVED"]},
  "new_values": {"aliases": ["APPROVED", "Approved", "OK", "ok"]}
}
```

---

## Template Store

**Port:** 8003 | **API Base:** `/api/template-store`

### Purpose

The Template Store defines **how concepts from the Def-Store combine** into reusable data structures with validation rules.

### Data Structures

#### Template

A schema definition for documents. Templates use **stable IDs** — the `template_id` persists across all versions. Multiple template versions can be active simultaneously, supporting both gradual migration and scenarios where different versions serve different purposes (e.g., ongoing vs new projects).

```json
{
  "template_id": "019abc14-def5-7abc-8def-123456789abc",
  "value": "PERSON",
  "label": "Person",
  "description": "Template for person records",
  "version": 3,
  "status": "active",
  "extends": null,
  "extends_version": null,
  "identity_fields": ["email"],
  "fields": [
    {
      "name": "first_name",
      "type": "string",
      "mandatory": true,
      "description": "Person's first name"
    },
    {
      "name": "status",
      "type": "term",
      "mandatory": false,
      "terminology_ref": "DOC_STATUS"
    },
    {
      "name": "address",
      "type": "object",
      "mandatory": false,
      "template_ref": "ADDRESS"
    }
  ],
  "rules": [
    {
      "type": "conditional_required",
      "condition": {"field": "country", "operator": "equals", "value": "DE"},
      "target_field": "tax_id"
    }
  ],
  "reporting": {
    "sync_enabled": true,
    "sync_strategy": "latest_only",
    "table_name": "doc_person"
  },
  "created_at": "2024-01-15T10:00:00Z",
  "created_by": "apikey:legacy"
}
```

#### Field Types

| Type | Description | Example | Additional Config |
|------|-------------|---------|-------------------|
| string | Free text | "John" | `min_length`, `max_length`, `pattern` |
| number | Numeric value | 42, 3.14 | `minimum`, `maximum` |
| integer | Whole number | 42 | `minimum`, `maximum` |
| boolean | True/false | true | — |
| date | ISO 8601 date | "2024-01-15" | — |
| datetime | ISO 8601 datetime | "2024-01-15T10:30:00Z" | — |
| term | Reference to terminology | "approved" | `terminology_ref` |
| object | Nested object | {...} | `template_ref` |
| array | List of items | [...] | `items` (field definition) |
| reference | Cross-document reference | "019abc..." | `reference_type`, `target_templates`, `include_subtypes` |
| file | Binary file attachment | "019abc..." | `file_config` (`allowed_types`, `max_size_mb`, `multiple`) |

#### Rule Types

| Rule Type | Description | Example |
|-----------|-------------|---------|
| `conditional_required` | Field required if condition met | Tax ID required if country=DE |
| `conditional_value` | Field value constrained by condition | Discount only if member=true |
| `mutual_exclusion` | Only one of listed fields can have value | Either phone OR mobile |
| `dependency` | Field requires another field to be present | Expiry requires issue date |

### Template Inheritance

Templates can extend other templates:

```json
{
  "template_id": "019abc15-def6-7abc-8def-123456789abc",
  "value": "EMPLOYEE",
  "label": "Employee",
  "extends": "PERSON",
  "extends_version": null,
  "fields": [
    {
      "name": "employee_id",
      "type": "string",
      "mandatory": true
    },
    {
      "name": "department",
      "type": "term",
      "terminology_ref": "DEPARTMENT"
    }
  ],
  "identity_fields": ["employee_id"]
}
```

Inheritance resolution:
1. Child inherits all parent fields
2. Child can override parent fields (same name)
3. Child adds its own fields
4. Child defines its own identity fields (replaces parent's)
5. Rules are merged (child rules evaluated after parent rules)

The `extends_version` field controls parent version resolution:
- `null` (default): always resolves against the **latest active** parent version
- A specific number (e.g., `2`): pins inheritance to that exact parent version

When fetching a resolved template, each field includes `inherited: true/false` and `inherited_from: "<template_id>"` to indicate whether it comes from a parent template or is defined directly on the child.

### Template Versioning

Templates use **stable IDs** — the `template_id` stays the same across all versions. The unique key is `(template_id, version)`.

| Operation | Result |
|-----------|--------|
| Create template (value=PERSON) | New `template_id`, version=1 |
| Update template | Same `template_id`, version=2, **version 1 still active** |
| Update template | Same `template_id`, version=3, **all versions still active** |

Multiple active versions support:
- **Gradual migration** — transition documents from v1 to v2 at your own pace
- **Parallel use cases** — e.g., ongoing projects use v1 while new projects use v2 with additional fields
- **Selective deactivation** — deactivate individual versions independently

### Draft Mode

Templates can be created with `status: "draft"` to skip all cross-reference validation. This enables:
- **Order-independent creation** — create templates in any order, activate when ready
- **Circular references** — both sides exist as drafts before either is activated
- **Cascading activation** — `POST /templates/{id}/activate` validates and activates the target plus all draft templates it references (all-or-nothing)
- **Dry-run preview** — `?dry_run=true` shows what would activate without making changes
- Draft templates cannot be used for document creation (Document-Store rejects non-active templates)

See `docs/design/template-draft-mode.md` for the full design.

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| **Templates** | | |
| GET | `/api/template-store/templates` | List all templates |
| GET | `/api/template-store/templates?latest_only=true` | List only latest versions |
| GET | `/api/template-store/templates/{id}` | Get template (resolved if extends) |
| GET | `/api/template-store/templates/{id}?version=N` | Get specific version |
| GET | `/api/template-store/templates/{id}?resolve=false` | Get template without inheritance |
| POST | `/api/template-store/templates` | Create template |
| PUT | `/api/template-store/templates/{id}` | Update template (creates new version) |
| DELETE | `/api/template-store/templates/{id}` | Deactivate template (latest version) |
| DELETE | `/api/template-store/templates/{id}?version=N` | Deactivate specific version |
| DELETE | `/api/template-store/templates/{id}?force=true` | Force deactivate even with dependent documents |
| POST | `/api/template-store/templates/bulk` | Bulk create templates |
| GET | `/api/template-store/templates/{id}/dependencies` | Get dependent documents |
| GET | `/api/template-store/templates/{id}/raw` | Get template without inheritance resolution |
| GET | `/api/template-store/templates/{id}/children` | Get direct child templates |
| GET | `/api/template-store/templates/{id}/descendants` | Get all descendant templates |
| POST | `/api/template-store/templates/{id}/cascade` | Cascade parent update to child templates |
| POST | `/api/template-store/templates/{id}/activate` | Activate a draft template (cascading) |
| **By Value** | | |
| GET | `/api/template-store/templates/by-value/{value}` | Get latest version by value |
| GET | `/api/template-store/templates/by-value/{value}/versions` | List all versions |
| GET | `/api/template-store/templates/by-value/{value}/versions/{v}` | Get specific version |
| **Validation** | | |
| POST | `/api/template-store/templates/{id}/validate` | Validate template references |
| **Health** | | |
| GET | `/api/template-store/health/integrity` | Check referential integrity |

### Reporting Configuration

Templates include configuration for PostgreSQL sync:

```json
{
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

| Setting | Options | Description |
|---------|---------|-------------|
| `sync_enabled` | true/false | Whether to sync to PostgreSQL |
| `sync_strategy` | `latest_only`, `all_versions`, `disabled` | Version handling |
| `table_name` | string | Custom table name (default: `doc_{value}` lowercase) |
| `flatten_arrays` | true/false | Flatten arrays into multiple rows |

---

## Document Store

**Port:** 8004 | **API Base:** `/api/document-store`

### Purpose

The Document Store holds **actual data** that conforms to templates. It is the primary data repository.

### Data Structures

#### Document

```json
{
  "document_id": "019abc16-def7-7abc-8def-123456789abc",
  "template_id": "019abc14-def5-7abc-8def-123456789abc",
  "template_value": "PERSON",
  "template_version": 3,
  "identity_hash": "a1b2c3d4e5f6...",
  "version": 2,
  "status": "active",
  "is_latest_version": true,
  "latest_version": 2,
  "data": {
    "first_name": "Alice",
    "last_name": "Smith",
    "status": "approved",
    "email": "alice@example.com"
  },
  "term_references": {
    "status": "019abc13-def4-7abc-8def-123456789abc"
  },
  "created_at": "2024-01-15T10:00:00Z",
  "created_by": "user:admin-001",
  "updated_at": "2024-02-20T14:30:00Z",
  "updated_by": "user:admin-001"
}
```

**Key fields:**
- `template_value` — Template value (e.g., "PERSON") for convenient filtering without needing the template_id
- `data` — Original submitted values
- `term_references` — Resolved term IDs for term fields (stores both original value AND term_id)
- `identity_hash` — SHA-256 of identity field values (see below for computation details)
- `is_latest_version` — Whether this is the current version
- `latest_version` — Latest version number (`document_id` is stable across versions)

### Identity and Versioning

Documents use **stable IDs** — the `document_id` persists across all versions. The unique key is `(document_id, version)`.

#### Identity Hash Computation

The identity hash determines whether a document submission creates a new document or a new version of an existing one. It is computed from the **identity fields** defined in the template:

```python
def compute_identity_hash(data: dict, identity_fields: list) -> str:
    sorted_fields = sorted(identity_fields)
    parts = []
    for field in sorted_fields:
        value = data.get(field, "")
        parts.append(f"{field}={normalize(value)}")
    normalized = "|".join(parts)
    return hashlib.sha256(normalized.encode()).hexdigest()
```

Values are normalized before hashing: strings are stripped and lowercased, so `"Alice@Example.com"` and `"alice@example.com"` produce the same hash.

**Namespace scoping:** The identity hash itself covers only the identity field values. However, the **Registry composite key** includes the namespace alongside the identity hash and template_id:

```json
{
  "namespace": "wip",
  "identity_hash": "a1b2c3...",
  "template_id": "019abc14-..."
}
```

This means the same identity field values in different namespaces produce different documents — identity uniqueness is scoped per namespace and per template.

#### Upsert Behavior (Single POST Endpoint)

```
POST /api/document-store/documents
        │
        ▼
Compute identity_hash from identity fields
        │
        ▼
Register with Registry (composite key: namespace + hash + template_id)
        │
        ├── New ID returned ──► CREATE new document (version 1)
        │
        └── Existing ID returned ──► CREATE new version, deactivate old
```

For templates **without identity fields**, the Registry receives an empty composite key and always generates a fresh document_id (each submission is a new document).

### Six-Stage Validation Pipeline

```
┌──────────────────────────────────────────────────────────────────┐
│                    VALIDATION PIPELINE                            │
├──────────────────────────────────────────────────────────────────┤
│                                                                   │
│  1. STRUCTURAL CHECK                                              │
│     • Is it valid JSON?                                           │
│     • Does it have required fields? (template_id, data)           │
│                                                                   │
│  2. TEMPLATE RESOLUTION                                           │
│     • Fetch template from Template Store                          │
│     • Resolve inheritance chain                                   │
│                                                                   │
│  3. FIELD VALIDATION                                              │
│     • Are mandatory fields present?                               │
│     • Are field types correct?                                    │
│     • Are nested objects valid against their templates?           │
│                                                                   │
│  4. TERM VALIDATION                                               │
│     • Bulk validate term values via Def-Store API                 │
│     • Collect resolved term_ids for term_references               │
│                                                                   │
│  5. RULE EVALUATION                                               │
│     • Evaluate conditional_required rules                         │
│     • Check cross-field constraints                               │
│     • Validate patterns and ranges                                │
│                                                                   │
│  6. IDENTITY COMPUTATION                                          │
│     • Are all identity fields present?                            │
│     • Compute identity hash                                       │
│     • Register with Registry to determine CREATE or UPDATE        │
│                                                                   │
└──────────────────────────────────────────────────────────────────┘
```

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| **Documents** | | |
| GET | `/api/document-store/documents` | List documents (filter by `template_id`, `template_value`, `status`) |
| GET | `/api/document-store/documents/{id}` | Get document (latest version) |
| GET | `/api/document-store/documents/{id}?version=N` | Get specific version |
| POST | `/api/document-store/documents` | Create/update document (upsert) |
| DELETE | `/api/document-store/documents/{id}` | Soft-delete (set status=inactive) |
| POST | `/api/document-store/documents/bulk` | Bulk create/update |
| **Versions** | | |
| GET | `/api/document-store/documents/{id}/versions` | Get all versions |
| GET | `/api/document-store/documents/{id}/versions/{v}` | Get specific version |
| GET | `/api/document-store/documents/{id}/latest` | Get latest version |
| POST | `/api/document-store/documents/{id}/restore/{v}` | Restore specific version |
| **Table View** | | |
| GET | `/api/document-store/table/{template_id}` | Flattened table view |
| GET | `/api/document-store/table/{template_id}/csv` | Export as CSV |
| **Query** | | |
| POST | `/api/document-store/documents/query` | Complex query |
| **Files** | | |
| POST | `/api/document-store/files` | Upload file (multipart/form-data) |
| GET | `/api/document-store/files/{id}` | Get file metadata |
| GET | `/api/document-store/files/{id}/download` | Get pre-signed download URL |
| GET | `/api/document-store/files/{id}/content` | Stream file content directly |
| DELETE | `/api/document-store/files/{id}` | Soft-delete file |
| GET | `/api/document-store/files/orphans/list` | List orphan files |
| GET | `/api/document-store/files/health/integrity` | File integrity check |
| **Health** | | |
| GET | `/api/document-store/health/integrity` | Check referential integrity |

### Table View Response

```json
{
  "template_id": "019abc14-def5-7abc-8def-123456789abc",
  "template_value": "PERSON",
  "columns": [
    {"name": "_document_id", "type": "string", "is_array": false},
    {"name": "first_name", "type": "string", "is_array": false},
    {"name": "languages", "type": "string", "is_array": true}
  ],
  "rows": [
    {"_document_id": "019abc...", "first_name": "John", "languages": "English"},
    {"_document_id": "019abc...", "first_name": "John", "languages": "Spanish"}
  ],
  "total_documents": 100,
  "total_rows": 150,
  "array_handling": "flattened"
}
```

---

## Registry

**Port:** 8001 | **API Base:** `/api/registry`

### Purpose

The Registry provides **federated identity management** and is the **ID generator for all WIP entities**.

### ID Generation

By default, the Registry generates **UUID7** identifiers (time-ordered UUIDs) for all entity types. This is the default for the `wip` namespace and any new namespace created without explicit configuration.

For namespaces that require human-readable IDs, the Registry supports **prefixed sequential IDs** (e.g., `TERM-000001`, `TPL-000002`) and other algorithms.

### ID Algorithms

| Algorithm | Format | Use Case |
|-----------|--------|----------|
| `uuid7` | `019abc12-def3-7abc-8def-123456789abc` | **Default.** Time-ordered, sortable by creation time |
| `uuid4` | `550e8400-e29b-41d4-a716-446655440000` | Universally unique, random |
| `prefixed` | `TERM-000001`, `TPL-000002` | Human-readable with prefix and sequential counter |
| `nanoid` | `V1StGXR8_Z5jdHi6B-myT` | URL-safe compact IDs |
| `pattern` | (custom regex) | Validated against a regex pattern |
| `any` | (any string) | No format enforcement |

### Namespace Configuration

Each namespace has its own ID algorithm configuration **per entity type**. When creating a namespace, you can specify which algorithm to use for terminologies, terms, templates, documents, and files.

**Default WIP namespace** (created by `initialize-wip`):
- All entity types use UUID7 (the global default)

**Example: Custom namespace with prefixed IDs:**

```bash
curl -X POST http://localhost:8001/api/registry/namespaces \
  -H "X-API-Key: dev_master_key_for_testing" \
  -H "Content-Type: application/json" \
  -d '{
    "prefix": "my-project",
    "description": "Project with human-readable IDs",
    "id_config": {
      "terminologies": {"algorithm": "prefixed", "prefix": "TERM-", "pad": 6},
      "terms": {"algorithm": "prefixed", "prefix": "T-", "pad": 6},
      "templates": {"algorithm": "prefixed", "prefix": "TPL-", "pad": 6},
      "documents": {"algorithm": "uuid7"},
      "files": {"algorithm": "uuid7"}
    }
  }'
```

Entity types not specified in `id_config` default to UUID7. You can query a namespace's ID configuration:

```bash
GET /api/registry/namespaces/{prefix}/id-config
```

### Composite Keys and Stable IDs

The Registry uses **composite keys** for idempotent ID generation. When a service registers an entity with a composite key, the Registry either returns the existing ID (if the key matches) or generates a new one.

- **Templates and files**: registered with an empty composite key `{}` — always get a new ID. Updates reuse the existing `template_id`.
- **Documents with identity fields**: composite key includes `{namespace, identity_hash, template_id}` — same identity returns the same `document_id`.
- **Documents without identity fields**: empty composite key — always get a new `document_id`.

### Synonyms

Multiple identifiers can resolve to the same entity:

```
Registry ID: 019abc14-... (preferred)
    │
    ├── Synonym: legacy_system:OLD-TPL-42
    └── Synonym: external_api:template_abc
```

Synonyms are indexed via a flat `search_values` array on each registry entry, making synonym lookups as fast as canonical ID lookups.

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| **Namespaces** | | |
| GET | `/api/registry/namespaces` | List all namespaces |
| GET | `/api/registry/namespaces/{prefix}` | Get namespace details |
| GET | `/api/registry/namespaces/{prefix}/stats` | Get entity counts |
| GET | `/api/registry/namespaces/{prefix}/id-config` | Get ID algorithm config |
| POST | `/api/registry/namespaces` | Create namespace (with optional `id_config`) |
| PUT | `/api/registry/namespaces/{prefix}` | Update namespace |
| POST | `/api/registry/namespaces/{prefix}/archive` | Archive namespace |
| POST | `/api/registry/namespaces/{prefix}/restore` | Restore archived namespace |
| DELETE | `/api/registry/namespaces/{prefix}` | Delete namespace (must be archived first) |
| POST | `/api/registry/namespaces/initialize-wip` | Initialize default WIP namespace |
| **Entries** | | |
| POST | `/api/registry/entries/register` | Register composite keys (bulk) |
| POST | `/api/registry/entries/lookup/by-id` | Lookup by ID with 3-step cascade |
| POST | `/api/registry/entries/lookup/by-key` | Lookup by composite key (bulk) |
| **Synonyms** | | |
| POST | `/api/registry/synonyms/add` | Add synonyms (bulk) |
| POST | `/api/registry/synonyms/remove` | Remove synonyms (bulk) |
| POST | `/api/registry/synonyms/merge` | Merge entries (bulk) |
| **Search** | | |
| POST | `/api/registry/search/by-fields` | Search by field criteria |
| POST | `/api/registry/search/by-term` | Free-text search |
| **Export/Import** | | |
| POST | `/api/registry/namespaces/{prefix}/export` | Export namespace |
| POST | `/api/registry/namespaces/import` | Import namespace |

### Extended Identifier Lookup

`POST /api/registry/entries/lookup/by-id` performs a **3-step resolution cascade**:

1. **Step 1 — `entry_id`:** Direct match on the canonical entry ID.
2. **Step 2 — `additional_ids`:** Match against merged IDs accumulated from entry merge operations.
3. **Step 3 — `search_values`:** Match against the flat `search_values` array, which contains synonym values, external IDs, and business keys from composite key fields.

The `pool_id` parameter is **optional**. Omit it to search across all pools/namespaces; provide it to constrain the search to a single pool.

The response includes a `matched_via` field indicating how the match was found (e.g., `entry_id`, `additional_ids`, or `search_values`). This enables the Document Store to resolve any identifier — canonical ID, synonym, or external ID — in a single call.

---

## Reporting Sync

**Port:** 8005 | **API Base:** `/api/reporting-sync`

### Purpose

Synchronize document data from MongoDB to PostgreSQL for SQL-based analytics and BI tools.

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                   REPORTING SYNC ARCHITECTURE                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   Document Store                    NATS                         │
│       :8004                       :4222                          │
│         │                           │                            │
│         ├──► MongoDB (save)         │                            │
│         │                           │                            │
│         └──► NATS publish ──────────┤                            │
│              wip.documents.created  │                            │
│                                     ▼                            │
│                              Reporting Sync                      │
│                                  :8005                           │
│                                     │                            │
│                              ┌──────┴──────┐                     │
│                              │ Transform   │                     │
│                              │ • Flatten   │                     │
│                              │ • Type map  │                     │
│                              └──────┬──────┘                     │
│                                     │                            │
│                                     ▼                            │
│                              PostgreSQL                          │
│                                :5432                             │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Table Generation

For each template with `sync_enabled: true`, a corresponding PostgreSQL table is generated. The table name defaults to `doc_{template_value}` (lowercase), e.g., template value `PERSON` → table `doc_person`.

For `latest_only` strategy, `document_id` is the primary key (upserts replace old version data). For `all_versions` strategy, the primary key is `(document_id, version)`.

```sql
-- latest_only strategy
CREATE TABLE doc_person (
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
    status TEXT,
    status_term_id TEXT,

    -- Nested objects flattened
    address_street TEXT,
    address_city TEXT,

    -- Original JSON
    data_json JSONB,
    term_references_json JSONB
);

-- all_versions strategy
CREATE TABLE doc_audit_log (
    document_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    -- ... same columns ...
    PRIMARY KEY (document_id, version)
);
```

### NATS Event Format

Events contain the full document (self-contained):

```json
{
  "event_id": "evt-123",
  "event_type": "document.created",
  "timestamp": "2024-01-30T10:00:00Z",
  "document": {
    "document_id": "019abc16-...",
    "template_id": "019abc14-...",
    "template_value": "PERSON",
    "version": 1,
    "status": "active",
    "data": { "..." },
    "term_references": { "..." }
  }
}
```

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| **Health** | | |
| GET | `/health` | Health check (NATS/PostgreSQL status) |
| GET | `/status` | Sync worker status |
| **Metrics** | | |
| GET | `/metrics` | Latency, throughput, per-template stats |
| GET | `/metrics/consumer` | NATS queue depth, pending messages |
| **Alerts** | | |
| GET | `/alerts` | Active alerts and configuration |
| PUT | `/alerts/config` | Update thresholds and webhook |
| POST | `/alerts/test` | Trigger manual alert check |
| **Schema** | | |
| GET | `/schema/{template_value}` | View generated schema |
| **Batch Sync** | | |
| POST | `/sync/batch/{template_value}` | Sync all docs for template |
| POST | `/sync/batch` | Sync all templates |
| GET | `/sync/batch/jobs` | List batch jobs |
| GET | `/sync/batch/jobs/{id}` | Get job status |
| DELETE | `/sync/batch/jobs/{id}` | Cancel job |
| **Integrity** | | |
| GET | `/health/integrity` | Aggregated integrity check |

### Alert Types

| Alert | Trigger |
|-------|---------|
| `queue_lag` | NATS pending messages exceed threshold |
| `error_rate` | Sync errors exceed threshold |
| `processing_stalled` | No events processed for threshold time |
| `connection_lost` | NATS or PostgreSQL connection lost |

---

## WIP Console

**Port:** 3000 (dev) / 80 (production) | **Access:** `https://localhost:8443`

### Purpose

Unified web UI for managing all WIP components.

### Technology Stack

- **Framework:** Vue 3 + TypeScript
- **UI Library:** PrimeVue
- **Build:** Vite
- **OIDC:** oidc-client-ts

### Features

#### Terminology Management
- List, create, edit, delete terminologies
- Manage terms with bulk import (JSON/CSV)
- Export terminologies to JSON/CSV
- Value validation (single and bulk)
- View which templates use each terminology

#### Template Management
- List, create, edit, delete templates
- Visual field editor
- Validation rules configuration
- Template inheritance visualization
- Reporting sync configuration
- Version history with version-specific navigation

#### Document Management
- List documents by template with soft title display (shows "title" field value when available)
- Dynamic form generation based on template fields
- Real-time validation feedback
- Version history viewing and restore
- Table view with CSV export

### Authentication

- **OIDC Login:** Via Dex (admin@wip.local, editor@wip.local, viewer@wip.local)
- **API Key:** For development/testing
- Configurable via settings panel

---

## Infrastructure

### MongoDB

**Port:** 27017

Primary document store. Databases:
- `wip_registry`
- `wip_def_store`
- `wip_template_store`
- `wip_document_store`

### PostgreSQL

**Port:** 5432

Reporting database. Tables auto-generated from templates:
- `doc_{template_value}` (lowercase) for each template with sync enabled
- `_wip_schema_migrations` for tracking schema changes

### NATS

**Port:** 4222 | **Monitoring:** 8222

Message queue with JetStream for event persistence.

**Subjects:**
| Subject | Publisher | Consumer |
|---------|-----------|----------|
| `wip.documents.created` | Document Store | Reporting Sync |
| `wip.documents.updated` | Document Store | Reporting Sync |
| `wip.documents.deleted` | Document Store | Reporting Sync |
| `wip.templates.created` | Template Store | Reporting Sync |
| `wip.templates.updated` | Template Store | Reporting Sync |

### MinIO

**Port:** 9000 (API) / 9001 (Console)

S3-compatible object storage for binary files. Used by Document Store for file uploads.
- Files stored with Registry IDs (UUID7 by default)
- Reference tracking (documents → files)
- Orphan detection for unlinked files
- SHA-256 checksums for duplicate detection

### Dex

**Port:** 5556

Lightweight OIDC provider (~30MB RAM). Features:
- Static users via YAML config
- Works over HTTP (no TLS required)
- Standard OIDC protocol

### Caddy

**Ports:** 8080 (HTTP) / 8443 (HTTPS)

Reverse proxy with auto-generated TLS certificates. Routes:
- `/` → WIP Console
- `/api/*` → Microservices
- `/dex/` → Dex OIDC

### Mongo Express (Optional)

**Port:** 8081

MongoDB admin UI. Included in `mac` and `pi-large` profiles.

---

## Authentication

All services use the **wip-auth** shared library.

### Auth Modes

| Mode | Description |
|------|-------------|
| `none` | No authentication (development) |
| `api_key_only` | API key via `X-API-Key` header |
| `jwt_only` | JWT from OIDC provider |
| `dual` | Both API key and JWT (default) |

### Dependencies

```python
from wip_auth import require_identity, require_groups, require_admin

@app.get("/protected")
async def protected(identity = Depends(require_identity)):
    return {"user": identity.username}

@app.post("/admin-only")
async def admin_only(identity = Depends(require_admin)):
    return {"admin": identity.username}
```

### Identity Tracking

All operations record the authenticated identity:
- `created_by`: `apikey:legacy` or `user:admin-001`
- `updated_by`: Same format

See [authentication.md](authentication.md) for full configuration details.
