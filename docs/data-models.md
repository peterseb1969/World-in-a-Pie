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
│   │ • id         │  references│ • id         │  conforms  │ • id         │  │
│   │ • name       │            │ • name       │  to        │ • template_id│  │
│   │ • version    │            │ • version    │            │ • version    │  │
│   │ • terms[]    │            │ • fields[]   │            │ • data{}     │  │
│   └──────────────┘            │ • rules[]    │            └──────────────┘  │
│          │                    │ • extends    │                              │
│          │ contains           └──────────────┘                              │
│          ▼                           │                                      │
│   ┌──────────────┐                   │ contains                             │
│   │    Term      │                   ▼                                      │
│   │              │            ┌──────────────┐                              │
│   │ • id         │            │    Field     │                              │
│   │ • code       │            │              │                              │
│   │ • label      │◄───────────│ • name       │                              │
│   │ • parent_id  │  references│ • type       │                              │
│   └──────────────┘            │ • term_ref   │                              │
│                               └──────────────┘                              │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Def-Store Models

### Terminology

A terminology is a controlled vocabulary containing related terms.

```python
class Terminology(BaseModel):
    """A controlled vocabulary containing related terms."""

    id: str = Field(
        ...,
        description="Unique identifier",
        examples=["terminology-gender"]
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
    version: int = Field(
        default=1,
        description="Version number, incremented on updates"
    )
    status: Literal["active", "inactive", "archived"] = Field(
        default="active",
        description="Lifecycle status"
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Creation timestamp"
    )
    created_by: str = Field(
        ...,
        description="User or system that created this"
    )
    updated_at: datetime | None = Field(
        None,
        description="Last update timestamp"
    )
    updated_by: str | None = Field(
        None,
        description="User or system that last updated this"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata"
    )
```

**Example:**
```json
{
  "id": "terminology-gender",
  "name": "Gender",
  "description": "Controlled vocabulary for gender identification",
  "version": 2,
  "status": "active",
  "created_at": "2024-01-15T10:00:00Z",
  "created_by": "admin",
  "updated_at": "2024-02-01T14:30:00Z",
  "updated_by": "admin",
  "metadata": {
    "source": "ISO 5218",
    "domain": "demographics"
  }
}
```

### Term

A single concept within a terminology.

```python
class Term(BaseModel):
    """An individual concept within a terminology."""

    id: str = Field(
        ...,
        description="Unique identifier",
        examples=["term-gender-male"]
    )
    terminology_id: str = Field(
        ...,
        description="Parent terminology reference"
    )
    code: str = Field(
        ...,
        description="Short code for the term",
        examples=["M", "F"]
    )
    label: str = Field(
        ...,
        description="Human-readable label",
        examples=["Male", "Female"]
    )
    description: str | None = Field(
        None,
        description="Detailed description"
    )
    version: int = Field(
        default=1,
        description="Version number"
    )
    status: Literal["active", "inactive", "archived"] = Field(
        default="active"
    )
    parent_id: str | None = Field(
        None,
        description="Parent term for hierarchical taxonomies"
    )
    sort_order: int = Field(
        default=0,
        description="Display order within terminology"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata (mappings, translations, etc.)"
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: str
    updated_at: datetime | None = None
    updated_by: str | None = None
```

**Example:**
```json
{
  "id": "term-gender-male",
  "terminology_id": "terminology-gender",
  "code": "M",
  "label": "Male",
  "description": "Male gender identity",
  "version": 1,
  "status": "active",
  "parent_id": null,
  "sort_order": 1,
  "metadata": {
    "iso_5218": "1",
    "hl7_v3": "M",
    "translations": {
      "de": "Männlich",
      "fr": "Masculin"
    }
  },
  "created_at": "2024-01-15T10:00:00Z",
  "created_by": "admin"
}
```

### Hierarchical Terms Example

```json
[
  {
    "id": "term-location-europe",
    "terminology_id": "terminology-location",
    "code": "EU",
    "label": "Europe",
    "parent_id": null,
    "sort_order": 1
  },
  {
    "id": "term-location-germany",
    "terminology_id": "terminology-location",
    "code": "DE",
    "label": "Germany",
    "parent_id": "term-location-europe",
    "sort_order": 1
  },
  {
    "id": "term-location-berlin",
    "terminology_id": "terminology-location",
    "code": "DE-BE",
    "label": "Berlin",
    "parent_id": "term-location-germany",
    "sort_order": 1
  }
]
```

