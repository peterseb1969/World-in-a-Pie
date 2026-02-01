# Design: Reference Fields

## Status

**Draft** - Pending review

## Overview

This document describes a unified reference system for WIP that allows documents to explicitly reference other entities (documents, terms, terminologies, templates) with validation, resolution, and version control.

## Motivation

The current system has limited reference capabilities:

| Reference Type | Current Support | Limitation |
|----------------|-----------------|------------|
| Document → Term | ✅ `type: "term"` | Works well |
| Document → Document | ❌ None | **Major gap** - uses unvalidated strings |
| Document → Terminology | ❌ None | Cannot store "which terminology to use" |
| Document → Template | ❌ None | Cannot store "which template to use" |

### The Problem

In the Study Plan use case, we have fields like:

```json
{
  "name": "study_id",
  "type": "string",
  "description": "Parent study identifier"
}
```

This is just a string. There's no validation that the study exists, no referential integrity, no way to navigate from a `STUDY_ARM` to its parent `STUDY_DEFINITION`.

### The Solution

A new `reference` field type that:
- Validates references at document creation/update
- Stores resolved reference information
- Supports version strategies (latest vs pinned)
- Enables referential integrity checks
- Allows multiple valid target types (polymorphic references)

---

## Design

### Field Type: `reference`

A new field type that can reference any WIP entity.

#### Field Definition Schema

```json
{
  "name": "field_name",
  "type": "reference",
  "reference_type": "document",
  "target_templates": ["TEMPLATE_CODE_1", "TEMPLATE_CODE_2"],
  "version_strategy": "latest",
  "required": true,
  "description": "Reference to parent entity"
}
```

#### Reference Types

| `reference_type` | Target | `target_*` Field | Use Case |
|------------------|--------|------------------|----------|
| `document` | Document | `target_templates` | STUDY_ARM → STUDY_DEFINITION |
| `term` | Term | `target_terminologies` | Person → GENDER term |
| `terminology` | Terminology | — | Config storing "which terminology to use" |
| `template` | Template | — | Config storing "which template to use" |

#### Version Strategies

| Strategy | Behavior | Storage | Use Case |
|----------|----------|---------|----------|
| `latest` | Always resolves to current active version | identity_hash (documents) or code (others) | Normal references - "the current study" |
| `pinned` | Locked to specific version at creation time | document_id / term_id / template_id | Audit compliance - "the study as it was when signed" |

**Default:** `latest`

**Internal Storage:**

Regardless of input format, references are always stored as:
- `latest`: identity_hash (for documents) or code (for terms/terminologies/templates)
- `pinned`: document_id / term_id / terminology_id / template_id

This ensures efficient resolution without re-parsing business keys.

#### Field Definition Examples

**Document Reference (single target):**
```json
{
  "name": "study",
  "type": "reference",
  "reference_type": "document",
  "target_templates": ["STUDY_DEFINITION"],
  "version_strategy": "latest",
  "required": true
}
```

**Document Reference (multiple targets - polymorphic):**
```json
{
  "name": "parent_entity",
  "type": "reference",
  "reference_type": "document",
  "target_templates": ["STUDY_DEFINITION", "STUDY_ARM", "STUDY_TIMEPOINT"],
  "version_strategy": "latest"
}
```

**Term Reference (replaces current `type: "term"`):**
```json
{
  "name": "gender",
  "type": "reference",
  "reference_type": "term",
  "target_terminologies": ["GENDER"],
  "version_strategy": "latest"
}
```

**Term Reference (multiple terminologies):**
```json
{
  "name": "classification",
  "type": "reference",
  "reference_type": "term",
  "target_terminologies": ["CATEGORY_A", "CATEGORY_B"],
  "version_strategy": "latest"
}
```

**Terminology Reference:**
```json
{
  "name": "allowed_values_source",
  "type": "reference",
  "reference_type": "terminology",
  "version_strategy": "latest"
}
```

**Template Reference:**
```json
{
  "name": "child_template",
  "type": "reference",
  "reference_type": "template",
  "version_strategy": "pinned"
}
```

---

### Storage Model

References are stored in a dedicated `references` field (replacing/extending `term_references`):

