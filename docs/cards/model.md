<!-- card-sources: what this card condenses; checked by components/mcp-server/tests/test_card_drift.py
wip://data-model sha256:ed54f8c8e860fd46 reviewed:944752b5
wip://conventions sha256:781366155475f9fd reviewed:944752b5
docs/WIP_PoNIFs.md sha256:99346d0d8e8c7605 reviewed:ea985986
docs/design/ontology-support.md sha256:06666e3c8f3a3cc1 reviewed:ea985986
docs/slash-commands/app-builder/wip-implement.md sha256:1afef1d1999621a1 reviewed:809f9059
docs/slash-commands/app-builder/wip-design-model.md sha256:88c3300b894dc3ca reviewed:809f9059
-->
# WIP model card — Phase 2–3: designing and implementing the data model

## Gate — answer both out loud before any MCP write
- **Q1:** Is every terminology, term, template, field, identity field and edge type you are about to create or change part of the model the user approved? If not, STOP: that is a Phase-2 design event — go back, propose it, get explicit approval. Never add it inline.
- **Q2:** Are you about to persist app state (rules, config, per-doc-type behaviour, anything your code reads back) anywhere other than a `data` field of a declared template? If yes, STOP. Config is a document of a config template; a controlled vocabulary is a terminology. Unsure where it belongs → ask the user.
- Approval means the user explicitly said so ("approved", "go ahead", "looks good"). Questions or "let me think" are not approval.
- Before designing: `list_terminologies`, `list_terms`, `get_template_fields` — reuse what exists; never propose recreating it.

## Field naming — use WIP's names
- `mandatory: true` — NOT `required`.
- Identifiers are `value` (NOT `code`/`name`); display names are `label` (NOT `name`).
- Field types: string, number, integer, boolean, date, datetime, term, reference, file, array, object.
- `type: term` → `terminology_ref` (NOT `terminology_id`).
- `type: reference` → `reference_type` (document | term | terminology | template) plus `target_templates` (document refs) or `target_terminologies` (term refs).
- `type: object` → `template_ref` (NOT `template_id`) plus `template_ref_version` (mandatory with `template_ref`).
- `type: array` → `array_item_type`; `array_terminology_ref` / `array_template_ref` / `array_file_config` for term / object / file items; an array of references must set `reference_type`.
- Terms: `value` (unique within terminology), `label`, optional `description`, `names` or `aliases`.
- `full_text_indexed: true` only on string fields with reporting sync enabled.

## Identity fields — the hash decides create vs update
- `create_document` is an upsert: same identity hash → new version of the same document; different hash → new document.
- Identity fields are the natural key: the minimum fields that say "this is the same real-world thing".
- Never put timestamps, run ids or other per-run data in identity fields — every hash becomes unique and you get duplicates instead of versions.
- Avoid timestamps in non-identity fields too — they force a new version on every otherwise unchanged write.
- Too many identity fields → corrections create duplicates; too few → different entities collide. Both are silent.
- Empty `identity_fields` = append-only: every create makes a new document, and `update_document` (PATCH) is rejected with `append_only`. Use it only for event logs and similar.
- Do not add a synthetic identity field to a template that should be append-only.
- A term- or reference-typed identity field hashes the resolved referent's canonical ID, not the spelling: aliases of one term are one identity. If it cannot be resolved the write is rejected (fail-closed); an ambiguous name on an identity field is rejected too.
- `identity_fields` cannot change across template versions (`identity_fields_immutable`). Changing identity is a fork: declare a new template value. Get it right in Phase 2.
- PATCH cannot change identity fields (`identity_field_change`) — create a new document instead.

## `metadata.*` is never identity or structure
- `metadata.custom` is caller-attached context (source tags, loader hints, audit traces). It never feeds the identity hash.
- Never put `metadata.*` in `identity_fields` or `full_text_indexed` — rejected. Never branch, sort or dedup on it.
- If a value needs meaning, it is a `data` field on the template schema (a Phase-2 change).

## Creation order
- namespace (`create_namespace` / `upsert_namespace`) → terminologies → terms → (term relations) → templates, referenced before referencing → documents.
- Why: a template's references (terminology_ref, target_templates, …) are resolved when the template is created — an unresolvable one fails the create. Documents are validated against active terms and existing referenced documents at write time.
- Model in your dev namespace (named in CLAUDE.md). MCP tools run with an admin key that spans namespaces: pass `namespace=` on every call.

