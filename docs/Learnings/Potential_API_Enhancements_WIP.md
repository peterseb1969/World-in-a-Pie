# Potential API Enhancements for WIP

## Context

These suggestions emerged from building a Clinical Study Plan application on top of WIP. The application uses 16 templates with inheritance, 19+ terminologies, and complex document reference graphs including cross-schedule anchors and predecessor chains. Each suggestion describes the current behavior, the friction it caused, and a concrete API enhancement that would have helped.

---

## 1. Inheritance-Aware Reference Validation

### Current Behavior
`target_templates: ["PLANNED_EVENT"]` only matches documents created with the exact `PLANNED_EVENT` template. Documents created with child templates (e.g., `PLANNED_COLLECTION extends PLANNED_EVENT`) are rejected.

### Friction
Any reference field in an inheritance hierarchy must explicitly list every possible subtype. For the Study Plan, `predecessor_event` required listing all 9 event template codes. Adding a new event subtype (e.g., `PLANNED_CONSENT`) requires updating `predecessor_event`'s `target_templates` on the base template AND re-propagating to all subtypes.

### Suggested Enhancement

**Option A — Wildcard syntax:**
```json
"target_templates": ["PLANNED_EVENT+"]
```
The `+` suffix means "this template and all templates that extend it." Validation traverses the inheritance chain.

**Option B — Automatic resolution:**
When `target_templates` includes a template that has children, automatically include all descendants. No syntax change needed — just a behavior change in the Document-Store's reference validator.

**Option C — `include_subtypes` flag:**
```json
{
  "name": "predecessor_event",
  "type": "reference",
  "target_templates": ["PLANNED_EVENT"],
  "include_subtypes": true
}
```

Option B is the most intuitive — if `PLANNED_COLLECTION` IS-A `PLANNED_EVENT`, then a reference accepting `PLANNED_EVENT` should accept `PLANNED_COLLECTION`. This matches how inheritance works in every type system.

### Impact
Eliminates the most painful design friction when combining inheritance and references. Removes the maintenance burden of updating target_templates lists when new subtypes are added.

---

## 2. Cascade Parent Template Updates to Children

### Current Behavior
Updating a parent template creates a new version (new `template_id`). Child templates continue extending the old version. Each child must be individually updated to extend the new parent.

### Friction
Updating `predecessor_event` on `PLANNED_EVENT` required:
1. Update `PLANNED_EVENT` → new version TPL-000018
2. Update `PLANNED_VISIT` to extend TPL-000018 → new version TPL-000027
3. Update `PLANNED_QUESTIONNAIRE` to extend TPL-000018 → new version TPL-000028
4. ... repeat for all 8 subtypes

That's 9 API calls for a single field change on the parent. And each subtype update had to carefully include ONLY child-specific fields to avoid breaking inheritance.

### Suggested Enhancement

**Option A — Cascade flag on parent update:**
```json
PUT /api/template-store/templates/{id}
{
  "fields": [...],
  "cascade_to_children": true
}
```
When `cascade_to_children` is true, WIP automatically creates new versions of all child templates extending the new parent.

**Option B — Lazy resolution:**
Store `extends` as a code (e.g., `"PLANNED_EVENT"`) rather than a template_id. Resolve to the latest version at document validation time, not at template creation time. This means parent updates automatically take effect for all children without any child updates.

**Option C — Explicit cascade endpoint:**
```
POST /api/template-store/templates/{id}/cascade
```
Creates new versions of all descendants extending the latest version of each ancestor.

Option B is the cleanest — it aligns with how `terminology_ref` works (you specify a code, not a terminology_id, and it always resolves to the current state).

---

## 3. Distinguish Inherited vs Own Fields in Template Response

### Current Behavior
`GET /templates/{id}` returns resolved fields (inherited + own) with no way to distinguish which are inherited and which are defined on the child template itself.