---

## Template Store Models

### Template

A schema definition for documents.

```python
class Template(BaseModel):
    """A schema definition that documents must conform to."""

    id: str = Field(
        ...,
        description="Unique identifier",
        examples=["template-person"]
    )
    name: str = Field(
        ...,
        description="Human-readable name",
        examples=["Person"]
    )
    description: str | None = Field(
        None,
        description="What this template represents"
    )
    version: int = Field(
        default=1,
        description="Version number"
    )
    status: Literal["active", "inactive", "archived"] = Field(
        default="active"
    )
    extends: str | None = Field(
        None,
        description="Parent template ID for inheritance"
    )
    identity_fields: list[str] = Field(
        ...,
        description="Fields that form the composite identity key"
    )
    fields: list[TemplateField] = Field(
        default_factory=list,
        description="Field definitions"
    )
    rules: list[ValidationRule] = Field(
        default_factory=list,
        description="Cross-field validation rules"
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: str
    updated_at: datetime | None = None
    updated_by: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
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


class TemplateField(BaseModel):
    """A field definition within a template."""

    name: str = Field(
        ...,
        description="Field name (used in data)",
        examples=["first_name", "birth_date"]
    )
    label: str = Field(
        ...,
        description="Human-readable label",
        examples=["First Name", "Date of Birth"]
    )
    type: FieldType = Field(
        ...,
        description="Data type"
    )
    mandatory: bool = Field(
        default=False,
        description="Whether field is required"
    )
    terminology_ref: str | None = Field(
        None,
        description="Reference to terminology (for term type)"
    )
    template_ref: str | None = Field(
        None,
        description="Reference to nested template (for object type)"
    )
    array_item_type: FieldType | None = Field(
        None,
        description="Type of array items (for array type)"
    )
    array_item_template_ref: str | None = Field(
        None,
        description="Template for array items if object type"
    )
    default_value: Any = Field(
        None,
        description="Default value if not provided"
    )
    validation: FieldValidation | None = Field(
        None,
        description="Field-level validation rules"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional field metadata"
    )


class FieldValidation(BaseModel):
    """Field-level validation constraints."""

    pattern: str | None = Field(
        None,
        description="Regex pattern for string fields"
    )
    min_length: int | None = Field(
        None,
        description="Minimum string length"
    )
    max_length: int | None = Field(
        None,
        description="Maximum string length"
    )
    minimum: float | None = Field(
        None,
        description="Minimum numeric value"
    )
    maximum: float | None = Field(
        None,
        description="Maximum numeric value"
    )
    enum: list[Any] | None = Field(
        None,
        description="Allowed values (not term-based)"
    )
```

### ValidationRule

Cross-field validation rules.

```python
class RuleType(str, Enum):
    CONDITIONAL_REQUIRED = "conditional_required"
    CONDITIONAL_VALUE = "conditional_value"
    MUTUAL_EXCLUSION = "mutual_exclusion"
    DEPENDENCY = "dependency"
    CUSTOM = "custom"


class Condition(BaseModel):
    """A condition for conditional rules."""

    field: str = Field(..., description="Field to check")
    operator: Literal["equals", "not_equals", "in", "not_in", "exists", "not_exists"]
    value: Any = Field(None, description="Value to compare (not needed for exists operators)")


class ValidationRule(BaseModel):
    """A cross-field validation rule."""

    type: RuleType
    description: str | None = None
    conditions: list[Condition] = Field(
        default_factory=list,
        description="Conditions that trigger the rule"
    )
    target_field: str | None = Field(
        None,
        description="Field affected by the rule"
    )
    target_fields: list[str] | None = Field(
        None,
        description="Fields affected (for mutual_exclusion)"
    )
    required: bool | None = Field(
        None,
        description="For conditional_required: is field required?"
    )
    allowed_values: list[Any] | None = Field(
        None,
        description="For conditional_value: allowed values"
    )
    error_message: str | None = Field(
        None,
        description="Custom error message"
    )
```

