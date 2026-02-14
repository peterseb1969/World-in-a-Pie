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
│   │ • term_id    │  canonical │ • template_id│  conforms  │ • document_id│  │
│   │ • code       │  IDs       │ • code       │  to        │ • template_id│  │
│   │ • name       │            │ • version    │            │ • version    │  │
│   │              │            │ • fields[]   │            │ • data{}     │  │
│   └──────────────┘            │ • rules[]    │            │ • term_refs[]│  │
│          │                    │ • reporting{}│            │ • refs[]     │  │
│          │ contains           └──────────────┘            └──────────────┘  │
│          ▼                           │                                      │
│   ┌──────────────┐                   │ contains                             │
│   │    Term      │                   ▼                                      │
│   │              │            ┌──────────────────┐                          │
│   │ • term_id    │            │  FieldDefinition │                          │
│   │ • code       │            │                  │                          │
│   │ • value      │◄───────────│ • name, label    │                          │
│   │ • aliases[]  │  canonical │ • type           │                          │
│   │ • parent_id  │  IDs       │ • terminology_ref│                          │
│   └──────────────┘            │ • template_ref   │                          │
│                               │ • version_strategy│                         │
│                               └──────────────────┘                          │
│                                                                              │
│   Note: Terms have NO versioning - changes tracked via audit log            │
│   Note: Templates can have multiple active versions simultaneously          │
│   Note: Documents store both original data AND resolved term_references     │
│   Note: All entity references stored as canonical IDs (TPL-/TERM-),         │
│         resolved from user-supplied codes at template creation time          │
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

    pool_id: str = Field(
        default="wip-templates",
        description="Pool ID for data isolation"
    )
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
    status: Literal["draft", "active", "deprecated", "inactive"] = Field(
        default="active"
    )
    extends: str | None = Field(
        None,
        description="Parent template_id for inheritance (TPL-XXXXXX)"
    )
    identity_fields: list[str] = Field(
        default_factory=list,
        description="Fields that form the composite identity key"
    )
    fields: list[FieldDefinition] = Field(
        default_factory=list
    )
    rules: list[ValidationRule] = Field(
        default_factory=list
    )
    metadata: TemplateMetadata = Field(
        default_factory=TemplateMetadata,
        description="Domain, category, tags, custom metadata"
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

### FieldDefinition

A field definition within a template. All entity reference fields store **canonical IDs** (resolved from user-supplied codes at template creation time).

```python
class FieldType(str, Enum):
    STRING = "string"
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    TERM = "term"          # Term from a Def-Store terminology
    REFERENCE = "reference"  # Cross-entity reference (document, term, terminology, template)
    FILE = "file"            # Binary file attachment (MinIO)
    OBJECT = "object"        # Nested template
    ARRAY = "array"          # Collection of items


class ReferenceType(str, Enum):
    DOCUMENT = "document"      # Reference to another document
    TERM = "term"              # Reference to a term in a terminology
    TERMINOLOGY = "terminology"  # Reference to a terminology itself
    TEMPLATE = "template"      # Reference to a template itself


class VersionStrategy(str, Enum):
    LATEST = "latest"   # Accept any version of the same template family (default)
    PINNED = "pinned"   # Accept only the exact stored template version


class SemanticType(str, Enum):
    EMAIL = "email"          # RFC 5322 email address
    URL = "url"              # Valid HTTP(S) URL
    LATITUDE = "latitude"    # Geographic latitude (-90 to 90)
    LONGITUDE = "longitude"  # Geographic longitude (-180 to 180)
    PERCENTAGE = "percentage"  # Percentage value (0 to 100)
    DURATION = "duration"    # Time duration {value, unit}
    GEO_POINT = "geo_point"  # Geographic point {latitude, longitude}


class FieldDefinition(BaseModel):
    """A field definition within a template."""

    name: str
    label: str                          # Human-readable label
    type: FieldType
    mandatory: bool = False
    default_value: Any | None = None

    # For type=term: terminology reference (stored as canonical TERM-XXXXXX)
    terminology_ref: str | None = Field(
        None,
        description="Canonical terminology_id (TERM-XXXXXX), resolved from code at creation"
    )

    # For type=object: nested template (stored as canonical TPL-XXXXXX)
    template_ref: str | None = Field(
        None,
        description="Canonical template_id (TPL-XXXXXX), resolved from code at creation"
    )

    # For type=reference: unified reference configuration
    reference_type: ReferenceType | None = None
    target_templates: list[str] | None = Field(
        None,
        description="Canonical template_ids for document references (resolved from codes at creation)"
    )
    include_subtypes: bool | None = Field(
        None,
        description="When true, also accepts documents from child templates via inheritance"
    )
    target_terminologies: list[str] | None = Field(
        None,
        description="Canonical terminology_ids for term references (resolved from codes at creation)"
    )
    version_strategy: VersionStrategy | None = Field(
        None,
        description="How to resolve reference versions: latest (default) or pinned"
    )

    # For type=file
    file_config: FileFieldConfig | None = None

    # For type=array: item configuration
    array_item_type: FieldType | None = None
    array_terminology_ref: str | None = Field(
        None,
        description="Canonical terminology_id (TERM-XXXXXX) for array term items"
    )
    array_template_ref: str | None = Field(
        None,
        description="Canonical template_id (TPL-XXXXXX) for array object items"
    )
    array_file_config: FileFieldConfig | None = None

    # Validation constraints (nested object)
    validation: FieldValidation | None = None

    # Semantic type for universal data patterns
    semantic_type: SemanticType | None = None

    # Inheritance tracking (populated during resolution, not stored)
    inherited: bool | None = None
    inherited_from: str | None = None

    # Additional metadata
    metadata: dict[str, Any] = {}


class FieldValidation(BaseModel):
    pattern: str | None = None      # Regex pattern for string fields
    min_length: int | None = None
    max_length: int | None = None
    minimum: float | None = None    # Minimum numeric value
    maximum: float | None = None    # Maximum numeric value
    enum: list[Any] | None = None   # Allowed values (not term-based)


class FileFieldConfig(BaseModel):
    allowed_types: list[str] = ["*/*"]  # MIME type patterns
    max_size_mb: float = 10.0           # Max file size (up to 100MB)
    multiple: bool = False              # Allow multiple files
    max_files: int | None = None        # Max files when multiple=true
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

**Template Example (as stored — all references are canonical IDs):**
```json
{
  "pool_id": "wip-templates",
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
      "label": "First Name",
      "type": "string",
      "mandatory": true,
      "validation": { "min_length": 1, "max_length": 100 }
    },
    {
      "name": "last_name",
      "label": "Last Name",
      "type": "string",
      "mandatory": true
    },
    {
      "name": "email",
      "label": "Email",
      "type": "string",
      "mandatory": true,
      "semantic_type": "email"
    },
    {
      "name": "gender",
      "label": "Gender",
      "type": "term",
      "terminology_ref": "TERM-000001"
    },
    {
      "name": "country",
      "label": "Country",
      "type": "term",
      "terminology_ref": "TERM-000005"
    },
    {
      "name": "tax_id",
      "label": "Tax ID",
      "type": "string"
    },
    {
      "name": "addresses",
      "label": "Addresses",
      "type": "array",
      "array_item_type": "object",
      "array_template_ref": "TPL-000008"
    },
    {
      "name": "supervisor",
      "label": "Supervisor",
      "type": "reference",
      "reference_type": "document",
      "target_templates": ["TPL-000001"],
      "version_strategy": "latest",
      "include_subtypes": true
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
  "metadata": {
    "domain": "hr",
    "category": "master_data",
    "tags": ["person", "core"]
  },
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

The core data entity. Documents store original data plus resolved term references, entity references, and file references.

```python
class Document(BaseModel):
    """A validated document conforming to a template."""

    pool_id: str = Field(
        default="wip-documents",
        description="Pool ID for data isolation"
    )
    document_id: str = Field(
        ...,
        description="Unique document ID (UUID7 for time-ordering)"
    )
    template_id: str = Field(
        ...,
        description="Template ID this document conforms to (TPL-XXXXXX)"
    )
    template_pool_id: str = Field(
        default="wip-templates",
        description="Pool ID of the template"
    )
    template_code: str | None = Field(
        None,
        description="Template code for easier identification"
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
    data: dict[str, Any] = Field(
        ...,
        description="Original submitted document content"
    )

    # Resolved references (populated during validation)
    term_references: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Resolved term IDs: [{field_path, term_id, terminology_ref, matched_via}]"
    )
    references: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Resolved entity references: [{field_path, reference_type, resolved: {...}}]"
    )
    file_references: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Resolved file refs: [{field_path, file_id, filename, content_type, size_bytes}]"
    )

    metadata: DocumentMetadata = Field(
        default_factory=DocumentMetadata,
        description="Source system, warnings, custom metadata"
    )
    created_at: datetime
    created_by: str
    updated_at: datetime | None = None
    updated_by: str | None = None
```

**Key design decisions:**

1. **`data`** stores the original submitted values (e.g., `"gender": "Female"`)
2. **`term_references`** stores resolved term IDs as an array of `{field_path, term_id, terminology_ref, matched_via}`
3. **`references`** stores resolved entity references (documents, terms, terminologies, templates)
4. **`file_references`** stores resolved file metadata for file fields
5. Both original data and resolved references are preserved for audit compliance

**Example:**
```json
{
  "pool_id": "wip-documents",
  "document_id": "0192abc1-def2-7abc-8def-123456789abc",
  "template_id": "TPL-000001",
  "template_code": "PERSON",
  "template_version": 3,
  "identity_hash": "a1b2c3d4e5f6g7h8i9j0...",
  "version": 2,
  "status": "active",
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
    ],
    "supervisor": "0192abc1-aaaa-7abc-8def-111111111111"
  },
  "term_references": [
    { "field_path": "gender", "term_id": "T-000002", "terminology_ref": "TERM-000001", "matched_via": "value" },
    { "field_path": "country", "term_id": "T-000042", "terminology_ref": "TERM-000005", "matched_via": "value" }
  ],
  "references": [
    {
      "field_path": "supervisor",
      "reference_type": "document",
      "lookup_value": "0192abc1-aaaa-7abc-8def-111111111111",
      "version_strategy": "latest",
      "resolved": {
        "document_id": "0192abc1-aaaa-7abc-8def-111111111111",
        "identity_hash": "f5e6d7c8...",
        "template_id": "TPL-000001",
        "version": 1
      }
    }
  ],
  "file_references": [],
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
| `wip-files` | prefixed | `FILE-000001` | File Storage |
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
    TEMPLATE_DELETED = "template.deleted"
    TEMPLATE_ACTIVATED = "template.activated"


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
    template_version: int | None = None
    template_code: str | None = None
    errors: list[dict] = []       # [{field, code, message, details}]
    warnings: list[str] = []
    term_references: list[dict] = []   # [{field_path, term_id, terminology_ref, matched_via}]
    references: list[dict] = []        # [{field_path, reference_type, resolved: {...}}]
    file_references: list[dict] = []   # [{field_path, file_id, filename, ...}]
    timing: dict[str, float] = {}      # stage -> milliseconds
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