### Friction
When updating a child template, you must send ONLY the child's own fields. If you include inherited fields, they become overrides, breaking inheritance. But the API doesn't tell you which fields are which. The `?resolve=false` parameter is mentioned in docs but not in the OpenAPI spec and didn't work reliably.

### Suggested Enhancement

**Option A — Field-level `inherited` flag:**
```json
{
  "name": "study_id",
  "label": "Study ID",
  "type": "term",
  "inherited": true,
  "inherited_from": "TPL-000018"
}
```

**Option B — Separate sections in response:**
```json
{
  "template_id": "TPL-000040",
  "extends": "TPL-000018",
  "own_fields": [
    {"name": "processing_type", ...},
    {"name": "output_material", ...}
  ],
  "inherited_fields": [
    {"name": "study_id", ...},
    {"name": "predecessor_event", ...}
  ],
  "resolved_fields": [...]  // merged view
}
```

**Option C — Reliable `?resolve=false`:**
Ensure `GET /templates/{id}?resolve=false` returns only the child's own fields, exactly as they were submitted. Document this in the OpenAPI spec.

Option A is the lightest touch — one boolean flag per field. Easy to implement, doesn't change the response structure.

---

## 4. Terminology List Filtering by Code

### Current Behavior
`GET /api/def-store/terminologies?code=EVENT_TYPE` returns ALL terminologies regardless of the `code` parameter value. The parameter is accepted but silently ignored.

### Friction
A script that checked "does this terminology exist?" by filtering on code incorrectly concluded all 19 terminologies existed (it always found the first one). This caused all terms to be added to a single terminology, requiring manual cleanup.

### Suggested Enhancement
Implement proper filtering on the terminology list endpoint:
```
GET /api/def-store/terminologies?code=EVENT_TYPE        → exact match
GET /api/def-store/terminologies?code__contains=STUDY   → partial match
GET /api/def-store/terminologies?code__in=EVENT_TYPE,STUDY_PHASE → multiple
```

At minimum, `?code=` should do an exact match filter. The `GET /api/def-store/terminologies/by-code/{code}` endpoint exists and works correctly, but the list endpoint's filter parameter should work too — or be removed from the API to avoid confusion.

---

## 5. Template-Aware Document List Endpoint

### Current Behavior
`GET /api/document-store/documents` returns documents with `template_id` but no `template_code`. To understand what type of document you're looking at, you need a separate call to the Template-Store to build a `template_id → code` mapping.

### Friction
Every reporting or debugging script needs to first fetch all templates, build a mapping, then interpret documents. This is boilerplate that every consumer must write.

### Suggested Enhancement

**Option A — Include `template_code` in document responses:**
```json
{
  "document_id": "019c58a9-...",
  "template_id": "TPL-000040",
  "template_code": "PLANNED_PROCESSING",
  ...
}
```

**Option B — Filter documents by template code:**
```
GET /api/document-store/documents?template_code=PLANNED_VISIT
```
Currently only `template_id` filtering is available.

Option A is trivial to implement (the Document-Store already knows the template) and eliminates a cross-service call for every consumer.

---

## 6. Bulk Template Validation Endpoint

### Current Behavior
Template creation validates one template at a time. If you have a set of interdependent templates (like the 16 in the Study Plan), you discover reference errors one by one as you create them in sequence.

### Friction
The creation order matters (referenced templates must exist first), and circular references (SCHEDULE ↔ TIMEPOINT) require a create-then-update pattern. Discovering these constraints at creation time means trial and error.

### Suggested Enhancement

**Dry-run / validation endpoint for template sets:**
```
POST /api/template-store/templates/validate-set
{
  "templates": [
    {"code": "SCHEDULE", "fields": [...], "extends": null},
    {"code": "TIMEPOINT", "fields": [...], "extends": null},
    {"code": "PLANNED_EVENT", "fields": [...], "extends": null},
    {"code": "PLANNED_VISIT", "extends": "PLANNED_EVENT", "fields": [...]}
  ]
}
```