```json
{
  "document_id": "0192abc...",
  "template_id": "TPL-000001",
  "template_code": "STUDY_ARM",
  "data": {
    "study": "DEMO-001",
    "arm_code": "TREATMENT",
    "gender": "male"
  },
  "references": {
    "study": {
      "reference_type": "document",
      "lookup_value": "DEMO-001",
      "resolved": {
        "document_id": "0192def...",
        "identity_hash": "abc123...",
        "template_code": "STUDY_DEFINITION",
        "version": 3
      },
      "version_strategy": "latest",
      "resolved_at": "2024-01-30T10:00:00Z"
    },
    "gender": {
      "reference_type": "term",
      "lookup_value": "male",
      "resolved": {
        "term_id": "T-000042",
        "terminology_code": "GENDER",
        "matched_via": "value"
      },
      "version_strategy": "latest",
      "resolved_at": "2024-01-30T10:00:00Z"
    }
  }
}
```

#### Storage Fields

| Field | Description |
|-------|-------------|
| `reference_type` | Type of reference (document, term, terminology, template) |
| `lookup_value` | Original value provided by user |
| `resolved` | Resolution details (varies by reference type) |
| `version_strategy` | Strategy used (latest/pinned) |
| `resolved_at` | Timestamp of resolution |

#### Resolved Object by Reference Type

**Document:**
```json
{
  "document_id": "0192def...",
  "identity_hash": "abc123...",
  "template_code": "STUDY_DEFINITION",
  "version": 3
}
```

**Term:**
```json
{
  "term_id": "T-000042",
  "terminology_code": "GENDER",
  "matched_via": "value"
}
```

**Terminology:**
```json
{
  "terminology_id": "TERM-000001",
  "terminology_code": "GENDER",
  "version": 1
}
```

**Template:**
```json
{
  "template_id": "TPL-000001",
  "template_code": "STUDY_DEFINITION",
  "version": 3
}
```

---

### Validation Behavior

#### On Document Create/Update

1. **Parse field definition** - Get reference_type, targets, version_strategy
2. **Lookup target entity** by the provided value:
   - Document: Find by identity field value (e.g., `study_id = "DEMO-001"`)
   - Term: Find by code, value, or alias
   - Terminology: Find by code
   - Template: Find by code
