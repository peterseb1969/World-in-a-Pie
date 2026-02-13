# Design: Template Reference Pinning

## Problem

Template reference fields (`target_templates`, `template_ref`, `array_template_ref`) store bare template **codes** (e.g., `"PERSON"`) — user-supplied strings that are not canonical identifiers. This violates WIP's core identity principle.

### WIP's Identity Model

The only valid ways to reference an entity in WIP are:

1. **Canonical ID** — Registry-issued, globally unique (e.g., `TPL-000001`)
2. **Composite key hash** — SHA-256 of the composite key, scoped to a namespace
3. **Registered synonym** — an alternative key registered in the Registry that resolves to a canonical ID

A bare template code like `"PERSON"` is none of these. It's an unqualified, user-supplied string used as a lookup hint. It has no namespace scoping, no hash, and no Registry-backed resolution.

### Current State

| Field | Currently Stores | Should Store |
|-------|-----------------|-------------|
| `extends` | `template_id` (TPL-000001) | Correct |
| `target_templates` | code ("PERSON") | `template_id` |
| `template_ref` | code ("PERSON") | `template_id` |
| `array_template_ref` | code ("PERSON") | `template_id` |

### Consequences

1. **No version pinning** — `target_templates: ["PERSON"]` silently accepts any version. If PERSON v2 adds or removes fields, reporting and downstream consumers break without warning.
2. **No pool scoping** — validation does `Template.find_one({"code": tpl_code})` without filtering by `pool_id`. In multi-pool deployments, this matches the wrong template.
3. **Inconsistency** — `extends` correctly stores a canonical ID while all other template references store codes.
4. **Bypasses the Registry** — the Registry exists to manage identity, but template references skip it entirely and rely on bare string matching.

## Proposed Solution: Always Store Canonical IDs

**All template reference fields store `template_id`s** — Registry-issued canonical identifiers. Always. The `version_strategy` field (already defined in `field.py`) controls how the stored ID is interpreted at document validation time.

### Storage — Always `template_id`

```
target_templates: ["TPL-000042", "TPL-000015"]    # not ["PERSON", "EMPLOYEE"]
template_ref: "TPL-000008"                         # not "ADDRESS"
array_template_ref: "TPL-000023"                   # not "CONTACT"
```

### Normalization at Template Creation

When a user provides a code (for convenience), the system resolves it to a `template_id` before storing:

1. User provides `"PERSON"` → system looks up latest active template with code PERSON in the pool → resolves to `TPL-000042` → stores `TPL-000042`
2. User provides `"TPL-000042"` → system validates it exists → stores `TPL-000042`

This happens in `create_template()` and `activate_template()`. **The stored value is always a canonical ID.**

### Version Strategy — Controls Resolution, Not Storage

```python
class VersionStrategy(str, Enum):
    LATEST = "latest"   # Accept any version of the same template family
    PINNED = "pinned"   # Accept only the exact stored template version
```

Default: `latest` (backward compatible).

**At document validation time:**

| Strategy | Stored | Resolution |
|----------|--------|-----------|
| `latest` | `TPL-000042` | Look up TPL-000042 → code "PERSON" → accept document if its template code is "PERSON" (any version) |
| `pinned` | `TPL-000042` | Accept document only if its `template_id` is exactly `TPL-000042` |

In both cases, what's stored is a canonical ID. The strategy determines how strictly it's matched.

### `extends` — No Change

Already stores `template_id`, always pinned. Correct as-is.

## API

### Template Creation

No changes to `CreateTemplateRequest`. The `version_strategy` field already exists on `FieldDefinition`. Users set it per-field:

