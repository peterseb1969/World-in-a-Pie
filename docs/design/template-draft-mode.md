# Design: Template Draft Mode

## Problem

When creating a set of interdependent templates (e.g., 16 templates with inheritance, cross-references, and circular dependencies), WIP validates references at creation time. This means:

- **Creation order matters** — referenced templates must exist before templates that reference them
- **Circular references require workarounds** — `SCHEDULE` references `TIMEPOINT` which references `SCHEDULE`; one must be created without the reference, then updated after the other exists
- **Errors are discovered one at a time** — you find out template 7 has an issue only after successfully creating templates 1-6

These constraints make complex template set design frustrating and error-prone.

## Proposed Solution: Draft Status

Add `"draft"` as a template status alongside `active`, `deprecated`, and `inactive`.

### Semantics

| Status | Cross-ref validation | Usable for documents | Editable |
|--------|---------------------|---------------------|----------|
| **draft** | Skipped | No | Yes |
| **active** | Required (validated on transition) | Yes | Yes (creates new version) |
| deprecated | N/A | Yes (existing only) | No |
| inactive | N/A | No | No |

### Lifecycle

```
                    ┌─────────────────────┐
    POST /templates │                     │
    (status=draft)  │       draft         │
    ─────────────►  │                     │
                    │  • No ref validation │
                    │  • Can edit freely   │
                    │  • Cannot create docs│
                    └──────────┬──────────┘
                               │
                POST /templates/{id}/activate
                  (validates all references)
                               │
                    ┌──────────▼──────────┐
                    │                     │
                    │       active        │
                    │                     │
                    │  • Fully validated   │
                    │  • Docs can use it   │
                    │  • Update = new ver  │
                    └─────────────────────┘
```

### API Changes

**Create template in draft mode:**
```json
POST /api/template-store/templates
{
  "code": "SCHEDULE",
  "name": "Study Schedule",
  "status": "draft",
  "fields": [
    {"name": "anchor_timepoint", "type": "reference", "reference_type": "document",
     "target_templates": ["TIMEPOINT"]}
  ]
}
```

WIP skips validation of `target_templates: ["TIMEPOINT"]` because status is draft. The TIMEPOINT template doesn't need to exist yet.

**Activate a template:**
```
POST /api/template-store/templates/{template_id}/activate
```

At activation time, WIP performs full validation:
- All `target_templates` must reference active templates
- All `terminology_ref` values must reference active terminologies
- `extends` must point to an active template
- All field type constraints are checked

If validation fails, the template stays in draft and the response contains the errors:
```json
{
  "status": "error",
  "errors": [
    {"field": "anchor_timepoint", "issue": "target_template 'TIMEPOINT' is not active"}
  ]
}
```

**Bulk activate (convenience):**
```
POST /api/template-store/templates/activate-batch
{
  "template_ids": ["TPL-000001", "TPL-000002", "..."]
}
```

Activates templates in dependency order. If template A references template B, B is activated first. Returns errors for any that fail.

### How This Solves Each Problem

**Creation order:** Irrelevant. Create all 16 templates as drafts in any order. Activate when ready.

**Circular references:** Both `SCHEDULE` and `TIMEPOINT` exist as drafts before either is activated. When activating, both templates can see each other. The activate-batch endpoint handles ordering.

**Error discovery:** All validation happens at activation time. You can inspect all draft templates before committing. Or use activate-batch to get all errors at once.

### What Skips Validation in Draft Mode

Only cross-entity reference validation is skipped:
- `target_templates` existence check — skipped
- `target_terminologies` existence check — skipped
- `terminology_ref` existence check — skipped
- `extends` existence check — skipped

Structural validation still applies in draft mode:
- Field types must be valid
- Field names must be unique
- Required properties (name, label, type) must be present
- Identity fields must reference defined field names

### Implementation Notes (Implemented)

**Template model (`template.py`):** Status field description includes "draft".

**API models (`api_models.py`):** `CreateTemplateRequest` has `status` field (None/"active"/"draft"). New `ActivateTemplateResponse` and `ActivationDetail` models. `ValidateTemplateResponse` has `will_also_activate` field.

**Template service (create):** If `request.status == "draft"`, skips: extends existence check, `_validate_field_references()`, cross-namespace validation, and NATS event publishing.

**Template service (activate):** Cascading activation via BFS:
1. Fetches template, verifies status is "draft"
2. `_build_activation_set()` — BFS through references, collecting all reachable draft templates (handles circular refs via visited set)
3. `_validate_activation_set()` — validates the full set as a unit (references valid if in set OR active)
4. If errors → returns errors, no state changes
5. If `dry_run` → returns preview
6. Sets `status="active"` on all, saves, publishes `TEMPLATE_ACTIVATED` events

**Template service (validate):** For draft templates, builds activation set and returns cascade preview via `will_also_activate`.

**NATS events (`nats_client.py`):** Added `TEMPLATE_ACTIVATED = "template.activated"` event type.

**Document-Store validation:** Already checks `template.status == "active"` — draft templates are automatically rejected for document creation.

**Bulk create:** Respects `status` from each request item, skips NATS events for drafts.

### Interaction with Existing Features

- **Template versioning:** Draft templates have version=1. Activating does not increment the version — the template_id stays the same. Only subsequent updates (after activation) create new versions.
- **Inheritance:** Draft templates can specify `extends`. The parent doesn't need to be active (or even exist) while the child is in draft. At activation, the parent must be active.
- **Reporting sync:** Draft templates with `reporting.sync_enabled = true` do not trigger PostgreSQL table creation until activated.
- **NATS events:** A `TEMPLATE_ACTIVATED` event is published when a template transitions from draft to active.

### Migration

No migration needed. Existing templates are all `active`. The `"draft"` status is additive.

### Alternatives Considered

**Bulk validation endpoint (`POST /templates/validate-set`):** Accepts a set of template definitions and returns validation issues + creation order. Rejected because:
- Still requires correct creation order at actual creation time
- Circular references still need workarounds
- Two-step process (validate then create) with no guarantee the environment doesn't change between steps
- Draft mode is simpler and solves the problem at the root

**Create-without-validate flag:** A `skip_validation=true` parameter on the create endpoint. Rejected because:
- Templates could be left in an invalid-but-active state
- Documents could be created against unvalidated templates
- No clear lifecycle for "when does validation happen?"
- Draft mode provides the same escape hatch with explicit status tracking
