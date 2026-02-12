# Data Models

This document defines the conceptual data structures used throughout World In a Pie (WIP).

---

## Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DATA MODEL HIERARCHY                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   DEF-STORE                    TEMPLATE STORE              DOCUMENT STORE   │
│   ══════════                   ══════════════              ══════════════   │
│                                                                              │
│   ┌──────────────┐            ┌──────────────┐            ┌──────────────┐  │
│   │ Terminology  │            │   Template   │            │   Document   │  │
│   │              │◄───────────│              │◄───────────│              │  │
│   │ • term_id    │  references│ • template_id│  conforms  │ • document_id│  │
│   │ • code       │            │ • code       │  to        │ • template_id│  │
│   │ • name       │            │ • version    │            │ • version    │  │
│   │              │            │ • fields[]   │            │ • data{}     │  │
│   └──────────────┘            │ • rules[]    │            │ • term_refs{}│  │
│          │                    │ • reporting{}│            └──────────────┘  │
│          │ contains           └──────────────┘                              │
│          ▼                           │                                      │
│   ┌──────────────┐                   │ contains                             │
│   │    Term      │                   ▼                                      │
│   │              │            ┌──────────────┐                              │
│   │ • term_id    │            │    Field     │                              │
│   │ • code       │            │              │                              │
│   │ • value      │◄───────────│ • name       │                              │
│   │ • aliases[]  │  references│ • type       │                              │
│   │ • parent_id  │            │ • term_ref   │                              │
│   └──────────────┘            └──────────────┘                              │
│                                                                              │
│   Note: Terms have NO versioning - changes tracked via audit log            │
│   Note: Templates can have multiple active versions simultaneously          │
│   Note: Documents store both original data AND resolved term_references     │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Def-Store Models

### Terminology

A terminology is a controlled vocabulary containing related terms. **Terminologies do not have versioning.**

```python
class Terminology(BaseModel):
    """A controlled vocabulary containing related terms."""

    terminology_id: str = Field(
        ...,
        description="Unique identifier (format: TERM-XXXXXX)",
        examples=["TERM-000001"]
    )
    code: str = Field(
        ...,
        description="Short code for the terminology",
        examples=["GENDER", "COUNTRY", "DEPARTMENT"]
    )
    name: str = Field(
        ...,
        description="Human-readable name",
        examples=["Gender"]
    )
    description: str | None = Field(
        None,
        description="Detailed description of the terminology"
    )
    status: Literal["active", "deprecated", "inactive"] = Field(
        default="active",
        description="Lifecycle status"
    )
    created_at: datetime
    created_by: str = Field(
        ...,
        description="Identity string (e.g., 'apikey:legacy', 'user:admin-001')"
    )
    updated_at: datetime | None = None
    updated_by: str | None = None
```

**Example:**
```json
{
  "terminology_id": "TERM-000001",
  "code": "GENDER",
  "name": "Gender",
  "description": "Controlled vocabulary for gender identification",
  "status": "active",
  "created_at": "2024-01-15T10:00:00Z",
  "created_by": "apikey:legacy",
  "updated_at": "2024-02-01T14:30:00Z",
  "updated_by": "user:admin-001"
}
```

### Term

A single concept within a terminology. **Terms do not have versioning - all changes are tracked in an audit log.**

```python
class Term(BaseModel):
    """An individual concept within a terminology."""

    term_id: str = Field(
        ...,
        description="Unique identifier (format: T-XXXXXX)",
        examples=["T-000001"]
    )
    terminology_id: str = Field(
        ...,
        description="Parent terminology reference"
    )
    code: str = Field(
        ...,
        description="Short code for the term",
        examples=["M", "F", "OTHER"]
    )
    value: str = Field(
        ...,
        description="Primary display value",
        examples=["Male", "Female"]
    )
    aliases: list[str] = Field(
        default_factory=list,
        description="Alternative values that resolve to this term",
        examples=[["MR", "Mr", "Mr.", "MALE"]]
    )
    description: str | None = Field(
        None,
        description="Detailed description"
    )
    status: Literal["active", "deprecated", "inactive"] = Field(
        default="active"
    )
    parent_id: str | None = Field(
        None,
        description="Parent term ID for hierarchical taxonomies"
    )
    sort_order: int = Field(
        default=0,
        description="Display order within terminology"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata (mappings, etc.)"
    )
    translations: dict[str, str] = Field(
        default_factory=dict,
        description="Translations by language code",
        examples=[{"de": "Männlich", "fr": "Masculin"}]
    )
    created_at: datetime
    created_by: str
    updated_at: datetime | None = None
    updated_by: str | None = None
```