**Template Example:**
```json
{
  "id": "template-person",
  "name": "Person",
  "description": "Template for person records",
  "version": 3,
  "status": "active",
  "extends": null,
  "identity_fields": ["national_id"],
  "fields": [
    {
      "name": "first_name",
      "label": "First Name",
      "type": "string",
      "mandatory": true,
      "validation": {
        "min_length": 1,
        "max_length": 100
      }
    },
    {
      "name": "last_name",
      "label": "Last Name",
      "type": "string",
      "mandatory": true
    },
    {
      "name": "gender",
      "label": "Gender",
      "type": "term",
      "mandatory": false,
      "terminology_ref": "terminology-gender"
    },
    {
      "name": "birth_date",
      "label": "Date of Birth",
      "type": "date",
      "mandatory": true
    },
    {
      "name": "national_id",
      "label": "National ID",
      "type": "string",
      "mandatory": true,
      "validation": {
        "pattern": "^[A-Z0-9]{8,20}$"
      }
    },
    {
      "name": "country",
      "label": "Country",
      "type": "term",
      "terminology_ref": "terminology-country"
    },
    {
      "name": "tax_id",
      "label": "Tax ID",
      "type": "string",
      "mandatory": false
    },
    {
      "name": "addresses",
      "label": "Addresses",
      "type": "array",
      "array_item_type": "object",
      "array_item_template_ref": "template-address"
    }
  ],
  "rules": [
    {
      "type": "conditional_required",
      "description": "Tax ID required for German residents",
      "conditions": [
        {"field": "country", "operator": "equals", "value": "term-country-de"}
      ],
      "target_field": "tax_id",
      "required": true,
      "error_message": "Tax ID is required for German residents"
    }
  ],
  "created_at": "2024-01-15T10:00:00Z",
  "created_by": "architect"
}
```

---

## Document Store Models

### Document

The core data entity.

```python
class Document(BaseModel):
    """A validated document conforming to a template."""

    id: str = Field(
        ...,
        description="Unique document ID"
    )
    template_id: str = Field(
        ...,
        description="Template this document conforms to"
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
        description="Actual document content"
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: str
    updated_at: datetime | None = None
    updated_by: str | None = None
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="System metadata (not user data)"
    )
```

**Example:**
```json
{
  "id": "doc-550e8400-e29b-41d4-a716-446655440000",
  "template_id": "template-person",
  "template_version": 3,
  "identity_hash": "a1b2c3d4e5f6g7h8i9j0...",
  "version": 2,
  "status": "active",
  "data": {
    "first_name": "Alice",
    "last_name": "Schmidt",
    "gender": "term-gender-female",
    "birth_date": "1990-05-15",
    "national_id": "DE123456789",
    "country": "term-country-de",
    "tax_id": "12345678901",
    "addresses": [
      {
        "type": "term-address-type-home",
        "street": "Hauptstraße 1",
        "city": "Berlin",
        "postal_code": "10115",
        "country": "term-country-de"
      }
    ]
  },
  "created_at": "2024-01-15T10:00:00Z",
  "created_by": "user-123",
  "updated_at": "2024-02-20T14:30:00Z",
  "updated_by": "user-456",
  "metadata": {
    "source_system": "hr-import",
    "import_batch": "batch-2024-02-20"
  }
}
```

---

## Registry Models

### Namespace

A logical partition for IDs to prevent collisions across systems.

```python
class IdGeneratorConfig(BaseModel):
    """Configuration for ID generation within a namespace."""

    type: Literal["uuid4", "uuid7", "nanoid", "prefixed", "external", "custom"] = Field(
        default="uuid4",
        description="ID generation strategy"
    )
    prefix: str | None = Field(
        None,
        description="Prefix for prefixed generator (e.g., 'TERM-', 'TPL-')"
    )
    length: int | None = Field(
        None,
        description="Length for nanoid generator"
    )
    pattern: str | None = Field(
        None,
        description="Pattern for custom generator"
    )


class Namespace(BaseModel):
    """A logical partition in the Registry for ID isolation."""

    id: str = Field(
        ...,
        description="Unique namespace identifier",
        examples=["default", "vendor1", "wip-terminologies"]
    )
    name: str = Field(
        ...,
        description="Human-readable name"
    )
    description: str | None = Field(
        None,
        description="Purpose of this namespace"
    )
    id_generator: IdGeneratorConfig = Field(
        default_factory=IdGeneratorConfig,
        description="ID generation configuration for this namespace"
    )
    source_endpoint: str | None = Field(
        None,
        description="API endpoint for external namespaces"
    )
    api_key_hash: str | None = Field(
        None,
        description="Hashed API key for authentication"
    )
    status: Literal["active", "inactive"] = Field(
        default="active"
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)
```