3. **Validate target type** - Check against `target_templates` or `target_terminologies`
4. **Check target status** - Warn if inactive/deprecated (don't fail)
5. **Store resolution** - Save resolved IDs and metadata

#### Validation Errors

| Condition | Behavior |
|-----------|----------|
| Target not found | Error: "Referenced {type} '{value}' not found" |
| Target wrong type | Error: "Referenced document is {actual}, expected one of {targets}" |
| Target inactive | Warning (logged): "Referenced {type} '{value}' is inactive" |
| Target deprecated | Warning (logged): "Referenced term '{value}' is deprecated" |

#### Input Formats

WIP accepts multiple input formats for reference values. The system auto-detects the format:

| Format | Detection | Resolution | Use Case |
|--------|-----------|------------|----------|
| Document ID | UUID7 pattern | Direct lookup by document_id | UI dropdowns, system integrations |
| Identity Hash | Prefix `hash:` | Lookup by identity_hash | System integrations, version-independent |
| Business Key | Anything else | Resolve via identity fields | API imports, human-entered data |

**Examples:**

```json
{
  "data": {
    "study": "0192abc-def0-7123-...",     // Document ID - direct lookup
    "department": "hash:abc123def456...",  // Identity hash - hash lookup
    "category": "DEMO-001"                 // Business key - resolve via identity fields
  }
}
```

**Typical usage patterns:**

- **UI (dropdowns/search):** Returns document_id or identity_hash → most common
- **API imports:** Uses business keys → resolved to IDs
- **System integrations:** May use any format depending on source

#### Lookup Behavior by Reference Type

**Document References:**

1. **Document ID** (UUID7 format): Direct lookup, return document
2. **Identity Hash** (prefixed `hash:`): Find active document with matching identity_hash
3. **Business Key**: Match against the target template's identity fields

**Single identity field example:**

`STUDY_DEFINITION` has `identity_fields: ["study_id"]`
- Input: `"DEMO-001"`
- Lookup: Find active document where `data.study_id = "DEMO-001"`

**Composite identity field example:**

`ORDER_LINE` has `identity_fields: ["order_id", "line_number"]`
- Input: `{"order_id": "ORD-001", "line_number": 1}` (object format)
- Lookup: Find active document where both fields match

**Term References:**

1. **Term ID** (T-XXXXXX format): Direct lookup
2. **Business Key**: Match by code, value, or alias (current behavior)

**Terminology References:**

1. **Terminology ID** (TERM-XXXXXX format): Direct lookup
2. **Code**: Match by terminology code

**Template References:**

1. **Template ID** (TPL-XXXXXX format): Direct lookup
2. **Code**: Match by template code (returns latest version unless pinned)

---

### Resolution Behavior

#### On Document Read

**Strategy: `latest`**

Re-resolve the reference to get current state:

```
identity_hash → find current active document → return current document_id
```

The `resolved` object in storage may be stale, but we re-resolve on read.

**Strategy: `pinned`**

Return the stored `document_id` directly. The referenced version may be inactive, but that's intentional.

#### Resolution API Response

When fetching a document, references can be:

1. **Inline resolved** (default) - Include resolved entity info
2. **Expanded** - Include full referenced entity (optional, via query param)

```
GET /documents/{id}?expand_references=true
```

**Default response:**
```json
{
  "document_id": "...",
  "data": {
    "study": "DEMO-001"
  },
  "references": {
    "study": {
      "reference_type": "document",
      "lookup_value": "DEMO-001",
      "resolved": {
        "document_id": "0192def...",
        "template_code": "STUDY_DEFINITION"
      }
    }
  }
}
```

**Expanded response:**
```json
{
  "document_id": "...",
  "data": {
    "study": "DEMO-001"
  },
  "references": {
    "study": {
      "reference_type": "document",
      "lookup_value": "DEMO-001",
      "resolved": {
        "document_id": "0192def...",
        "template_code": "STUDY_DEFINITION"
      },
      "expanded": {
        "document_id": "0192def...",
        "data": {
          "study_id": "DEMO-001",
          "name": "WIP-101 Phase 2 Study",
          "phase": "Phase 2"
        }
      }
    }
  }
}
```

---

### Arrays of References

References work naturally in arrays:

**Field Definition:**
```json
{
  "name": "related_studies",
  "type": "array",
  "items": {
    "type": "reference",
    "reference_type": "document",
    "target_templates": ["STUDY_DEFINITION"]
  }
}
```

**Document Data:**
```json
{
  "data": {
    "related_studies": ["DEMO-001", "DEMO-002", "DEMO-003"]
  },
  "references": {
    "related_studies": [
      {
        "reference_type": "document",
        "lookup_value": "DEMO-001",
        "resolved": { "document_id": "...", ... }
      },
      {
        "reference_type": "document",
        "lookup_value": "DEMO-002",
        "resolved": { "document_id": "...", ... }
      }
    ]
  }
}
```

---

### Referential Integrity

#### Deactivation Behavior

When a referenced entity is deactivated:

1. **Warn** - Log warning to audit trail
2. **Continue** - Don't block the deactivation
3. **Mark stale** - Referencing documents can be identified via health check

This matches the user's preference: "Warn and log in audit trail"

#### Health Check Enhancements

The existing `/health/integrity` endpoint gains new issue types:

| Issue Type | Severity | Description |
|------------|----------|-------------|
| `orphaned_document_ref` | error | Referenced document not found |
| `inactive_document_ref` | warning | Referenced document is inactive |
| `stale_document_ref` | info | Pinned reference has newer version available |
| `orphaned_term_ref` | error | Referenced term not found |
| `inactive_term_ref` | warning | Referenced term is deprecated |

#### Reverse Lookup

New endpoint to find what references a given entity:

```
GET /documents/{id}/referenced-by
GET /terminologies/{id}/referenced-by
GET /templates/{id}/referenced-by
```

Response:
```json
{
  "referenced_by": [
    {
      "document_id": "0192abc...",
      "template_code": "STUDY_ARM",
      "field": "study",
      "reference_type": "document"
    }
  ],
  "count": 15
}
```

---

### Migration: Existing `term` Type

The current `type: "term"` with `terminology_ref` should be migrated to the new system.

#### Approach: Backward Compatibility

1. **Keep supporting** `type: "term"` in field definitions
2. **Internally convert** to reference type during validation
3. **Store using new format** (`references` instead of `term_references`)
4. **API compatibility** - Continue returning `term_references` for backward compat

#### Migration Path

**Phase 1:** New reference system alongside existing term type
**Phase 2:** Migrate existing documents (background job)
**Phase 3:** Deprecate `term_references` field in API responses
**Phase 4:** Remove old code paths

---

### API Changes

#### Template Store

**Field Definition Validation:**
- Accept `type: "reference"` with required `reference_type`
- Validate `target_templates` exist (for document refs)
- Validate `target_terminologies` exist (for term refs)

#### Document Store

**Document Validation:**
- Resolve all reference fields
- Store in `references` object
- Return validation errors for unresolved references

**Document Response:**
- Include `references` object
- Support `?expand_references=true` query param

**New Endpoints:**
```
GET /documents/{id}/referenced-by
```

#### Def-Store

**New Endpoint:**
```
GET /terminologies/{id}/referenced-by
GET /terms/{id}/referenced-by
```

---

### Implementation Phases

#### Phase 1: Core Infrastructure

1. Update Template Store field validation for `type: "reference"`
2. Add `references` storage to Document model
3. Implement document reference resolution in Document Store
4. Update document validation pipeline

#### Phase 2: Term Reference Migration

1. Support both `type: "term"` and `type: "reference"` with `reference_type: "term"`
2. Store term references in new `references` format
3. Maintain `term_references` in API for backward compatibility

#### Phase 3: Referential Integrity

1. Add `referenced-by` endpoints
2. Enhance health check with new issue types
3. Add deactivation warnings with audit logging

#### Phase 4: Advanced Features

1. Reference expansion (`?expand_references=true`)
2. Terminology and template reference types
3. Composite identity lookup for document references

---

### Resolved Questions

1. **Composite identity lookup format** - ✅ RESOLVED
   - Accept document_id, identity_hash, OR business keys
   - For composite business keys, use object format: `{"order_id": "ORD-001", "line_number": 1}`
   - UI typically provides document_id via dropdowns/search (most common case)

2. **Deactivation behavior** - ✅ RESOLVED
   - Warn and log to audit trail
   - Don't block deactivation
   - Health check identifies stale references

### Open Questions

1. **Cross-reference cycles** - Should we detect/prevent circular references?
   - A → B → C → A
   - Recommendation: Detect and warn, don't prevent (cycles may be valid in some models)

2. **Cascading resolution** - When expanding references, how deep?
   - Default: 1 level
   - Optional: `?expand_depth=2`
   - Recommendation: Start with 1 level only, add depth parameter later if needed

3. **Index optimization** - Which fields need indexes for efficient reverse lookups?
   - `references.*.resolved.document_id`
   - `references.*.resolved.term_id`
   - `references.*.resolved.identity_hash`

---

### Example: Updated Study Plan

With the new reference system, the Study Plan templates would be updated:

**STUDY_ARM (before):**
```json
{
  "name": "study_id",
  "type": "string",
  "required": true,
  "description": "Parent study identifier"
}
```

**STUDY_ARM (after):**
```json
{
  "name": "study",
  "type": "reference",
  "reference_type": "document",
  "target_templates": ["STUDY_DEFINITION"],
  "version_strategy": "latest",
  "required": true,
  "description": "Parent study"
}
```

**STUDY_TIMEPOINT (after):**
```json
{
  "name": "study",
  "type": "reference",
  "reference_type": "document",
  "target_templates": ["STUDY_DEFINITION"],
  "version_strategy": "latest",
  "required": true
},
{
  "name": "phase",
  "type": "reference",
  "reference_type": "term",
  "target_terminologies": ["STUDY_PHASE"],
  "version_strategy": "latest"
}
```

**STUDY_PLANNED_EVENT (after):**
```json
{
  "name": "study",
  "type": "reference",
  "reference_type": "document",
  "target_templates": ["STUDY_DEFINITION"],
  "required": true
},
{
  "name": "timepoint",
  "type": "reference",
  "reference_type": "document",
  "target_templates": ["STUDY_TIMEPOINT"],
  "required": true
},
{
  "name": "event_type",
  "type": "reference",
  "reference_type": "term",
  "target_terminologies": ["EVENT_TYPE"],
  "required": true
}
```

This makes the relationships explicit, validated, and navigable.
