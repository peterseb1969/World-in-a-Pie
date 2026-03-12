# Design: Template & Terminology Reference Pinning

## Problem

Entity reference fields in templates store bare **values** — user-supplied strings that are not canonical identifiers. This violates WIP's core identity principle.

### WIP's Identity Model

The only valid ways to reference an entity in WIP are:

1. **Canonical ID** — Registry-issued, globally unique (e.g., `TPL-000001`, `TERM-000001`)
2. **Composite key hash** — SHA-256 of the composite key, scoped to a namespace
3. **Registered synonym** — an alternative key registered in the Registry that resolves to a canonical ID

A bare value like `"PERSON"` or `"DOC_STATUS"` is none of these. It's an unqualified, user-supplied string used as a lookup hint.

### Affected Fields

**Template references:**

| Field | Previously Stored | Now Stores |
|-------|------------------|------------|
| `extends` | `template_id` (TPL-XXXXXX) | Correct (no change) |
| `target_templates` | value ("PERSON") | `template_id` (TPL-XXXXXX) |
| `template_ref` | value ("ADDRESS") | `template_id` (TPL-XXXXXX) |
| `array_template_ref` | value ("CONTACT") | `template_id` (TPL-XXXXXX) |

**Terminology references:**

| Field | Previously Stored | Now Stores |
|-------|------------------|------------|
| `terminology_ref` | value ("DOC_STATUS") | `terminology_id` (TERM-XXXXXX) |
| `array_terminology_ref` | value ("COUNTRY") | `terminology_id` (TERM-XXXXXX) |
| `target_terminologies` | values (["GENDER"]) | `terminology_id`s (["TERM-XXXXXX"]) |

### Consequences (Now Fixed)

1. **No version pinning** — `target_templates: ["PERSON"]` silently accepted any version.
2. **No namespace scoping** — validation did `Template.find_one({"value": tpl_value})` without filtering by namespace.
3. **Inconsistency** — `extends` correctly stored a canonical ID while all other references stored values.
4. **Bypassed the Registry** — template references skipped the Registry entirely.
5. **Broken nested validation** — `get_template_resolved(template_ref)` received a value, returned `None`, silently skipping nested object validation.

## Solution: Always Store Canonical IDs

**All reference fields store canonical IDs** — `template_id` for templates, `terminology_id` for terminologies. The `version_strategy` field controls how stored IDs are interpreted at document validation time.

### Storage

```
target_templates: ["TPL-000042", "TPL-000015"]    # not ["PERSON", "EMPLOYEE"]
template_ref: "TPL-000008"                         # not "ADDRESS"
array_template_ref: "TPL-000023"                   # not "CONTACT"
terminology_ref: "TERM-000001"                     # not "DOC_STATUS"
array_terminology_ref: "TERM-000005"               # not "COUNTRY"
target_terminologies: ["TERM-000003"]              # not ["GENDER"]
```

### Normalization at Template Creation

When a user provides a value (for convenience), the system resolves it to a canonical ID before storing:

1. User provides `"PERSON"` → system looks up latest active template with value PERSON in the pool → resolves to `TPL-000042` → stores `TPL-000042`
2. User provides `"TPL-000042"` → system validates it exists → stores `TPL-000042`
3. User provides `"DOC_STATUS"` → system looks up terminology → resolves to `TERM-000001` → stores `TERM-000001`

This happens in `create_template()`, `activate_template()`, and `create_templates_bulk()`.

For **draft templates**: normalization is deferred to activation (referenced entities may not exist yet).

### Version Strategy — Controls Resolution, Not Storage

```python
class VersionStrategy(str, Enum):
    LATEST = "latest"   # Accept any version of the same template family
    PINNED = "pinned"   # Accept only the exact stored template version
```

Default: `latest`.

**At document validation time:**

| Strategy | Stored | Resolution |
|----------|--------|-----------|
| `latest` | `TPL-000042` | Look up TPL-000042 → value "PERSON" → accept document if its template value is "PERSON" (any version) |
| `pinned` | `TPL-000042` | Accept document only if its `template_id` is exactly `TPL-000042` |

### `extends` — No Change

Already stores `template_id`, always pinned. Correct as-is.

## API

### Template Creation

No changes to `CreateTemplateRequest`. Users can provide values or IDs — normalization is transparent:

```json
{
  "value": "EMPLOYEE_REVIEW",
  "fields": [
    {
      "name": "employee",
      "type": "reference",
      "reference_type": "document",
      "target_templates": ["EMPLOYEE"],
      "version_strategy": "pinned"
    },
    {
      "name": "status",
      "type": "term",
      "terminology_ref": "DOC_STATUS"
    },
    {
      "name": "department_head",
      "type": "reference",
      "reference_type": "document",
      "target_templates": ["EMPLOYEE"]
    }
  ]
}
```

After normalization:

```json
{
  "fields": [
    {
      "name": "employee",
      "target_templates": ["TPL-000015"],
      "version_strategy": "pinned"
    },
    {
      "name": "status",
      "terminology_ref": "TERM-000001"
    },
    {
      "name": "department_head",
      "target_templates": ["TPL-000015"],
      "version_strategy": "latest"
    }
  ]
}
```

### Nested Object Fields

Same pattern. `template_ref` and `array_template_ref` are always normalized to `template_id`:

```json
{
  "name": "address",
  "type": "object",
  "template_ref": "ADDRESS",
  "version_strategy": "pinned"
}
```

Stored as `"template_ref": "TPL-000008"`.

For `pinned`: validates nested object against exactly TPL-000008's schema.
For `latest`: validates against the latest active version of the ADDRESS family.

### `include_subtypes` Interaction

Works with both strategies. The stored IDs are always used as the starting point:

**`latest`:** `target_templates: ["TPL-000042"]` → look up TPL-000042's value (PERSON) → find all descendants by value → expand to `["PERSON", "EMPLOYEE", "CUSTOMER"]` (matched by value, any version).

**`pinned`:** `target_templates: ["TPL-000042"]` → find descendants of exactly TPL-000042 → expand to `["TPL-000042", "TPL-000043", "TPL-000044"]` (matched by exact template_id).

## Implementation

### Files Modified

**Template-Store (`components/template-store/src/template_store/`):**

| File | Changes |
|------|---------|
| `models/field.py` | Updated descriptions for all 6 reference fields |
| `services/template_service.py` | Added `_resolve_to_template_id()`, `_resolve_to_terminology_id()`, `_normalize_field_references()`. Called in `create_template()`, `activate_template()`, `create_templates_bulk()`. Fixed pool scoping in `_validate_field_references()`. |

**Document-Store (`components/document-store/src/document_store/`):**

| File | Changes |
|------|---------|
| `services/validation_service.py` | Updated `_resolve_document_reference()` (version_strategy-aware matching), `_validate_object()` and `_validate_array()` (version_strategy for nested templates), `_expand_target_templates()` (pinned vs latest expansion), `_lookup_by_business_key()` (ID-based lookup) |

### Key Methods

**`_resolve_to_template_id(ref, namespace, known_templates=None)`** — Resolves a template value or ID to a canonical `template_id`. If `ref` is a UUID7, validates existence. Otherwise looks up latest active template by value in the namespace. `known_templates` enables cross-resolution within an activation set.

**`_resolve_to_terminology_id(ref, namespace)`** — Resolves a terminology value or ID to a canonical `terminology_id`. If `ref` is a UUID7, validates existence and active status. Otherwise looks up by value via def-store.

**`_normalize_field_references(fields, namespace, known_templates=None)`** — Iterates all fields and normalizes the 6 reference fields to canonical IDs. Mutates fields in-place.

## What Doesn't Change

- **`extends`** — already stores `template_id`, always pinned. Correct.
- **Document identity** — documents already pin to a specific `template_id`. Correct.
- **Registry** — no changes to the Registry service itself.
- **Reporting sync** — no changes. Template reporting config is version-specific.
- **Def-Store service** — no changes (clients already support both ID and code lookup).
- **API request models** — `CreateTemplateRequest` still accepts values as input; normalization is transparent.

## Verification

1. Create template with `target_templates: ["PERSON"]` → stored as canonical IDs
2. Create template with `terminology_ref: "DOC_STATUS"` → stored as canonical ID
3. Create template with `target_terminologies: ["GENDER"]` → stored as canonical IDs
4. Create template with already-canonical IDs → stored as-is
5. `version_strategy: "pinned"` → document with different template version rejected
6. `version_strategy: "latest"` (default) → document with any family version accepted
7. `include_subtypes` with `pinned` → descendants matched by exact template_id
8. `include_subtypes` with `latest` → descendants matched by value family
9. Nested object validation via `template_ref` → works (was silently broken before)
10. Draft activation → references normalized to canonical IDs at activation time
11. Bulk create (non-draft) → references normalized
12. Term field with terminology_id → validates correctly via def-store