**Example:**
```json
{
  "term_id": "T-000001",
  "terminology_id": "TERM-000001",
  "code": "M",
  "value": "Male",
  "aliases": ["MR", "Mr", "Mr.", "MALE", "mr"],
  "description": "Male gender identity",
  "status": "active",
  "parent_id": null,
  "sort_order": 1,
  "metadata": {
    "iso_5218": "1",
    "hl7_v3": "M"
  },
  "translations": {
    "de": "Männlich",
    "fr": "Masculin"
  },
  "created_at": "2024-01-15T10:00:00Z",
  "created_by": "apikey:legacy"
}
```

### Term Alias Resolution

When validating a value against a terminology, the system checks in order:
1. **code** - Exact match on term code
2. **value** - Exact match on primary value
3. **aliases** - Match against any alias

The validation response indicates which match type was used:

```json
{
  "terminology_code": "GENDER",
  "input_value": "Mr.",
  "valid": true,
  "term_id": "T-000001",
  "matched_via": "alias",
  "normalized_value": "Male"
}
```

### Term Audit Log

Instead of versioning, term changes are recorded in an audit log:

```python
class TermAuditEntry(BaseModel):
    """An audit log entry for term changes."""

    term_id: str
    terminology_id: str
    action: Literal["created", "updated", "deprecated", "deleted"]
    changed_at: datetime
    changed_by: str
    changed_fields: list[str] = Field(
        default_factory=list,
        description="Fields that were modified"
    )
    previous_values: dict[str, Any] = Field(
        default_factory=dict
    )
    new_values: dict[str, Any] = Field(
        default_factory=dict
    )
```

**Example:**
```json
{
  "term_id": "T-000001",
  "terminology_id": "TERM-000001",
  "action": "updated",
  "changed_at": "2024-01-30T10:00:00Z",
  "changed_by": "user:admin-001",
  "changed_fields": ["aliases"],
  "previous_values": {"aliases": ["MR", "MR."]},
  "new_values": {"aliases": ["MR", "MR.", "Mr.", "mr"]}
}
```

### Hierarchical Terms Example

```json
[
  {
    "term_id": "T-000010",
    "terminology_id": "TERM-000005",
    "code": "ENG",
    "value": "Engineering",
    "parent_id": null,
    "sort_order": 1
  },
  {
    "term_id": "T-000011",
    "terminology_id": "TERM-000005",
    "code": "FE",
    "value": "Frontend",
    "parent_id": "T-000010",
    "sort_order": 1
  },
  {
    "term_id": "T-000012",
    "terminology_id": "TERM-000005",
    "code": "BE",
    "value": "Backend",
    "parent_id": "T-000010",
    "sort_order": 2
  }
]
```

---

## Template Store Models

### Template

A schema definition for documents. **Multiple template versions can be active simultaneously** for gradual migration scenarios.

```python
class Template(BaseModel):
    """A schema definition that documents must conform to."""

    template_id: str = Field(
        ...,
        description="Unique identifier (format: TPL-XXXXXX)",
        examples=["TPL-000001"]
    )
    code: str = Field(
        ...,
        description="Template code (shared across versions)",
        examples=["PERSON", "EMPLOYEE"]
    )
    name: str = Field(
        ...,
        description="Human-readable name",
        examples=["Person"]
    )
    description: str | None = None
    version: int = Field(
        default=1,
        description="Version number (incremented on updates)"
    )
    status: Literal["active", "deprecated", "inactive"] = Field(
        default="active"
    )
    extends: str | None = Field(
        None,
        description="Parent template code for inheritance"
    )
    identity_fields: list[str] = Field(
        ...,
        description="Fields that form the composite identity key"
    )
    fields: list[TemplateField] = Field(
        default_factory=list
    )
    rules: list[ValidationRule] = Field(
        default_factory=list
    )
    reporting: ReportingConfig | None = Field(
        None,
        description="PostgreSQL sync configuration"
    )
    created_at: datetime
    created_by: str
    updated_at: datetime | None = None
    updated_by: str | None = None
```

### TemplateField

A field definition within a template.