```json
{
  "code": "EMPLOYEE_REVIEW",
  "fields": [
    {
      "name": "employee",
      "type": "reference",
      "reference_type": "document",
      "target_templates": ["EMPLOYEE"],
      "version_strategy": "pinned"
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

After normalization (assuming EMPLOYEE latest active is TPL-000015):

```json
{
  "fields": [
    {
      "name": "employee",
      "target_templates": ["TPL-000015"],
      "version_strategy": "pinned"
    },
    {
      "name": "department_head",
      "target_templates": ["TPL-000015"],
      "version_strategy": "latest"
    }
  ]
}
```

Both store `TPL-000015`. The `employee` field only accepts documents using exactly TPL-000015. The `department_head` field accepts documents using any version of the EMPLOYEE template family.

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

Stored:

```json
{
  "name": "address",
  "type": "object",
  "template_ref": "TPL-000008",
  "version_strategy": "pinned"
}
```

For `pinned`: validates nested object against exactly TPL-000008's schema.
For `latest`: validates against the latest active version of the ADDRESS family.

### `include_subtypes` Interaction

Works with both strategies. The stored IDs are always used as the starting point:

**`latest`:** `target_templates: ["TPL-000042"]` → look up TPL-000042's code (PERSON) → find all descendants by code → expand to `["PERSON", "EMPLOYEE", "CUSTOMER"]` (matched by code, any version).

**`pinned`:** `target_templates: ["TPL-000042"]` → find descendants of exactly TPL-000042 → expand to `["TPL-000042", "TPL-000043", "TPL-000044"]` (matched by exact template_id).

## Implementation

### Files to Modify

**Template-Store:**

| File | Changes |
|------|---------|
| `models/field.py` | Fix `template_ref` / `array_template_ref` descriptions |
| `services/template_service.py` | Add `_normalize_template_references()`, call in create/activate |

**Document-Store:**

| File | Changes |
|------|---------|
| `services/validation_service.py` | Pinned path in `_resolve_document_reference`, `_validate_object`, `_expand_target_templates` |

### Step 1: Fix Field Descriptions (template-store)

```python
# field.py
template_ref: Optional[str] = Field(
    None,
    description="Canonical template_id for nested template (resolved from code at creation)"
)
array_template_ref: Optional[str] = Field(
    None,
    description="Canonical template_id for array item template (resolved from code at creation)"
)
target_templates: Optional[list[str]] = Field(
    None,
    description="Canonical template_ids for allowed document reference targets (resolved from codes at creation)"
)
```

### Step 2: Normalize References at Template Creation (template-store)

New helper method in `TemplateService`:

```python
@staticmethod
async def _normalize_template_references(
    fields: list[FieldDefinition],
    pool_id: str
) -> None:
    """
    Normalize all template references in fields to canonical template_ids.

    Resolves codes to template_ids. Validates that template_ids exist.
    Mutates the field objects in-place.
    """
    for field in fields:
        # Normalize target_templates
        if field.target_templates:
            field.target_templates = [
                await TemplateService._resolve_to_template_id(ref, pool_id)
                for ref in field.target_templates
            ]

        # Normalize template_ref
        if field.template_ref:
            field.template_ref = await TemplateService._resolve_to_template_id(
                field.template_ref, pool_id
            )

        # Normalize array_template_ref
        if field.array_template_ref:
            field.array_template_ref = await TemplateService._resolve_to_template_id(
                field.array_template_ref, pool_id
            )


@staticmethod
async def _resolve_to_template_id(ref: str, pool_id: str) -> str:
    """
    Resolve a template reference to a canonical template_id.

    Accepts either a template_id (TPL-XXXXXX) or a code.
    Returns the canonical template_id.
    """
    # If it looks like a template_id, validate it exists
    if ref.startswith("TPL-"):
        template = await Template.find_one({"template_id": ref})
        if not template:
            raise ValueError(f"Template '{ref}' not found")
        return ref

    # It's a code — resolve to latest active template_id in the pool
    template = await Template.find_one(
        {"pool_id": pool_id, "code": ref, "status": "active"},
        sort=[("version", -1)]
    )
    if not template:
        raise ValueError(
            f"No active template with code '{ref}' found in pool '{pool_id}'"
        )
    return template.template_id
```

Called in `create_template()` (for non-draft), `activate_template()`, and `create_templates_bulk()`.

For **draft templates**: normalization is deferred to activation (referenced templates may not exist yet).

### Step 3: Document-Store — Pinned Resolution Path

In `_resolve_document_reference()`, the `version_strategy` is already available in the validation context. Change the template matching check:

```python
# Current (always matches by code):
if doc_template.get("code") not in target_templates:
    result.add_error(...)

# New (respects version_strategy):
if version_strategy == "pinned":
    # Exact template_id match
    if doc.template_id not in target_templates:
        result.add_error(
            code="invalid_reference_template",
            message=f"Referenced document uses template '{doc.template_id}', "
                    f"expected one of {target_templates} (pinned)",
            field=field_path
        )
        return None
else:
    # Latest: look up each stored template_id's code, match by code
    allowed_codes = set()
    for tpl_id in target_templates:
        tpl = await client.get_template(template_id=tpl_id)
        if tpl:
            allowed_codes.add(tpl.get("code"))
    if doc_template.get("code") not in allowed_codes:
        result.add_error(
            code="invalid_reference_template",
            message=f"Referenced document is '{doc_template.get('code')}', "
                    f"expected template family of {target_templates}",
            field=field_path
        )
        return None
