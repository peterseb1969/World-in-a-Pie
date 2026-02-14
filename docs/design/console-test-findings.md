# WIP Console — Manual Test Findings (2026-02-14)

## Summary

19 findings from manual testing. 10 bugs, 4 design questions, 3 missing features, 1 cosmetic, 1 nice-to-have.

**Important:** All decisions below must be implemented on BOTH the data model/backend AND the UI side.

---

## Design Decisions (Resolved)

### A5 — Terminology Metadata
- **`language`**: Move to term level (terminology gets `default_language`; terms inherit it)
- **`version`**: Drop entirely (WIP has its own versioning)
- **`source` / `source_url`**: Promote to optional canonical fields on Terminology
- **`custom`**: Only store when non-empty; document as free-form extension point

### A6 — Sort Order
- Keep `sort_order`, make optional, default `null` (not `0`)
- UI sorts by `sort_order` when present, alphabetical fallback

### A7 — parent_term_id
- Keep — enables hierarchical terms / ontologies
- Needs proper UI support (tree view or indented list)

### B4 — Null Field Noise
- Use `exclude_none=True` on serialization — only store fields that are set
- A string field should store `{name, type, label, required}` and nothing else

### B6 — Empty Target Template on References
- Intentional — leaving target template empty is valid (allows referencing any document)

### Term Identity Simplification
- **Drop `code`** — `term_id` (T-XXXXXX) is the machine identity, `value` is the human key
- **`value`**: Required, unique within terminology, stored in documents
- **`label`**: Optional, defaults to `value` if not set — UI display text
- **`aliases`**: Non-language alternative values (abbreviations, legacy codes, misspellings)

### Term Language Model (Option 2 — Extend Existing `translations` Array)
- **Terminology** gets `default_language` (e.g., `"en"`) — declares base language of its terms
- **Term** top-level `value`/`label` are in the terminology's default language
- **`translations`** is the existing array field on the Term model. Currently `{language, label, description}` — extend each entry to also include `value` and `aliases`:
  ```json
  {
    "value": "Blood",
    "label": "Blood",
    "aliases": ["blood sample"],
    "translations": [
      { "language": "de", "value": "Blut", "label": "Blut", "aliases": ["Blutwert"] },
      { "language": "fr", "value": "Sang", "label": "Sang" }
    ]
  }
  ```
- Validation resolves across: `value` → `aliases` → all translation `value`s → all translation `aliases`
- All resolve to the same `term_id` — language is irrelevant for downstream analysis

### C4 — Raw Data Types in MongoDB
- Leave as-is — cosmetic issue only. Reporting (PostgreSQL) and table view API both resolve types correctly from templates.

### Drop "deprecated" Status for Terminologies and Templates
- **Terminologies:** only `active` / `inactive`
- **Templates:** only `draft` / `active` / `inactive`
- **Terms:** keep `deprecated` (with full workflow: `deprecated_reason`, `replaced_by_term_id`, dedicated endpoint)
- Rationale: "deprecated" for terminologies/templates was never enforced and functionally identical to inactive. Only terms have a meaningful deprecation workflow.

---

## Action Items

### Bugs — Fix All

| # | Area | Finding | Status |
|---|------|---------|--------|
| A1 | Term Aliases | UI does not work for create or update | Open |
| A2 | Audit Trail | Create events missing for Terminologies and Terms — only updates appear | Open |
| A3 | Updated_by (Terminology) | Field is empty on Terminology records | Open |
| A4 | Updated_by (Term/File) | Shows raw hash instead of readable username. Same root cause as D1. | Open |
| B1 | Identity Fields | Unlabeled checkbox in identity fields selection list | Open |
| B2 | Target Templates | Unlabeled checkbox in target template selection dropdown | Open |
| C1 | File Upload (Doc Create) | `"Invalid file type, allowed file types: *."` error | Open |
| C2 | Conditional Rule | Condition not evaluated correctly — rule fires regardless of field value | Open |
| C3 | Reference Validation | Validation fails even after linking to a document | Open |
| C5 | File Size Limits | Different limits between doc-creation dialog and file upload page | Open |
| D1 | Uploaded_by (File) | Raw hash instead of username (same root cause as A4) | Open |
| D2 | allowed_templates | Restriction not persisted — MongoDB shows `null` | Open |

### Missing Features — Expose on UI

| # | Area | Action | Status |
|---|------|--------|--------|
| B3 | Default Value | Expose `default_value` configuration in template field editor | Open |
| B5 | Enum Validation | Expose `enum` configuration in template validation editor | Open |
| UI | Translations | Expose existing `translations` array for terms (create + edit) | Open |

### Enhancements — Implement

| # | Area | Action | Effort | Status |
|---|------|--------|--------|--------|
| B7 | Template Rules | Add `greater_than`, `less_than`, `greater_than_or_equal`, `less_than_or_equal` operators | Low (3 files) | Open |
