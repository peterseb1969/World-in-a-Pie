---
section: fixes
cases: [1264]
---

### A template-store or Registry outage on the remaining write paths is refused per item, never a 500, a warning or a miss

- **Bulk create**: a template-store outage while the relationship and
  isolation gates read an item's template answers that item
  `dependency_unavailable`; the Registry-registration stage no longer asks
  template-store (it reads the template's usage from validation).
  `POST /validation/validate-bulk` shares bulk create's tolerant template
  warm-up instead of failing the batch with a 500.
- **`include_subtypes`**: a template-store outage while expanding a
  reference field's subtypes is `dependency_unavailable`; it read as "no
  subtypes" (refusing legal subtype references) and was cached for the batch.
- **Ambiguous `target_templates`**: an entry naming several templates is
  `ambiguous_reference` on every path, not only the endpoint check (a value
  or business-key reference got `reference_check_failed`).
- **Registry error items**: a Registry answer carrying `status: "error"` for
  an item is a Registry outage (503 `registry_unavailable`) on every resolve
  path, never "not found"; an item refused for what the caller sent
  (`invalid_*`) is an answer, not an outage to retry.
- **Rollbacks**: a create that fails after the Registry minted its entry
  (auto-synonym refused, single or bulk) releases the entry and the synonyms
  it claimed; a rollback that cannot reach the Registry is logged, never a
  500. A Registry write that could not reach the Registry carries
  `details.rule: registry_unavailable`.
- **`on_synonym_conflict: "warn"`** covers the synonyms the Registry refuses;
  a Registry that could not answer refuses the create in either mode.