```

Note: The `allowed_codes` lookup can be cached per validation run to avoid repeated calls.

### Step 4: Document-Store — Nested Object Resolution

In `_validate_object()`, `template_ref` is now always a `template_id`:

```python
template_ref = field.get("template_ref")
if template_ref:
    if field.get("version_strategy") == "pinned":
        # Use exactly this template version
        nested_template = await client.get_template_resolved(template_ref)
    else:
        # Latest: resolve to latest version of this family
        tpl = await client.get_template(template_id=template_ref)
        if tpl:
            nested_template = await client.get_template(
                template_code=tpl.get("code"),
                resolve_inheritance=True
            )
```

### Step 5: Document-Store — Expand Target Templates

`_expand_target_templates()` receives template_ids in both strategies. The expansion differs:

```python
async def _expand_target_templates(
    self,
    target_templates: list[str],
    cache: dict[str, list[str]],
    version_strategy: str = "latest"
) -> list[str]:
    expanded = set(target_templates)
    client = get_template_store_client()

    for tpl_id in target_templates:
        if tpl_id in cache:
            expanded.update(cache[tpl_id])
            continue

        try:
            descendants = await client.get_template_descendants(tpl_id)
            if version_strategy == "pinned":
                # Collect descendant template_ids (exact versions)
                desc_refs = [d.get("template_id") for d in descendants if d.get("template_id")]
            else:
                # Collect descendant codes (any version)
                desc_refs = [d.get("code") for d in descendants if d.get("code")]
            cache[tpl_id] = desc_refs
            expanded.update(desc_refs)
        except TemplateStoreError:
            cache[tpl_id] = []

    return list(expanded)
```

For `latest`, the expanded set is a mix of template_ids (the originals) and codes (the descendants). The matching logic in Step 3 already handles both: it resolves stored template_ids to codes before matching.

### Step 6: Pool Scoping in Template-Store Validation

Ensure all template lookups in `validate_template()` and `_validate_activation_set()` include `pool_id`:

```python
# Fix: always scope by pool
ref_tpl = await Template.find_one({"pool_id": pool_id, "code": tpl_code})
```

## Migration

### Backward Compatibility

Existing templates store codes in `target_templates`, `template_ref`, and `array_template_ref`. These continue to work because:

1. Default `version_strategy` is `latest` (or `None`, treated as `latest`)
2. Document-store validation currently checks by code → no change for existing templates
3. **No data migration required** — the document-store can detect whether a stored value is a template_id (starts with `TPL-`) or a legacy code, and handle both

### Gradual Migration

When existing templates are updated (creating a new version), the new version gets normalized references (template_ids). Old versions remain unchanged.

### Detection Logic

The document-store needs to handle both legacy codes and canonical IDs during the transition:

```python
def _is_template_id(ref: str) -> bool:
    """Check if a reference is a canonical template_id vs a legacy code."""
    return ref.startswith("TPL-")
```

For legacy codes, fall back to current behavior (match by code). For template_ids, use the new resolution logic.

## What Doesn't Change

- **`extends`** — already stores `template_id`, always pinned. Correct.
- **Document identity** — documents already pin to a specific `template_id`. Correct.
- **Term references** — `terminology_ref`, `target_terminologies` reference terminologies by code. Terminologies don't have multi-version semantics (updates are in-place), so code-based references are appropriate. If terminologies gain versioning, the same pattern should apply.
- **Registry** — no changes to the Registry service itself.
- **Reporting sync** — no changes. Template reporting config is version-specific.

## Verification

1. Create template with `target_templates: ["PERSON"]` → stored as `["TPL-000042"]` (resolved)
2. Create template with `target_templates: ["TPL-000042"]` → stored as `["TPL-000042"]` (already canonical)
3. `version_strategy: "pinned"` — document with PERSON v2 (TPL-000050) is rejected
4. `version_strategy: "latest"` (default) — document with PERSON v2 (TPL-000050) is accepted
5. `include_subtypes` with `pinned` → only descendants of exact TPL-000042
6. `include_subtypes` with `latest` → descendants of any version of PERSON
7. Existing templates with legacy codes → continue working (backward compatible)
8. Template update (new version) → new version has normalized template_ids
9. Draft template activation → references normalized to template_ids at activation time