```python
class FieldType(str, Enum):
    STRING = "string"
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    TERM = "term"
    OBJECT = "object"
    ARRAY = "array"
    REFERENCE = "reference"  # Cross-document reference


class TemplateField(BaseModel):
    """A field definition within a template."""

    name: str = Field(
        ...,
        description="Field name (used in document data)",
        examples=["first_name", "birth_date", "gender"]
    )
    type: FieldType
    mandatory: bool = Field(default=False)
    description: str | None = None

    # Type-specific configurations
    terminology_ref: str | None = Field(
        None,
        description="Terminology code (for term type)"
    )
    template_ref: str | None = Field(
        None,
        description="Template code (for object type)"
    )
    reference_template: str | None = Field(
        None,
        description="Template code (for reference type)"
    )
    items: "TemplateField | None" = Field(
        None,
        description="Item definition (for array type)"
    )

    # Validation constraints
    min_length: int | None = None
    max_length: int | None = None
    minimum: float | None = None
    maximum: float | None = None
    pattern: str | None = Field(
        None,
        description="Regex pattern for string fields"
    )
    enum: list[Any] | None = Field(
        None,
        description="Allowed values (not term-based)"
    )
```

### ReportingConfig

Configuration for PostgreSQL sync:

```python
class ReportingConfig(BaseModel):
    """Configuration for reporting sync to PostgreSQL."""

    sync_enabled: bool = Field(default=True)
    sync_strategy: Literal["latest_only", "all_versions", "disabled"] = Field(
        default="latest_only"
    )
    table_name: str | None = Field(
        None,
        description="Custom table name (default: doc_{code})"
    )
    include_metadata: bool = Field(default=True)
    flatten_arrays: bool = Field(default=True)
    max_array_elements: int = Field(default=10)
```

### ValidationRule

Cross-field validation rules:

```python
class RuleType(str, Enum):
    CONDITIONAL_REQUIRED = "conditional_required"
    CONDITIONAL_VALUE = "conditional_value"
    MUTUAL_EXCLUSION = "mutual_exclusion"
    DEPENDENCY = "dependency"


class ValidationRule(BaseModel):
    """A cross-field validation rule."""

    type: RuleType
    description: str | None = None
    condition: RuleCondition | None = None
    target_field: str | None = None
    target_fields: list[str] | None = None  # For mutual_exclusion
    error_message: str | None = None


class RuleCondition(BaseModel):
    """A condition for conditional rules."""

    field: str
    operator: Literal["equals", "not_equals", "in", "not_in", "exists", "not_exists"]
    value: Any = None
```

**Template Example:**
```json
{
  "template_id": "TPL-000001",
  "code": "PERSON",
  "name": "Person",
  "description": "Template for person records",
  "version": 3,
  "status": "active",
  "extends": null,
  "identity_fields": ["email"],
  "fields": [
    {
      "name": "first_name",
      "type": "string",
      "mandatory": true,
      "min_length": 1,
      "max_length": 100
    },
    {
      "name": "last_name",
      "type": "string",
      "mandatory": true
    },
    {
      "name": "email",
      "type": "string",
      "mandatory": true,
      "pattern": "^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$"
    },
    {
      "name": "gender",
      "type": "term",
      "mandatory": false,
      "terminology_ref": "GENDER"
    },
    {
      "name": "country",
      "type": "term",
      "terminology_ref": "COUNTRY"
    },
    {
      "name": "tax_id",
      "type": "string",
      "mandatory": false
    },
    {
      "name": "addresses",
      "type": "array",
      "items": {
        "name": "address",
        "type": "object",
        "template_ref": "ADDRESS"
      }
    }
  ],
  "rules": [
    {
      "type": "conditional_required",
      "description": "Tax ID required for German residents",
      "condition": {
        "field": "country",
        "operator": "equals",
        "value": "Germany"
      },
      "target_field": "tax_id",
      "error_message": "Tax ID is required for German residents"
    }
  ],
  "reporting": {
    "sync_enabled": true,
    "sync_strategy": "latest_only",
    "table_name": "doc_person",
    "flatten_arrays": true
  },
  "created_at": "2024-01-15T10:00:00Z",
  "created_by": "apikey:legacy"
}
```

### Template Versioning

When a template is updated, a **new document with a new template_id** is created. The original remains active.

| Operation | Result |
|-----------|--------|
| Create template (code=PERSON) | TPL-000001, version=1 |
| Update TPL-000001 | NEW TPL-000002, version=2, **TPL-000001 still active** |
| Update TPL-000002 | NEW TPL-000003, version=3, **all still active** |

All versions share the same `code` but have different `template_id` values.

---

## Document Store Models

### Document

The core data entity. Documents store both the original data and resolved term references.