## Draft mode and activation
- Create templates with `status: "draft"` to skip reference validation — for circular dependencies or order-independent creation.
- `activate_template` validates and activates, cascading to referenced drafts. All-or-nothing: if any template in the chain fails, none activate. `dry_run=true` validates only.

## Template versions
- `create_template` is an upsert on (namespace, value): identical → `unchanged`; any difference → `updated`, new version N+1 with a diff in `details`. It never errors on a same-name create — read the status.
- `usage`, `versioned` and edge endpoint lists cannot change on a re-post (`immutable_property`).
- The old version STAYS ACTIVE. Retire it with `deactivate_template(template_id, version=N)` — `version` is required.
- Always pass `template_version` in `create_document`; without it WIP picks "latest active", which may be the wrong one.
- "Latest" resolution is cached for 5 s — after a template change, wait or pin the version.
- `update_template` `fields` replaces the whole list — include unchanged fields.
- Test a schema change against live documents with `validate_template_candidate` before minting versions.

## Edge types (relationships between documents)
- An edge type is a template with `usage: "relationship"`; use `create_edge_type`.
- It needs two `reference_type: document` fields named exactly `source_ref` and `target_ref`, plus non-empty template-level `source_templates` and `target_templates`.
- Do not set `target_templates` on `source_ref`/`target_ref` — it is derived from the template-level lists and discarded.
- Properties of the interaction itself (role, quantity) are further fields.
- Source and target documents must be in the edge's own namespace (`cross_namespace_relationship`); archived endpoints are rejected. Across namespaces use a plain document reference field instead.
- `versioned: false` (any template with identity fields; edges are the typical use) = updates overwrite in place, documents stay at version 1, no history. It requires non-empty `identity_fields`; declare them explicitly, e.g. `[source_ref, target_ref]` (`create_edge_type` fills that in when omitted; `create_template` does not). Add a third field (e.g. `role`) to allow several edges between one pair.
- `usage` and `versioned` are immutable after creation — choose deliberately.
- To let new templates take part, widen with `add_edge_type_endpoints` — never delete and recreate (strands existing edges). Endpoints cannot be removed.
- Query with `get_document_relationships` and `traverse_documents`; these work only on `usage: "relationship"` templates.

## Term relations (hierarchies)
- If a vocabulary has natural parent-child or part-whole structure (taxonomy, classification, org chart, geographic containment), model it with term relations, not a flat list.
- Create with `create_term_relations([{source_term_id, target_term_id, relation_type}])`, e.g. "Cat is_a Animal" (source = child).
- Seeded types: is_a, has_subtype, part_of, has_part, maps_to, mapped_from, related_to, finding_site, causative_agent, regulates, positively_regulates, negatively_regulates. More can be added to `_ONTOLOGY_RELATIONSHIP_TYPES`.
- Verify with `get_term_hierarchy(term_id, direction=...)` — direction: children, parents, ancestors, descendants; one `relation_type` per call (default is_a). `list_term_relations` shows all of a term's relations.
- Bulk-load whole ontologies with `import_terminology` (OBO Graph JSON).
- Relations = "related entities"; Registry synonyms = "the same entity". Do not use one for the other.

## Addressing terms and other entities
- A term identifier is exactly one of: the canonical UUID; `ns:terminology:value`; or the field form (a `terminology` parameter plus the raw value, never colon-parsed).
- The 2-part `TERMINOLOGY:VALUE` shorthand is rejected (422) on every term endpoint. Prefer the field form.
- Other entities: a bare value always resolves in your OWN namespace; `NS:VALUE` is the only value form that crosses namespaces; a UUID crosses freely. Permission (`allowed_external_refs`) does not make a bare value cross — write `"kb:KB_TOPIC"`.
- In documents submit the term's value or name; WIP stores both value and term_id. Inactive terms are rejected in new documents.

## Bulk results — 200 OK always
- Every write returns 200 even when items fail. Check each `results[i].status` (created, updated, unchanged, error, skipped).
- Branch on `results[i].error_code`, not the message text.
- Single-item MCP tools unwrap the result; bulk tools (`create_documents_bulk`, `create_templates_bulk`, `create_terminologies_bulk`) return the full envelope.
- On any failed call in Phase 3: stop, report the exact error, decide model issue (back to Phase 2) or WIP issue, ask the user.

## When the card is not enough
- `docs/WIP_PoNIFs.md` — the non-intuitive behaviours in depth.
- `docs/ontology-support.md` — term relations, traversal, OBO import.
- `wip://data-model` — field types, reference types, terms, templates, documents.
- `wip://conventions` — bulk responses, PATCH, upserts, namespaces, addressing, caches.
