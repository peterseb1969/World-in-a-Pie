# Incomplete WIP Documentation: Learnings

## Context

These issues were encountered while building a Clinical Study Plan application on top of WIP, following the `AI-Assisted-Development.md` process. An AI assistant (Claude) consumed the WIP APIs to create 19 terminologies, 16 templates (with inheritance), and 43 documents including cross-document references, predecessor chains, and cross-schedule anchors.

---

## 1. Term `label` Field is Required but Not Documented

**What happened:** Bulk term creation via `POST /api/def-store/terminologies/{id}/terms/bulk` failed with a validation error: `"Field required"` for `label` on every term.

**What the docs say:** The `AI-Assisted-Development.md` examples show terms with `code`, `value`, and `aliases` — no `label` field. The phrase "Every field needs a `label`" appears in the template section but not in the terminology/term section.

**What was missing:** The term creation API requires `label` as a mandatory field alongside `code` and `value`. This is not documented in:
- The `AI-Assisted-Development.md` examples (all term creation examples omit `label`)
- The inline examples in the End-to-End walkthrough section

**Impact:** First API call failed. Required inspecting the error response and adding `label` to all term definitions.

**Suggested fix:** Add `label` to all term creation examples in `AI-Assisted-Development.md`. Add a note in the terminology section: "Every term requires `code`, `value`, and `label` fields. The `label` is the human-readable display name."

---

## 2. Terminology List `code` Filter Does Not Filter

**What happened:** `GET /api/def-store/terminologies?code=STUDY_PHASE` returned ALL terminologies instead of filtering by the `code` parameter. This caused a script to incorrectly conclude that all 19 terminologies already existed (it found `EVENT_TYPE` for every query) and dumped 90+ wrong terms into a single terminology.

**What the docs say:** The `AI-Assisted-Development.md` examples use `?code=<CODE>` as a filter:
```bash
curl -s -H "X-API-Key: <key>" \
  "http://<hostname>:8002/api/def-store/terminologies?code=<CODE>" | jq .
```

**What was missing:** Either:
- The `code` query parameter is not implemented as a filter (documentation is wrong), or
- The parameter name is different (e.g., `code__eq` or `search`)

The actual behavior (no filtering) contradicts the documented usage pattern. There is no warning that the parameter is ignored.

**Impact:** Catastrophic data corruption in a dev environment — 90 terms from unrelated terminologies dumped into `EVENT_TYPE`. Required manual cleanup (user had to delete the terminology and all its values) before the correct data could be created.

**Suggested fix:** Either implement the `code` filter on the terminology list endpoint, or remove the misleading example from `AI-Assisted-Development.md` and document the correct way to check if a terminology exists (e.g., use `GET /api/def-store/terminologies/by-code/{code}` instead).

---

## 3. Template Inheritance and Reference Validation Interaction Not Documented

**What happened:** `PLANNED_PROCESSING` extends `PLANNED_EVENT`. The `predecessor_event` field on `PLANNED_EVENT` had `target_templates: ["PLANNED_EVENT"]`. When creating a `PLANNED_PROCESSING` document with a `predecessor_event` pointing to a `PLANNED_COLLECTION` document (which also extends `PLANNED_EVENT`), WIP rejected it:

```
"Referenced document is 'PLANNED_COLLECTION', expected one of ['PLANNED_EVENT']"
```

**What the docs say:** The `AI-Assisted-Development.md` documents polymorphic references:
```json
"target_templates": ["STUDY_DEFINITION", "STUDY_ARM", "STUDY_TIMEPOINT"]
```

The `components.md` documents template inheritance rules. Neither document mentions how these two features interact.

**What was missing:** A clear statement that **reference validation is template-exact, not inheritance-aware**. A document created with template `PLANNED_COLLECTION` is NOT considered a valid match for `target_templates: ["PLANNED_EVENT"]`, even though `PLANNED_COLLECTION` extends `PLANNED_EVENT`.

This is a fundamental behavior that affects any design using both inheritance and references — which is a very natural combination.

**Impact:** Required redesigning the `predecessor_event` field to be a polymorphic reference listing all 9 event template codes. Required updating the base template, then updating all 8 subtypes to re-inherit the fixed field. Multiple rounds of debugging and template versioning.

**Suggested fix:** Add a section to `components.md` under Template Inheritance:

> **Important: Inheritance does not affect reference validation.** When a reference field specifies `target_templates: ["PARENT"]`, only documents created with the exact `PARENT` template are accepted. Documents created with child templates that extend `PARENT` are NOT automatically accepted. To accept documents from a parent and all its children, you must explicitly list all template codes in `target_templates`.