```python
class Document(BaseModel):
    """A validated document conforming to a template."""

    document_id: str = Field(
        ...,
        description="Unique document ID (UUID7 for time-ordering)"
    )
    template_id: str = Field(
        ...,
        description="Template ID this document conforms to"
    )
    template_code: str = Field(
        ...,
        description="Template code (for easier querying)"
    )
    template_version: int = Field(
        ...,
        description="Version of template used for validation"
    )
    identity_hash: str = Field(
        ...,
        description="SHA-256 hash of identity fields"
    )
    version: int = Field(
        default=1,
        description="Document version number"
    )
    status: Literal["active", "inactive", "archived"] = Field(
        default="active"
    )
    is_latest_version: bool = Field(
        ...,
        description="Whether this is the current version"
    )
    latest_version: int = Field(
        ...,
        description="The highest version number for this identity"
    )
    latest_document_id: str = Field(
        ...,
        description="Document ID of the latest version"
    )
    data: dict[str, Any] = Field(
        ...,
        description="Original submitted document content"
    )
    term_references: dict[str, str | list[str]] = Field(
        default_factory=dict,
        description="Resolved term IDs for term fields"
    )
    created_at: datetime
    created_by: str
    updated_at: datetime | None = None
    updated_by: str | None = None
```

**Key design decisions:**

1. **`data`** stores the original submitted values (e.g., `"gender": "Female"`)
2. **`term_references`** stores resolved term IDs (e.g., `"gender": "T-000002"`)
3. Both are preserved for audit compliance - original values never migrated
4. **`is_latest_version`** and **`latest_document_id`** enable navigation from old versions

**Example:**
```json
{
  "document_id": "0192abc1-def2-7abc-8def-123456789abc",
  "template_id": "TPL-000001",
  "template_code": "PERSON",
  "template_version": 3,
  "identity_hash": "a1b2c3d4e5f6g7h8i9j0...",
  "version": 2,
  "status": "active",
  "is_latest_version": true,
  "latest_version": 2,
  "latest_document_id": "0192abc1-def2-7abc-8def-123456789abc",
  "data": {
    "first_name": "Alice",
    "last_name": "Schmidt",
    "email": "alice@example.com",
    "gender": "Female",
    "country": "Germany",
    "tax_id": "12345678901",
    "addresses": [
      {
        "street": "Hauptstraße 1",
        "city": "Berlin",
        "postal_code": "10115",
        "country": "Germany"
      }
    ]
  },
  "term_references": {
    "gender": "T-000002",
    "country": "T-000042",
    "addresses.0.country": "T-000042"
  },
  "created_at": "2024-01-15T10:00:00Z",
  "created_by": "user:admin-001",
  "updated_at": "2024-02-20T14:30:00Z",
  "updated_by": "user:admin-001"
}
```

### Document Versioning

Documents use identity-based versioning. When a document with the same identity_hash is submitted:

1. The existing active document is deactivated
2. A new document is created with an incremented version number
3. Both share the same `identity_hash`

```
Document v1 (identity_hash: abc123)
    status: inactive
    document_id: 0192aaa...
    version: 1
    is_latest_version: false
    latest_document_id: 0192bbb...

Document v2 (identity_hash: abc123)
    status: active
    document_id: 0192bbb...
    version: 2
    is_latest_version: true
    latest_document_id: 0192bbb...
```

---

## Registry Models

### Namespace

A logical partition for IDs to prevent collisions across systems.

```python
class IdGeneratorConfig(BaseModel):
    """Configuration for ID generation within a namespace."""

    type: Literal["uuid4", "uuid7", "prefixed", "external"] = Field(
        default="uuid4"
    )
    prefix: str | None = Field(
        None,
        description="Prefix for prefixed generator (e.g., 'TERM-', 'TPL-')"
    )


class Namespace(BaseModel):
    """A logical partition in the Registry for ID isolation."""

    namespace_id: str = Field(
        ...,
        description="Unique namespace identifier",
        examples=["default", "wip-terminologies"]
    )
    name: str
    description: str | None = None
    id_generator: IdGeneratorConfig = Field(
        default_factory=IdGeneratorConfig
    )
    status: Literal["active", "inactive"] = Field(default="active")
    created_at: datetime
```

### WIP Internal Namespaces

The Registry pre-configures namespaces for WIP components:

| Namespace | ID Generator | Format | Used By |
|-----------|--------------|--------|---------|
| `wip-terminologies` | prefixed | `TERM-000001` | Def-Store |
| `wip-terms` | prefixed | `T-000001` | Def-Store |
| `wip-templates` | prefixed | `TPL-000001` | Template Store |
| `wip-documents` | uuid7 | `0192abc1-def2-7abc-...` | Document Store |
| `default` | uuid4 | `550e8400-e29b-41d4-...` | General use |

### Registry Entry

A registry entry stores a canonical ID with its composite key and optional synonyms.

