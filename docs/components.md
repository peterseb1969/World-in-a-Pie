# Component Specifications

This document provides detailed specifications for each component in the World In a Pie (WIP) system.

---

## Table of Contents

1. [Def-Store](#def-store)
2. [Template Store](#template-store)
3. [Document Store](#document-store)
4. [Validation Engine](#validation-engine)
5. [Registry](#registry)
6. [Web UIs](#web-uis)
7. [Reporting Layer](#reporting-layer)
8. [Message Queue](#message-queue)
9. [Auth Service](#auth-service)

---

## Def-Store

### Purpose

The Def-Store is the **foundational layer** containing all ontologies and terminologies. It defines *what concepts exist* in the system.

### Data Structures

#### Terminology

A collection of related terms forming a controlled vocabulary.

```json
{
  "id": "term-gender",
  "name": "Gender",
  "description": "Controlled vocabulary for gender identification",
  "version": 1,
  "status": "active",
  "created_at": "2024-01-15T10:00:00Z",
  "created_by": "admin",
  "terms": [
    {"ref": "term-gender-male"},
    {"ref": "term-gender-female"},
    {"ref": "term-gender-other"},
    {"ref": "term-gender-undisclosed"}
  ]
}
```

#### Term

An individual concept within a terminology.

```json
{
  "id": "term-gender-male",
  "terminology_id": "term-gender",
  "code": "M",
  "label": "Male",
  "description": "Male gender identity",
  "version": 1,
  "status": "active",
  "parent_id": null,
  "metadata": {
    "iso_5218": "1",
    "hl7_v3": "M"
  }
}
```

### Hierarchical Terms

Terms can form hierarchies (e.g., location taxonomies):

```
term-location-world
├── term-location-europe
│   ├── term-location-germany
│   │   ├── term-location-berlin
│   │   └── term-location-munich
│   └── term-location-france
└── term-location-asia
    └── term-location-japan
```

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/terminologies` | List all terminologies |
| GET | `/api/terminologies/{id}` | Get terminology with terms |
| POST | `/api/terminologies` | Create terminology |
| PUT | `/api/terminologies/{id}` | Update terminology (new version) |
| DELETE | `/api/terminologies/{id}` | Deactivate terminology |
| GET | `/api/terms/{id}` | Get single term |
| POST | `/api/terms` | Create term |
| PUT | `/api/terms/{id}` | Update term (new version) |
| DELETE | `/api/terms/{id}` | Deactivate term |

### Access Control

| Role | Permissions |
|------|-------------|
| admin | Full CRUD |
| architect | Read only |
| editor | Read only |
| viewer | Read only |

### Bootstrapping

The Def-Store requires manual bootstrapping since it cannot validate against non-existent templates. A bootstrap script seeds the foundational terminologies:

```bash
python -m wip.bootstrap --seed-definitions
```

---

## Template Store

### Purpose

The Template Store defines **how concepts from the Def-Store combine** into reusable data structures with validation rules.

### Data Structures

#### Template

A schema definition for documents.

```json
{
  "id": "template-person",
  "name": "Person",
  "description": "Template for person records",
  "version": 3,
  "status": "active",
  "extends": null,
  "created_at": "2024-01-15T10:00:00Z",
  "created_by": "architect",
  "identity_fields": ["national_id"],
  "fields": [
    {
      "name": "first_name",
      "label": "First Name",
      "type": "string",
      "mandatory": true,
      "terminology_ref": null
    },
    {
      "name": "last_name",
      "label": "Last Name",
      "type": "string",
      "mandatory": true,
      "terminology_ref": null
    },
    {
      "name": "gender",
      "label": "Gender",
      "type": "term",
      "mandatory": false,
      "terminology_ref": "term-gender"
    },
    {
      "name": "birth_date",
      "label": "Date of Birth",
      "type": "date",
      "mandatory": true,
      "terminology_ref": null
    },
    {
      "name": "national_id",
      "label": "National ID",
      "type": "string",
      "mandatory": true,
      "terminology_ref": null
    },
    {
      "name": "address",
      "label": "Address",
      "type": "object",
      "mandatory": false,
      "template_ref": "template-address"
    }
  ],
  "rules": [
    {
      "type": "conditional_required",
      "if": {"field": "country", "equals": "DE"},
      "then": {"field": "tax_id", "required": true}
    }
  ]
}
```

#### Field Types

| Type | Description | Example |
|------|-------------|---------|
| `string` | Free text | "John" |
| `number` | Numeric value | 42, 3.14 |
| `integer` | Whole number | 42 |
| `boolean` | True/false | true |
| `date` | ISO 8601 date | "2024-01-15" |
| `datetime` | ISO 8601 datetime | "2024-01-15T10:30:00Z" |
| `term` | Reference to terminology term | "term-gender-male" |
| `object` | Nested object (ref to template) | {...} |
| `array` | List of items | [...] |

#### Rule Types

| Rule Type | Description |
|-----------|-------------|
| `conditional_required` | Field required if condition met |
| `conditional_value` | Field value constrained by condition |
| `mutual_exclusion` | Only one of listed fields can have value |
| `dependency` | Field requires another field to be present |
| `pattern` | Field must match regex pattern |
| `range` | Numeric field must be within range |

### Template Inheritance

Templates can extend other templates:

```json
{
  "id": "template-employee",
  "name": "Employee",
  "extends": "template-person",
  "fields": [
    {
      "name": "employee_id",
      "label": "Employee ID",
      "type": "string",
      "mandatory": true
    },
    {
      "name": "department",
      "label": "Department",
      "type": "term",
      "terminology_ref": "term-departments"
    }
  ],
  "identity_fields": ["employee_id"]
}
```

Inheritance resolution:
1. Child inherits all parent fields
2. Child can override parent fields
3. Child adds its own fields
4. Child defines its own identity fields (replaces parent's)
5. Rules are merged (child rules evaluated after parent rules)

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/templates` | List all templates |
| GET | `/api/templates/{id}` | Get template (resolved if extends) |
| GET | `/api/templates/{id}/raw` | Get template without inheritance resolution |
| POST | `/api/templates` | Create template |
| PUT | `/api/templates/{id}` | Update template (new version) |
| DELETE | `/api/templates/{id}` | Deactivate template |
| GET | `/api/templates/{id}/validate` | Validate template references |

### Access Control

| Role | Permissions |
|------|-------------|
| admin | Full CRUD |
| architect | Full CRUD |
| editor | Read only |
| viewer | Read only |

---

## Document Store

### Purpose

The Document Store holds **actual data** that conforms to templates. It is the primary data repository.

### Data Structures

#### Document

```json
{
  "id": "doc-550e8400-e29b-41d4-a716-446655440000",
  "template_id": "template-person",
  "template_version": 3,
  "identity_hash": "a1b2c3d4e5f6...",
  "version": 2,
  "status": "active",
  "created_at": "2024-01-15T10:00:00Z",
  "created_by": "user-123",
  "updated_at": "2024-02-20T14:30:00Z",
  "updated_by": "user-456",
  "data": {
    "first_name": "Alice",
    "last_name": "Smith",
    "gender": "term-gender-female",
    "birth_date": "1990-05-15",
    "national_id": "DE123456789",
    "address": {
      "street": "Hauptstraße 1",
      "city": "Berlin",
      "postal_code": "10115",
      "country": "DE"
    }
  }
}
```

### Identity and Versioning

#### Identity Hash Computation

```python
def compute_identity_hash(document: dict, template: dict) -> str:
    """
    Compute deterministic identity hash from identity fields.
    """
    identity_fields = template["identity_fields"]

    # Sort fields alphanumerically
    sorted_fields = sorted(identity_fields)

    # Build normalized string: field1=value1|field2=value2|...
    parts = []
    for field in sorted_fields:
        value = document["data"].get(field, "")
        parts.append(f"{field}={value}")

    normalized = "|".join(parts)

    # Hash with SHA-256
    return hashlib.sha256(normalized.encode()).hexdigest()
```

#### Upsert Behavior

```
New document submitted
        │
        ▼
Compute identity hash
        │
        ▼
Search for existing active document with same hash
        │
        ├─── Not found ──► CREATE new document (version 1)
        │
        └─── Found ──► UPDATE (deactivate old, create new version)
```

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/documents` | List documents (with filtering) |
| GET | `/api/documents/{id}` | Get document |
| GET | `/api/documents/{id}/versions` | Get all versions |
| GET | `/api/documents/{id}/versions/{v}` | Get specific version |
| POST | `/api/documents` | Create/update document |
| DELETE | `/api/documents/{id}` | Deactivate document |
| POST | `/api/documents/query` | Complex query |
| POST | `/api/documents/bulk` | Bulk create/update |

### Query Capabilities

```json
{
  "template_id": "template-person",
  "filter": {
    "data.city": "Berlin",
    "data.birth_date": {"$gte": "1990-01-01"}
  },
  "sort": [{"field": "data.last_name", "order": "asc"}],
  "pagination": {
    "offset": 0,
    "limit": 50
  },
  "include_inactive": false
}
```

### Access Control

| Role | Permissions |
|------|-------------|
| admin | Full CRUD |
| architect | Read only |
| editor | Full CRUD |
| viewer | Read only |
| system | Full CRUD (API key auth) |

---

## Validation Engine

### Purpose

The Validation Engine ensures all documents conform to their declared templates before storage.

### Validation Pipeline

```
┌─────────────────────────────────────────────────────────────────┐
│                     VALIDATION PIPELINE                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. STRUCTURAL VALIDATION                                        │
│     • Is it valid JSON?                                          │
│     • Does it have required envelope fields?                     │
│       (template_id, data)                                        │
│                                                                  │
│  2. TEMPLATE RESOLUTION                                          │
│     • Does the template exist?                                   │
│     • Is the template active?                                    │
│     • Resolve inheritance if applicable                          │
│                                                                  │
│  3. FIELD VALIDATION                                             │
│     • Are all mandatory fields present?                          │
│     • Are field types correct?                                   │
│     • Do term references point to valid terms?                   │
│     • Are nested objects valid against their templates?          │
│                                                                  │
│  4. RULE EVALUATION                                              │
│     • Evaluate conditional rules                                 │
│     • Check cross-field constraints                              │
│     • Validate patterns and ranges                               │
│                                                                  │
│  5. IDENTITY COMPUTATION                                         │
│     • Are all identity fields present?                           │
│     • Compute identity hash                                      │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Validation Response

#### Success

```json
{
  "valid": true,
  "identity_hash": "a1b2c3d4e5f6...",
  "is_update": false,
  "warnings": []
}
```

#### Failure

```json
{
  "valid": false,
  "errors": [
    {
      "field": "data.national_id",
      "code": "REQUIRED_FIELD_MISSING",
      "message": "Field 'national_id' is required"
    },
    {
      "field": "data.gender",
      "code": "INVALID_TERM_REFERENCE",
      "message": "Term 'term-gender-unknown' does not exist in terminology 'term-gender'"
    }
  ],
  "warnings": [
    {
      "field": "data.phone",
      "code": "DEPRECATED_FIELD",
      "message": "Field 'phone' is deprecated, use 'contact_phone' instead"
    }
  ]
}
```

### Error Codes

| Code | Description |
|------|-------------|
| `INVALID_JSON` | Document is not valid JSON |
| `MISSING_TEMPLATE_ID` | No template_id specified |
| `TEMPLATE_NOT_FOUND` | Template does not exist |
| `TEMPLATE_INACTIVE` | Template is deactivated |
| `REQUIRED_FIELD_MISSING` | Mandatory field not provided |
| `INVALID_TYPE` | Field value has wrong type |
| `INVALID_TERM_REFERENCE` | Term reference not found |
| `INVALID_PATTERN` | Value doesn't match pattern |
| `OUT_OF_RANGE` | Numeric value outside range |
| `RULE_VIOLATION` | Conditional rule violated |
| `IDENTITY_FIELD_MISSING` | Identity field not provided |

---

## Registry

### Purpose

The Registry provides **federated identity management** across WIP instances and external systems. It is the **foundational service** that generates IDs for all WIP entities (terminologies, terms, templates, documents) and enables:

- Mapping composite keys to stable identifiers
- Cross-system identity resolution via **namespaces**
- Multiple keys resolving to the same entity via **synonyms**
- Duplicate resolution via **ID-as-synonym**

### Core Concepts

#### Namespaces

Namespaces prevent ID collisions when the same identifier exists in different systems.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              NAMESPACES                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐            │
│   │    default      │  │    vendor1      │  │    vendor2      │            │
│   │   (Registry     │  │   (External     │  │   (External     │            │
│   │    managed)     │  │    system)      │  │    system)      │            │
│   ├─────────────────┤  ├─────────────────┤  ├─────────────────┤            │
│   │                 │  │                 │  │                 │            │
│   │  ID: "XY"  ─────┼──┼── ID: "XY" ─────┼──┼── ID: "XY"      │            │
│   │                 │  │                 │  │                 │            │
│   │  (different     │  │  (different     │  │  (different     │            │
│   │   entities)     │  │   entities)     │  │   entities)     │            │
│   │                 │  │                 │  │                 │            │
│   └─────────────────┘  └─────────────────┘  └─────────────────┘            │
│                                                                              │
│   Same ID "XY" in different namespaces = different entities                 │
│   Namespaces enable coexistence without collision                           │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

| Namespace Type | ID Generation | Uniqueness |
|----------------|---------------|------------|
| **default** | Registry-managed (configurable strategy) | Enforced by Registry |
| **custom** | Per-namespace configurable strategy | Managed by source system |

#### Synonyms

Multiple composite keys can resolve to the **same** Registry ID. This enables cross-system identity matching.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           SYNONYM EXAMPLE                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   Registry ID: 550e8400-e29b-41d4-a716-446655440000 (preferred)             │
│                                                                              │
│   Synonyms (all resolve to the same entity):                                │
│   ┌──────────────┬─────────────────────────────────────────────────────┐   │
│   │  Namespace   │  Composite Key                                      │   │
│   ├──────────────┼─────────────────────────────────────────────────────┤   │
│   │  default     │  {product_id: "PROD-001", region: "EU", sku: "A1"}  │   │
│   │  vendor1     │  {vendor_sku: "AB-123"}                             │   │
│   │  vendor2     │  {part_number: "CD-456", revision: "2"}             │   │
│   │  erp_system  │  {material_id: "MAT999", plant: "DE01"}             │   │
│   └──────────────┴─────────────────────────────────────────────────────┘   │
│                                                                              │
│   Note: Each namespace can have different composite key structures          │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Use Case**: A product exists in your system as "XY", Vendor 1 calls it "AB", Vendor 2 calls it "CD". The Registry links all three as synonyms of the same entity.

#### ID-as-Synonym (Duplicate Resolution)

When the same entity was accidentally registered twice with different IDs, one ID can become a synonym for the other.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        DUPLICATE RESOLUTION                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   BEFORE: Two IDs for the same entity (mistake)                             │
│                                                                              │
│   Registry ID A: 550e8400-...  ──► Entity "Product X"                       │
│   Registry ID B: 661f9511-...  ──► Entity "Product X" (duplicate!)          │
│                                                                              │
│   ─────────────────────────────────────────────────────────────────────     │
│                                                                              │
│   AFTER: B becomes synonym for A                                            │
│                                                                              │
│   Registry ID A: 550e8400-...  ──► Entity "Product X" (PREFERRED)           │
│                       ▲                                                      │
│                       │ synonym                                              │
│   Registry ID B: 661f9511-...  ─┘                                           │
│                                                                              │
│   Result:                                                                    │
│   • Query for A → Returns A (preferred) + B (additional)                    │
│   • Query for B → Returns A (preferred) + B (additional)                    │
│   • Downstream systems using B continue to work                             │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Tradeoff**: An entity may have multiple valid IDs, but they all resolve consistently.

### Data Structures

#### Namespace

```json
{
  "id": "vendor1",
  "name": "Vendor 1 System",
  "description": "External vendor product catalog",
  "id_generator": {
    "type": "external",
    "description": "IDs provided by vendor"
  },
  "source_endpoint": "https://vendor1.example.com/api",
  "api_key_hash": "...",
  "status": "active",
  "created_at": "2024-01-01T00:00:00Z",
  "metadata": {}
}
```

#### Registry Entry

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "namespace": "default",
  "is_preferred": true,
  "composite_key_hash": "sha256:a1b2c3d4e5f6...",
  "composite_key_values": {
    "product_id": "PROD-001",
    "region": "EU",
    "sku": "A1"
  },
  "synonyms": [
    {
      "namespace": "vendor1",
      "composite_key_hash": "sha256:b2c3d4e5f6...",
      "composite_key_values": {"vendor_sku": "AB-123"}
    },
    {
      "namespace": "vendor2",
      "composite_key_hash": "sha256:c3d4e5f6g7...",
      "composite_key_values": {"part_number": "CD-456", "revision": "2"}
    }
  ],
  "additional_ids": ["661f9511-..."],
  "source_system": "wip-main",
  "status": "active",
  "created_at": "2024-01-15T10:00:00Z",
  "updated_at": "2024-02-20T14:30:00Z"
}
```

#### Query Response

All queries return **all IDs** with preferred ID indicated:

```json
{
  "preferred_id": "550e8400-e29b-41d4-a716-446655440000",
  "additional_ids": ["661f9511-..."],
  "namespace": "default",
  "composite_key_values": {
    "product_id": "PROD-001",
    "region": "EU",
    "sku": "A1"
  },
  "synonyms": [
    {
      "namespace": "vendor1",
      "composite_key_values": {"vendor_sku": "AB-123"}
    },
    {
      "namespace": "vendor2",
      "composite_key_values": {"part_number": "CD-456", "revision": "2"}
    }
  ],
  "source_system": "wip-main",
  "status": "active"
}
```

### ID Generators

Configurable per namespace:

| Generator | Format | Use Case |
|-----------|--------|----------|
| `uuid4` | `550e8400-e29b-41d4-a716-446655440000` | Default, universally unique |
| `uuid7` | Time-ordered UUID | Sortable by creation time |
| `nanoid` | `V1StGXR8_Z5jdHi6B-myT` | URL-friendly, shorter |
| `prefixed` | `TERM-001`, `TPL-002` | Human-readable with prefix |
| `external` | Any format | IDs provided by external system |
| `custom` | Configurable pattern | Domain-specific formats |

### Search Capabilities

#### Search Modes

| Mode | Description |
|------|-------------|
| **Full composite key** | Exact match on all key fields |
| **Partial key** | Match on subset of fields |
| **Individual field value** | Search by any single field value |
| **Cross-namespace** | Search across all namespaces (default) |
| **Single namespace** | Restrict search to one namespace |

#### Search Request

```json
{
  "namespace": null,
  "search": {
    "field": "vendor_sku",
    "value": "AB-123"
  },
  "include_synonyms": true
}
```

#### Search by Composite Key

```json
{
  "namespace": "vendor2",
  "composite_key": {
    "part_number": "CD-456",
    "revision": "2"
  }
}
```

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| **Namespaces** | | |
| GET | `/api/registry/namespaces` | List all namespaces |
| POST | `/api/registry/namespaces` | Create namespace |
| GET | `/api/registry/namespaces/{ns}` | Get namespace details |
| PUT | `/api/registry/namespaces/{ns}` | Update namespace |
| DELETE | `/api/registry/namespaces/{ns}` | Deactivate namespace |
| **Registration** | | |
| POST | `/api/registry/register` | Register new composite key |
| POST | `/api/registry/register-synonym` | Add synonym to existing ID |
| POST | `/api/registry/merge` | Merge two IDs (make one synonym of other) |
| **Lookup** | | |
| GET | `/api/registry/{ns}/{id}` | Lookup by namespace and ID |
| POST | `/api/registry/lookup` | Lookup by composite key |
| POST | `/api/registry/search` | Search by field values |
| **Management** | | |
| PUT | `/api/registry/{ns}/{id}` | Update entry |
| DELETE | `/api/registry/{ns}/{id}` | Deactivate entry |
| DELETE | `/api/registry/{ns}/{id}/synonyms/{syn_ns}/{syn_hash}` | Remove synonym |
| PUT | `/api/registry/{ns}/{id}/preferred` | Set as preferred ID |

### Authentication

Registry uses **API keys** for system-to-system authentication:

```http
POST /api/registry/register
X-API-Key: wip_sk_live_abc123...
Content-Type: application/json

{
  "namespace": "default",
  "composite_key": {
    "product_id": "PROD-001",
    "region": "EU"
  }
}
```

### WIP Integration

The Registry serves as the **ID generator for all WIP components**:

| Component | Registry Usage |
|-----------|----------------|
| **Terminologies** | IDs generated via Registry (namespace: `wip-terminologies`) |
| **Terms** | IDs generated via Registry (namespace: `wip-terms`) |
| **Templates** | IDs generated via Registry (namespace: `wip-templates`) |
| **Documents** | IDs generated via Registry (namespace: `wip-documents`) |

This ensures consistent identity management across the entire WIP ecosystem.

---

## Web UIs

### Ontology/Terminology Editor

**Purpose**: Manage the Def-Store (terminologies and terms).

**Features**:
- Tree view of terminologies and terms
- Drag-drop reordering for hierarchical terms
- Version history viewer
- Import/export (CSV, JSON)
- Search across all terms
- Bulk operations

**Key Components**:
- `TreeTable` for hierarchy display
- `Dialog` for term editing
- `DataTable` for flat views
- `FileUpload` for imports

### Template Editor

**Purpose**: Create and manage templates.

**Features**:
- Visual field designer
- Drag-drop field reordering
- Rule builder (visual)
- Template inheritance visualization
- Preview mode (sample document)
- Validation testing
- Version comparison

**Key Components**:
- `OrderList` for field arrangement
- `Panel` for field configuration
- `Dropdown` for terminology selection
- `TabView` for rule categories

### Admin UI

**Purpose**: Direct data curation and fixes.

**Features**:
- Browse any store
- Direct document editing (⚠️ bypasses some validations)
- Bulk status changes
- Version restoration
- Archive management
- System health dashboard

**Access**: Highly restricted (admin role only)

### Query Builder

**Purpose**: Build and execute ad-hoc queries.

**Features**:
- Visual filter builder
- Template-aware field suggestions
- Save/load queries
- Export results (CSV, JSON)
- Query history
- Share queries with team

**Key Components**:
- `AutoComplete` for fields
- `MultiSelect` for term values
- `DataTable` for results
- `Dialog` for save/load

---

## Reporting Layer

### Purpose

Provide a **relational database projection** of document data for SQL-based querying and reporting tools.

### Architecture

```
Document Store ──► Sync Worker ──► Reporting Store
   (MongoDB)           │           (PostgreSQL)
                       │
                ┌──────┴──────┐
                │  Transform  │
                │             │
                │ • Flatten   │
                │ • Type map  │
                │ • Index     │
                └─────────────┘
```

### Table Generation

For each template, a corresponding table is generated:

**Template**: `template-person`

**Generated Table**: `doc_person`

```sql
CREATE TABLE doc_person (
    id UUID PRIMARY KEY,
    version INTEGER,
    status VARCHAR(20),
    created_at TIMESTAMP,
    updated_at TIMESTAMP,

    -- Flattened data fields
    first_name VARCHAR(255),
    last_name VARCHAR(255),
    gender VARCHAR(50),
    birth_date DATE,
    national_id VARCHAR(50),

    -- Nested objects flattened
    address_street VARCHAR(255),
    address_city VARCHAR(255),
    address_postal_code VARCHAR(20),
    address_country VARCHAR(10),

    -- Original JSON for complex queries
    data_json JSONB
);
```

### Sync Modes

Configured per deployment:

```yaml
reporting:
  enabled: true
  store:
    type: postgresql
    connection: postgresql://localhost:5432/wip_reporting

  sync:
    mode: event  # batch, event, queue

    # Batch mode settings
    batch:
      schedule: "0 */6 * * *"  # Every 6 hours

    # Event mode settings
    event:
      debounce_ms: 1000

    # Queue mode settings
    queue:
      subject: "wip.documents.>"
```

---

## Message Queue

### Purpose

Enable **asynchronous communication** for event-driven sync and notifications.

### NATS Subjects

| Subject | Purpose |
|---------|---------|
| `wip.documents.created` | New document created |
| `wip.documents.updated` | Document updated (new version) |
| `wip.documents.deactivated` | Document deactivated |
| `wip.templates.created` | New template created |
| `wip.templates.updated` | Template updated |
| `wip.sync.request` | Request sync for document |
| `wip.sync.complete` | Sync completed |

### Event Format

```json
{
  "event_id": "evt-123",
  "event_type": "document.created",
  "timestamp": "2024-01-15T10:00:00Z",
  "payload": {
    "document_id": "doc-456",
    "template_id": "template-person",
    "identity_hash": "a1b2c3..."
  }
}
```

### JetStream (Persistence)

For guaranteed delivery:

```yaml
nats:
  jetstream:
    enabled: true
    streams:
      - name: WIP_EVENTS
        subjects:
          - "wip.>"
        retention: limits
        max_age: 168h  # 7 days
        max_bytes: 1073741824  # 1GB
```

---

## Auth Service

### Purpose

Centralized **authentication and authorization** for all WIP components.

### Integration

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                  │
│   User ──► UI ──► Authentik ──► JWT ──► API ──► Protected      │
│                                               Resource          │
│                                                                  │
│   System ──► API Key ──► API ──► Protected Resource             │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### JWT Claims

```json
{
  "sub": "user-123",
  "email": "alice@example.com",
  "name": "Alice Smith",
  "roles": ["editor", "architect"],
  "groups": ["team-europe"],
  "exp": 1705320000,
  "iat": 1705316400
}
```

### API Key Format

```
wip_sk_live_abc123def456...
│   │  │    │
│   │  │    └── Random bytes (base64)
│   │  └─────── Environment (live/test)
│   └────────── Type (sk = secret key)
└────────────── Prefix
```

### Configuration

```yaml
auth:
  provider: authentik  # authentik, authelia, none

  authentik:
    url: http://authentik:9000
    client_id: wip-api
    client_secret: ${AUTHENTIK_SECRET}

  api_keys:
    enabled: true
    hash_algorithm: argon2id
```