**Example:**
```json
{
  "id": "vendor1",
  "name": "Vendor 1 Product Catalog",
  "description": "External vendor product identifiers",
  "id_generator": {
    "type": "external"
  },
  "source_endpoint": "https://vendor1.example.com/api",
  "api_key_hash": "argon2:...",
  "status": "active",
  "created_at": "2024-01-01T00:00:00Z",
  "metadata": {
    "contact": "vendor1-support@example.com"
  }
}
```

### Synonym

An alternative composite key that resolves to the same entity.

```python
class Synonym(BaseModel):
    """A composite key in a specific namespace that resolves to the parent entry."""

    namespace: str = Field(
        ...,
        description="Namespace this synonym belongs to"
    )
    composite_key_hash: str = Field(
        ...,
        description="Hash of the composite key"
    )
    composite_key_values: dict[str, Any] = Field(
        ...,
        description="The actual key values"
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: str = Field(
        ...,
        description="User or system that created this synonym"
    )
```

### RegistryEntry

An identity mapping in the registry with support for namespaces and synonyms.

```python
class RegistryEntry(BaseModel):
    """A registered identity in the central registry."""

    id: str = Field(
        ...,
        description="Primary ID in this namespace"
    )
    namespace: str = Field(
        ...,
        description="Namespace this entry belongs to"
    )
    is_preferred: bool = Field(
        default=True,
        description="Whether this is the preferred ID for the entity"
    )
    composite_key_hash: str = Field(
        ...,
        description="Hash of the composite key"
    )
    composite_key_values: dict[str, Any] = Field(
        ...,
        description="The actual key values"
    )
    synonyms: list[Synonym] = Field(
        default_factory=list,
        description="Alternative composite keys that resolve to this entry"
    )
    additional_ids: list[str] = Field(
        default_factory=list,
        description="Other IDs that are synonyms (from ID-as-synonym merges)"
    )
    source_system: str = Field(
        ...,
        description="System that registered this identity"
    )
    status: Literal["active", "inactive"] = Field(
        default="active"
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: str
    updated_at: datetime | None = None
    updated_by: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
```

**Example:**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "namespace": "default",
  "is_preferred": true,
  "composite_key_hash": "sha256:a1b2c3d4e5f6...",
  "composite_key_values": {
    "product_id": "PROD-001",
    "region": "EU"
  },
  "synonyms": [
    {
      "namespace": "vendor1",
      "composite_key_hash": "sha256:b2c3d4e5...",
      "composite_key_values": {"vendor_sku": "AB-123"},
      "created_at": "2024-01-20T10:00:00Z",
      "created_by": "import-service"
    },
    {
      "namespace": "vendor2",
      "composite_key_hash": "sha256:c3d4e5f6...",
      "composite_key_values": {"part_number": "CD-456", "revision": "2"},
      "created_at": "2024-01-25T14:00:00Z",
      "created_by": "admin"
    }
  ],
  "additional_ids": ["661f9511-e29b-41d4-a716-446655440001"],
  "source_system": "wip-main",
  "status": "active",
  "created_at": "2024-01-15T10:00:00Z",
  "created_by": "product-import",
  "updated_at": "2024-01-25T14:00:00Z",
  "updated_by": "admin"
}
```

### RegistryQueryResponse

Response format for registry lookups (always returns all IDs).

```python
class RegistryQueryResponse(BaseModel):
    """Response from a registry lookup - always includes all IDs."""

    preferred_id: str = Field(
        ...,
        description="The canonical/preferred ID for this entity"
    )
    preferred_namespace: str = Field(
        ...,
        description="Namespace of the preferred ID"
    )
    additional_ids: list[dict] = Field(
        default_factory=list,
        description="Other IDs that resolve to this entity"
    )
    composite_key_values: dict[str, Any] = Field(
        ...,
        description="Key values for the preferred entry"
    )
    synonyms: list[Synonym] = Field(
        default_factory=list,
        description="All synonyms across namespaces"
    )
    source_system: str
    status: str