```python
class RegistryEntry(BaseModel):
    entry_id: str          # Canonical ID (e.g., UUID7, TPL-000001)
    primary_pool_id: str   # Pool this entry belongs to
    primary_composite_key: dict[str, Any]  # Original composite key
    additional_ids: list[dict[str, str]]   # Merged IDs from entry merges
    synonyms: list[Synonym]               # Alternative composite keys
    search_values: list[str]              # Flattened string values from all composite keys
    status: str            # "active" or "inactive"
    source_info: SourceInfo | None
    metadata: dict[str, Any]
```

**`search_values`** is a flat array containing all string values extracted from `primary_composite_key` and all `synonyms[].composite_key`. It is automatically rebuilt whenever synonyms are added, removed, or entries are merged. This enables efficient value-based lookups — any string value from any composite key can be resolved to its canonical entry via a single indexed query.

**Example:**
```json
{
  "entry_id": "0192abc1-def2-7abc-...",
  "primary_pool_id": "wip-documents",
  "primary_composite_key": {
    "identity_hash": "abc123...",
    "template_id": "TPL-000001"
  },
  "synonyms": [
    {
      "pool_id": "wip-documents",
      "composite_key": { "external_id": "ERP-CUS-001" }
    }
  ],
  "search_values": ["ERP-CUS-001", "TPL-000001", "abc123..."],
  "additional_ids": []
}
```

---

## Event Models

Events are published to NATS and contain the **full document** (self-contained for reliable processing).

```python
class EventType(str, Enum):
    DOCUMENT_CREATED = "document.created"
    DOCUMENT_UPDATED = "document.updated"
    DOCUMENT_DELETED = "document.deleted"
    TEMPLATE_CREATED = "template.created"
    TEMPLATE_UPDATED = "template.updated"


class DocumentEvent(BaseModel):
    """An event published when a document changes."""

    event_id: str
    event_type: EventType
    timestamp: datetime
    document: Document  # Full document included
```

**Example Event:**
```json
{
  "event_id": "evt-123",
  "event_type": "document.created",
  "timestamp": "2024-01-15T10:00:00Z",
  "document": {
    "document_id": "0192abc...",
    "template_id": "TPL-000001",
    "template_code": "PERSON",
    "version": 1,
    "status": "active",
    "data": {
      "first_name": "Alice",
      "email": "alice@example.com"
    },
    "term_references": {}
  }
}
```

**Why full document in events:**
- Self-contained: Sync worker doesn't need to fetch from source
- No race conditions: Document state at event time is captured
- Simpler processing: Just transform and upsert

---

## Identity Hash Algorithm

```python
import hashlib

def compute_identity_hash(
    data: dict[str, Any],
    identity_fields: list[str]
) -> str:
    """
    Compute a deterministic hash from identity fields.

    Algorithm:
    1. Sort identity field names alphanumerically
    2. Build normalized string: field1=value1|field2=value2|...
    3. Hash with SHA-256
    4. Return hex digest
    """
    sorted_fields = sorted(identity_fields)

    parts = []
    for field in sorted_fields:
        value = data.get(field, "")
        if value is None:
            value = ""
        else:
            value = str(value)
        parts.append(f"{field}={value}")

    normalized = "|".join(parts)

    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
```

**Example:**
```python
data = {
    "first_name": "Alice",
    "email": "alice@example.com"
}
identity_fields = ["email"]

# Normalized: "email=alice@example.com"
# Result: sha256("email=alice@example.com")
hash = compute_identity_hash(data, identity_fields)
# → "a1b2c3d4e5f6..."
```

---

## API Response Models

### Paginated Response

```python
class PaginatedResponse(BaseModel, Generic[T]):
    """Paginated list response."""

    items: list[T]
    total: int
    page: int
    page_size: int
    has_more: bool
```

### Validation Result

```python
class ValidationResult(BaseModel):
    """Result of document validation."""

    valid: bool
    identity_hash: str | None = None
    is_update: bool = False
    existing_document_id: str | None = None
    errors: list[ValidationError] = []
    warnings: list[ValidationWarning] = []
    term_references: dict[str, str] = {}


class ValidationError(BaseModel):
    field: str | None = None
    code: str
    message: str


class ValidationWarning(BaseModel):
    field: str | None = None
    code: str
    message: str
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
| `INVALID_TERM_REFERENCE` | Term value not found in terminology |
| `INVALID_PATTERN` | Value doesn't match pattern |
| `OUT_OF_RANGE` | Numeric value outside range |
| `RULE_VIOLATION` | Cross-field rule violated |
| `IDENTITY_FIELD_MISSING` | Identity field not provided |