Response:
```json
{
  "valid": false,
  "issues": [
    {"template": "SCHEDULE", "field": "anchor_timepoint", "issue": "circular_reference",
     "detail": "SCHEDULE references TIMEPOINT which references SCHEDULE",
     "suggestion": "Create SCHEDULE first without anchor_timepoint, then update after TIMEPOINT exists"},
    {"template": "PLANNED_VISIT", "field": "predecessor_event", "issue": "inheritance_gap",
     "detail": "target_templates ['PLANNED_EVENT'] won't match subtypes like PLANNED_VISIT"}
  ],
  "suggested_creation_order": ["SPONSOR", "LAB_COMPANY", "LAB_SITE", "STUDY_DEFINITION", "SCHEDULE", ...]
}
```

This would catch design issues before any templates are created, and suggest the optimal creation order.

---

## 7. Terminology Reactivation Endpoint

### Current Behavior
`DELETE /api/def-store/terminologies/{id}` soft-deletes (deactivates) a terminology. There is no endpoint to reactivate it. The terminology's code remains reserved in the Registry, blocking creation of a new terminology with the same code.

### Friction
After accidentally corrupting `EVENT_TYPE` with wrong terms, deleting it left the code permanently reserved. Could not create a fresh `EVENT_TYPE`. Required the user to manually intervene via admin tools.

### Suggested Enhancement

```
POST /api/def-store/terminologies/{id}/restore
```

Restores a soft-deleted terminology to `active` status. Consistent with WIP's "never delete, only deactivate" philosophy — if deactivation is reversible in principle, the API should support reversal.

Alternatively, the create endpoint could offer a `force` parameter:
```json
POST /api/def-store/terminologies
{
  "code": "EVENT_TYPE",
  "name": "Event Type",
  "replace_inactive": true
}
```

---

## 8. Per-Study Terminology Validation on Template Fields

### Current Behavior
Template fields use a static `terminology_ref` that points to a fixed terminology code (e.g., `"terminology_ref": "EVENT_TYPE"`). This works for global terminologies but not for per-study terminologies where the correct terminology depends on a field value in the document (e.g., `study_id`).

### Friction
Per-study codes (`arm_code`, `timepoint_code`, `schedule_code`, `event_code`) cannot be validated at the WIP template level against their per-study terminologies. Validation must happen at the application layer, which is less reliable and requires custom code.

### Suggested Enhancement

**Dynamic terminology resolution:**
```json
{
  "name": "arm_code",
  "type": "term",
  "terminology_ref_pattern": "STUDY_{study_id}_ARMS"
}
```

The `terminology_ref_pattern` uses the value of another field in the same document to construct the terminology code at validation time. When validating a document with `study_id: "DEMO-001"`, the field would validate against `STUDY_DEMO-001_ARMS`.

This would bring per-study code validation into WIP's native validation layer, eliminating the need for application-layer validation of these fields.

---

## Summary

| # | Enhancement | Category | Impact |
|---|---|---|---|
| 1 | Inheritance-aware reference validation | Core behavior | Eliminates most painful friction with inheritance + references |
| 2 | Cascade parent updates to children | Template management | 9 API calls → 1 for parent field changes |
| 3 | Inherited vs own field distinction | Template API | Prevents accidental inheritance breakage during updates |
| 4 | Terminology list code filter | Def-Store API | Prevents data corruption from incorrect existence checks |
| 5 | Template code in document responses | Document API | Eliminates cross-service boilerplate for every consumer |
| 6 | Bulk template validation | Template API | Catches design issues before creation, suggests creation order |
| 7 | Terminology reactivation | Def-Store API | Completes the soft-delete lifecycle |
| 8 | Dynamic terminology ref | Template validation | Native per-study code validation instead of app-layer workarounds |

Enhancements 1, 2, and 3 form a coherent set around making inheritance a first-class citizen in WIP. If inheritance is a supported feature, its interaction with references, updates, and the API response format should be seamless.