```

**Example Response:**
```json
{
  "preferred_id": "550e8400-e29b-41d4-a716-446655440000",
  "preferred_namespace": "default",
  "additional_ids": [
    {"id": "661f9511-...", "namespace": "default"}
  ],
  "composite_key_values": {
    "product_id": "PROD-001",
    "region": "EU"
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

### WIP Internal Namespaces

The Registry pre-configures namespaces for WIP components:

```python
WIP_NAMESPACES = {
    "wip-terminologies": IdGeneratorConfig(type="prefixed", prefix="TERM-"),
    "wip-terms": IdGeneratorConfig(type="prefixed", prefix="T-"),
    "wip-templates": IdGeneratorConfig(type="prefixed", prefix="TPL-"),
    "wip-documents": IdGeneratorConfig(type="uuid7"),  # Time-sortable
}
```

---

## API Models

### Request/Response Models

```python
# Document creation request
class DocumentCreate(BaseModel):
    template_id: str
    data: dict[str, Any]


# Document response (includes system fields)
class DocumentResponse(BaseModel):
    id: str
    template_id: str
    template_version: int
    version: int
    status: str
    data: dict[str, Any]
    created_at: datetime
    updated_at: datetime | None


# Validation result
class ValidationResult(BaseModel):
    valid: bool
    identity_hash: str | None = None
    is_update: bool = False
    errors: list[ValidationError] = []
    warnings: list[ValidationWarning] = []


class ValidationError(BaseModel):
    field: str
    code: str
    message: str


class ValidationWarning(BaseModel):
    field: str
    code: str
    message: str


# Query request
class DocumentQuery(BaseModel):
    template_id: str | None = None
    filter: dict[str, Any] = {}
    sort: list[SortField] = []
    pagination: Pagination = Pagination()
    include_inactive: bool = False


class SortField(BaseModel):
    field: str
    order: Literal["asc", "desc"] = "asc"


class Pagination(BaseModel):
    offset: int = 0
    limit: int = 50


# Query response
class DocumentQueryResponse(BaseModel):
    documents: list[DocumentResponse]
    total: int
    offset: int
    limit: int
```

---

## Event Models

```python
class EventType(str, Enum):
    DOCUMENT_CREATED = "document.created"
    DOCUMENT_UPDATED = "document.updated"
    DOCUMENT_DEACTIVATED = "document.deactivated"
    TEMPLATE_CREATED = "template.created"
    TEMPLATE_UPDATED = "template.updated"
    TERMINOLOGY_CREATED = "terminology.created"
    TERMINOLOGY_UPDATED = "terminology.updated"


class Event(BaseModel):
    """An event published to the message queue."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    type: EventType
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    source: str = Field(..., description="System that generated the event")
    payload: dict[str, Any]


# Example event
{
    "id": "evt-123",
    "type": "document.created",
    "timestamp": "2024-01-15T10:00:00Z",
    "source": "wip-api",
    "payload": {
        "document_id": "doc-456",
        "template_id": "template-person",
        "identity_hash": "a1b2c3...",
        "version": 1
    }
}
```

---

## Identity Hash Algorithm

```python
import hashlib
import json


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
    # Sort fields
    sorted_fields = sorted(identity_fields)

    # Build normalized string
    parts = []
    for field in sorted_fields:
        value = data.get(field, "")
        # Handle nested fields with dot notation
        if "." in field:
            value = get_nested_value(data, field)
        # Normalize value to string
        if isinstance(value, dict):
            value = json.dumps(value, sort_keys=True)
        elif value is None:
            value = ""
        else:
            value = str(value)
        parts.append(f"{field}={value}")

    normalized = "|".join(parts)

    # Hash
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def get_nested_value(data: dict, path: str) -> Any:
    """Get a value from a nested dict using dot notation."""
    keys = path.split(".")
    value = data
    for key in keys:
        if isinstance(value, dict):
            value = value.get(key)
        else:
            return None
    return value
```

**Example:**
```python
data = {
    "first_name": "Alice",
    "last_name": "Schmidt",
    "national_id": "DE123456789"
}
identity_fields = ["national_id"]

# Result: sha256("national_id=DE123456789")
hash = compute_identity_hash(data, identity_fields)
# → "a1b2c3d4e5f6..."
```