Consider also documenting the polymorphic reference pattern as the recommended approach when using inheritance:
```json
"target_templates": ["PLANNED_EVENT", "PLANNED_VISIT", "PLANNED_COLLECTION", "...all subtypes"]
```

---

## 4. Template Update (`PUT`) Creates New Version — Inheritance Implications Not Documented

**What happened:** After updating `PLANNED_EVENT` (creating TPL-000018 from TPL-000009), all 8 subtypes still pointed to the old version via their `extends` field (which stores a `template_id`, not a code). The subtypes needed to be individually updated to extend the new parent version.

**What the docs say:** The `AI-Assisted-Development.md` mentions: "When a template is updated, WIP creates a new template_id with an incremented version." The `components.md` says `extends` takes a template code when creating.

**What was missing:**
- The `extends` field is stored as a **template_id** (e.g., `TPL-000009`), not as a code (e.g., `PLANNED_EVENT`), even though creation accepts a code.
- When the parent template is updated (new version), child templates do NOT automatically follow. They continue extending the old parent version.
- To propagate a parent change to children, you must update each child template individually.
- The update endpoint accepts either a template_id or code for the `extends` field, but it resolves to the **latest version** of that code at update time.

**Impact:** Multiple rounds of template updates to get the correct inheritance chain. Initial fix attempt failed because subtypes still inherited the old `predecessor_event` definition.

**Suggested fix:** Add to the Template Inheritance section:

> **Versioning and inheritance:** The `extends` field stores a resolved `template_id`. When a parent template is updated (creating a new version), existing child templates continue extending the old parent version. To propagate parent changes to children, each child must be updated with `"extends": "<PARENT_CODE>"` (which resolves to the latest parent version).

---

## 5. Child Field Override Behavior During Template Update

**What happened:** When updating a child template with `PUT`, if the request body includes fields that have the same names as inherited fields, the child's version overrides the parent's. This caused subtypes to "freeze" the old `predecessor_event` definition (with `target_templates: ["PLANNED_EVENT"]`) even after the parent was updated.

**What the docs say:** The inheritance rule "Child can override parent fields" is documented, but only in the context of template creation.

**What was missing:** A clear warning that the `PUT` update endpoint must receive ONLY the child-specific fields if you want inheritance to work correctly. If you fetch a resolved template (which includes inherited fields) and send it back in an update, all inherited fields become overrides on the child, effectively breaking inheritance for those fields.

The `?resolve=false` parameter is mentioned in `components.md` but:
- It's not in the OpenAPI spec
- When tested, it didn't reliably return only child-specific fields
- There's no documented way to distinguish "inherited" vs "own" fields in the API response

**Impact:** Required manually defining the exact child-specific fields for each of the 8 subtypes and updating them one by one.

**Suggested fix:**
1. Add a field-level `inherited: true/false` flag to the template response
2. Or reliably implement and document `?resolve=false`
3. Add a warning to the update documentation: "When updating a child template, only include fields that the child defines or overrides. Including inherited fields will override the parent's definition."

---

## 6. Swagger Docs as Source of Truth — But Incomplete

**What happened:** The `AI-Assisted-Development.md` correctly advises "Do not rely solely on documentation. The Swagger docs are the source of truth." However, the Swagger/OpenAPI spec itself has gaps:
- The `?resolve=false` query parameter on `GET /templates/{id}` is not in the OpenAPI spec
- The term creation request schema doesn't clearly show `label` as required
- The terminology list endpoint's `code` parameter behavior is ambiguous

**What was missing:** The Swagger docs should be complete and accurate if they're positioned as the source of truth. Currently, there's a gap between the Swagger spec, the `components.md` documentation, and the actual API behavior.

**Suggested fix:** Audit the OpenAPI specs for all services against actual API behavior. Ensure every parameter, every required field, and every validation rule is accurately reflected.

---

## Summary

| Issue | Category | Severity |
|-------|----------|----------|
| Term `label` required but not documented | Missing docs | Medium — first API call fails |
| Terminology `code` filter doesn't work | Wrong docs | High — causes data corruption |
| Inheritance doesn't affect reference validation | Missing docs | High — fundamental design impact |
| Template versioning breaks inheritance chain | Missing docs | Medium — multiple update rounds |
| Child field override during update | Missing docs | Medium — subtle inheritance breakage |
| Swagger spec incomplete | Incomplete docs | Low — workaround is trial and error |

All of these follow the pattern: **the API behavior is reasonable, but the documentation either doesn't mention it or implies something different.** Better docs would have prevented hours of debugging.
