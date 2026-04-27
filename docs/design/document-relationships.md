# Design: Document Relationships

## Status

**Implemented as of 2026-04-25** (Phases 0–7 of the implementation plan, commits `2eeb872`..`bb15ee9`). Phase 8 (archetype gating) deferred until Theme 11 lands. Phase 9 (this documentation pass) ships alongside the implementation.

Derived from Theme 8 of the [v2 design seeds](../../../FR-YAC/reports/BE-YAC-20260409-1636/fireside-v2-design-seeds.md#theme-8-template-usage-annotations-apr-14). Decisions recorded in that file's "Decisions (2026-04-25)" section close all outstanding Theme-8 questions.

Companion: [`document-relationships-implementation.md`](document-relationships-implementation.md) — decomposition, sequencing, effort estimates.

## Motivation

WIP today supports Document → Document references via the `reference` field type (see [`reference-fields.md`](reference-fields.md)). A reference says "this document points at that one," but it carries no properties of its own. When information belongs to the **interaction** between two documents — not to either endpoint — there is no idiomatic way to model it.

The motivating example (lab journal): an experiment uses 50µg of bevacizumab at 10mg/mL from lot `LOT-2026-0412`, with role `catalyst`. The quantity and role are:

- not a property of bevacizumab — it's still bevacizumab regardless of the experiment
- not a property of the experiment — the protocol describes what was done, not this specific edge
- they ARE properties of *how the experiment uses bevacizumab*

Modeling this as fields on either endpoint either duplicates data or forces artificial encoding. Modeling it as a separate "EXPERIMENT_INPUT" document with `source_ref`, `target_ref`, and edge properties is natural.

### Rule of thumb (from the fireside seeds)

- True regardless of context → **entity document** (molecule's molecular weight)
- True only in this specific interaction → **relationship document** (quantity used, role)
- About what happened → **activity/experiment document** (protocol, date, operator)
- About what participated and how → **relationship document**

## Design

### The `usage` annotation on templates

Every template gets a new top-level field `usage` with values:

| Value | Meaning | Status |
|---|---|---|
| `entity` (default) | Full document lifecycle — what v1.1.0 does today | Shipping behavior |
| `reference` | LOV — lightweight, frequently-updated, skip versioning | Out of scope for this doc (Theme 8 covers separately) |
| `relationship` | Typed, property-carrying edge between documents | **This doc** |

The annotation changes *behavior* (validation, query APIs, reporting shape), not *structure*. Relationship documents flow through the same document engine, same backup format, same MCP document APIs. Templates without `usage: relationship` behave exactly like v1.1.0 entity templates — zero cost.

### Edge type shape

An **edge type** is the schema for a class of relationships between documents. It is implemented as a template with `usage: relationship` — the storage layer is unchanged, but the conceptual layer treats edge types as a distinct kind of thing from entity templates. Throughout this doc, "edge type" refers to the schema and "template" refers to the underlying storage representation.

```yaml
template:
  value: EXPERIMENT_INPUT
  usage: relationship
  source_templates: [EXPERIMENT, ASSAY]
  target_templates: [MOLECULE, BIOSPECIMEN, CHEMICAL, INSTRUMENT]
  versioned: true              # default true; set false for latest-only relationships
  identity_fields: [source_ref, target_ref, role]   # optional; dedups edges
  fields:
    - name: source_ref
      type: reference
      reference_type: document
      target_templates: [EXPERIMENT, ASSAY]   # must match template-level source_templates
      mandatory: true
    - name: target_ref
      type: reference
      reference_type: document
      target_templates: [MOLECULE, BIOSPECIMEN, CHEMICAL, INSTRUMENT]   # match template-level target_templates
      mandatory: true
    - name: role
      type: term
      terminology_ref: INPUT_ROLES
      mandatory: true
    - name: quantity
      type: string
    - name: concentration
      type: string
    - name: lot_number
      type: string
```

**Two new template-level fields:**

- `source_templates: list[str]` — template values allowed as the source endpoint. Required when `usage: relationship`.
- `target_templates: list[str]` — template values allowed as the target endpoint. Required when `usage: relationship`.

**Convention:** an edge type MUST declare two reference fields named `source_ref` and `target_ref`. These are regular reference fields with `reference_type: document` and `target_templates` matching the template-level list. Template-store validates at creation time that these fields exist and agree with the template-level declarations.

**Why mandate the field names?** Query APIs, Mongo indexes, and reporting-sync all need stable access paths. Naming them by convention is simpler than introducing "which field is the source" metadata.

### Validation at write time

On `create_document` against an edge type:

1. Standard template validation (all fields, reference resolution).
2. `source_ref` must resolve to a document whose template value is in the template-level `source_templates` list. Resolution goes through Registry (`resolve_entity_ids(bypass_cache=True)` — Principle 3).
3. `target_ref` must resolve to a document whose template value is in the template-level `target_templates` list.
4. If the source or target is in a different namespace than the relationship document, **reject with `cross_namespace_relationship` error** — deferred to post-v2.
5. If the target document has `status: archived` or is hard-deleted, reject — standard reference-integrity rule.

### Versioning

`versioned: true` (default) — relationship documents version like any other entity. Updates create new versions, full audit trail preserved.

`versioned: false` — updates replace the existing document in place. Useful for relationships where the edge identity matters but its history doesn't (e.g., "monster has spell" in a bestiary).

Implementation: the `versioned` flag lives on the template, not the document. Document-store's update path consults the template to decide between the "create-new-version" path and the "overwrite-in-place" path.

### Storage model

Relationship documents are MongoDB documents in the namespaced document collections, same as entity documents. Additional indexes:

```javascript
// Always, on document-store startup or template registration
db.documents_<namespace>.createIndex({ template_id: 1, "data.source_ref": 1 })
db.documents_<namespace>.createIndex({ template_id: 1, "data.target_ref": 1 })
```

Indexes are conditional on the template's `usage: relationship`. Templates with `usage: entity` don't get them.

### Query APIs

Two new document-store endpoints:

#### `GET /api/document-store/documents/{id}/relationships`

Return relationship documents that point at or from this document.

| Query parameter | Values | Default |
|---|---|---|
| `direction` | `incoming` \| `outgoing` \| `both` | `both` |
| `template` | template value or comma-separated list | all |
| `namespace` | target namespace | inferred from document |
| `active_only` | `true` \| `false` | `true` |

Response: paginated list of relationship documents (same shape as `list_documents`). No resolution of the other endpoint by default — caller decides whether to follow refs.

Implementation: MongoDB `find({ template_id: {$in: [...]}, $or: [{"data.source_ref": id}, {"data.target_ref": id}] })`, indexed.

#### `GET /api/document-store/documents/{id}/traverse`

N-hop graph traversal from this document.

| Query parameter | Values | Default |
|---|---|---|
| `depth` | 1..10 | 1 |
| `types` | edge type values | all |
| `direction` | `outgoing` \| `incoming` \| `both` | `outgoing` |

Response: tree structure rooted at the document, with edges = relationship documents, nodes = documents reached.

Implementation: MongoDB `$graphLookup`. `direction=outgoing` walks `source_ref → _id → target_ref`. `direction=incoming` walks the reverse. `direction=both` runs two `$graphLookup`s and merges on document `_id` to deduplicate cycles.

**Depth cap:** 10. MongoDB has no hard limit but performance degrades with depth × average degree. 10 is generous enough for real lineage; anything deeper is an analytical query that belongs in Postgres.

### MCP tools (new)

Two new MCP tools in document-store's surface. The existing naming convention is `get_<thing>` / `list_<thing>` — I'll follow it.

| Tool | Signature | Purpose |
|---|---|---|
| `get_document_relationships` | `(document_id, direction?, template?, namespace?, active_only?)` | Thin wrapper over `GET /documents/{id}/relationships` |
| `traverse_documents` | `(document_id, depth=1, types?, direction="outgoing", namespace?)` | Wrapper over `GET /documents/{id}/traverse` |

These are in addition to the existing document tools — `list_relationships` (the term-ontology tool) is being renamed as part of this work.

### Name-clash resolution

Term-ontology edges (is_a, part_of) used to live under the name "relationship" — `create_relationships` in def-store's API, `TermRelationship` model, `term_relationships` Mongo collection. That name collided with the document-level meaning introduced here.

**Rename — completed in Phase 0** (no backward compat; fresh-instance restart is the recovery path):

| Old (pre-Phase 0) | New |
|---|---|
| `create_relationships` (def-store API) | `create_term_relations` |
| `mcp__wip__create_relationships` (MCP tool) | `mcp__wip__create_term_relations` |
| `list_relationships` (MCP tool) | `list_term_relations` |
| `delete_relationships` (MCP tool) | `delete_term_relations` |
| `get_term_hierarchy` | unchanged |
| Module `components/def-store/src/def_store/models/term_relationship.py` | `term_relation.py` |
| Class `TermRelationship` | `TermRelation` |
| Mongo collection `term_relationships` | `term_relations` |
| HTTP path `/api/def-store/ontology/relationships` | `/api/def-store/ontology/term-relations` |
| NATS subject `wip.relationships.>` | `wip.term_relations.>` |
| NATS event types `RELATIONSHIP_CREATED/DELETED` | `TERM_RELATION_CREATED/DELETED` |

After the rename, the word "relationship" in WIP consistently means "document-to-document typed edge." The word "relation" is for ontology edges between terms (OBO-style).

The system-terminology data identifier `_ONTOLOGY_RELATIONSHIP_TYPES` was **not** renamed: apps' `_ONTOLOGY_RELATIONSHIP_TYPES_EXT.json` extension files match against this value, and renaming it would silently break those apps. The constant in `system_terminologies.py` is `TERM_RELATION_TYPES_TERMINOLOGY_VALUE` but its value remains `"_ONTOLOGY_RELATIONSHIP_TYPES"`.

### Postgres reporting (optional, archetype-gated)

When `reporting-sync` is deployed, an edge type's reporting table gets two indexed columns: `source_ref_id` (resolved from the `source_ref` reference field's internal storage — identity_hash for `latest`, document_id for `pinned`) and `target_ref_id`. These are indexed btree columns that enable SQL JOINs against the source and target document tables.

Example queries then become tractable in SQL:

```sql
-- All experiments that used bevacizumab as a catalyst
SELECT DISTINCT e.*
FROM doc_experiment_input rel
JOIN doc_experiment e ON e.identity_hash = rel.source_ref_id
JOIN doc_molecule m ON m.identity_hash = rel.target_ref_id
WHERE m.data->>'name' = 'bevacizumab'
  AND rel.data->>'role' = 'catalyst';
```

Materialized views are out of scope for this design — they are a general analytics concern, not relationship-specific. A future "saved reports" feature (seen in feature-seeds Theme 10) is the right home for materialized views.

### NATS event contract

Relationship documents publish standard document events (`document.created`, `document.updated`, `document.deleted`, `document.archived`) on the existing `WIP_EVENTS` stream. The event payload for a relationship document MUST include enough state for an external subscriber (Snowflake, BigQuery, etc.) to rebuild the edge without querying back:

```json
{
  "event_type": "document.created",
  "document_id": "...",
  "template_id": "...",
  "template_value": "EXPERIMENT_INPUT",
  "template_version": 3,
  "template_usage": "relationship",
  "namespace": "lab-journal",
  "version": 1,
  "data": {
    "source_ref": "...",
    "source_ref_resolved": "...identity-hash or pinned document_id...",
    "source_template_value": "EXPERIMENT",
    "target_ref": "...",
    "target_ref_resolved": "...",
    "target_template_value": "MOLECULE",
    "role": "catalyst",
    "quantity": "50µg",
    "concentration": "10mg/mL",
    "lot_number": "LOT-2026-0412"
  },
  "timestamp": "2026-04-25T...",
  "actor": "peter"
}
```

Resolved refs are included so subscribers don't have to chase references. Template usage is included so subscribers can route relationship events to edge tables without inspecting the template.

### Archetype integration

The relationship capability is gated on the application archetype (feature seeds Theme 11):

- **Archetypes where relationships are relevant** (e.g., data integration, authoring, analytics): MCP tools exposed by default, Console UI shows relationship panels.
- **Archetypes where relationships are irrelevant** (e.g., lightweight): MCP tools hidden by default, app can opt in via manifest.

Operational mechanism: the archetype determines which MCP tools are in the tool list and whether `create-app-project.sh` scaffolds relationship-capable manifests. The platform itself always supports relationships at the API level — archetype gating is a developer-experience layer, not a functional gate. Specific archetype definitions and their contents are designed in the Theme 11 work, not here.

## Invariant: MongoDB-only functionality

Relationships must be fully functional on MongoDB alone. **No code path in the relationship APIs may require Postgres or NATS.** Postgres and NATS are analytics paths, not functional dependencies. This is the explicit v2 constraint recorded in the feature seed doc (2026-04-25).

| Capability | Storage requirement |
|---|---|
| Create / update / delete / version relationship documents | MongoDB only |
| `/documents/{id}/relationships?direction=...` | MongoDB indexes |
| `/documents/{id}/traverse?depth=N` | MongoDB `$graphLookup` |
| Edge-property filtering (`role = catalyst`) | MongoDB field indexes |
| Heavy aggregations, cross-template analytics | Postgres (when present) OR external NATS subscriber |
| Streaming export to Snowflake / BigQuery / etc. | NATS stream (optional) |

## Interaction with existing features

### Reference fields

Relationships build on the existing reference field infrastructure. `source_ref` and `target_ref` are regular reference fields — no new field type. The novelty is the template-level contract (`source_templates`, `target_templates`, `usage: relationship`) and the indexes that follow from it.

### Synonym resolution

A relationship's `source_ref` and `target_ref` pass through the existing universal synonym resolver. Apps can write `source_ref: "experiment-42"` and Registry resolves to canonical IDs. Writes always `bypass_cache=True` per Principle 3 (already in place for reference field normalization).

### Template versioning

Edge types version like any other template. A v2 of EXPERIMENT_INPUT can add fields; existing v1 documents keep validating against v1. The `versioned` flag is set at creation and immutable — changing it would silently reshape existing documents' lifecycle.

### Namespace isolation

Source and target must be in the same namespace as the relationship document. Cross-namespace relationships are rejected at write time with `cross_namespace_relationship` error (see Theme 3 in the feature seeds — out of scope for this design).

### Deletion guardrails

Feature seeds Apr-21 Theme on deletion guardrails extends to relationships: if a document has inbound relationship documents pointing at it, hard-delete is blocked (force override accepted). The check is already the right pattern for terms and templates; extending it to documents-as-relationship-targets is additive.

### Backup/restore

Relationship documents are documents. Existing backup/restore handles them automatically. The Theme-5 open question (restore bypasses template validation) applies to relationships the same way — if a restore brings in relationship documents whose `source_templates` / `target_templates` no longer match the current template version, that's the same bug, not worse.

## What's out of scope for v2

- **Meta-relationships** (relationships whose source or target is another relationship). Deferred — adds recursive complexity.
- **Cross-namespace relationships.** Rejected at write. Theme 3 will revisit.
- **Auto-materialized views** for common analytical patterns (usage frequency, cardinality). A future "saved reports" feature is the right home.
- **LOV templates** (`usage: reference`). Separate theme, separate design doc.
- **Graph DBMS integration** (Neo4j, Dgraph). MongoDB `$graphLookup` + Postgres JOINs cover the v2 traversal requirements.

## Open implementation questions

- **`$graphLookup` bidirectional merge strategy.** For `direction=both`, two `$graphLookup`s return overlapping trees. Merging requires dedup by document `_id`, and some way to mark the direction each edge was reached from. Two options: (a) run the merge client-side in the document-store handler, or (b) use a single `$graphLookup` with a pre-projected edge collection that has both directions materialised. Decide during implementation.
- **`versioned: false` concurrency.** If two writers update the same latest-only relationship simultaneously, the "overwrite in place" path needs an optimistic-concurrency token. Existing document-store update path has `if_match` support — reuse.
- **Identity fields on edge types.** Whether to make `(source_ref, target_ref)` a default identity tuple (dedup edges on create) or leave it to the schema author. Recommendation: leave to the author — sometimes multiple edges between the same pair are intentional (an experiment used bevacizumab twice, at different timepoints, different concentrations). Let the author set `identity_fields: [source_ref, target_ref, timepoint]` when dedup is wanted.
- **Relationship-to-self.** Is `source_ref == target_ref` legal? Arguably yes (self-loop in graph terms — rare but legitimate, e.g., a document `supersedes` an earlier version of itself). Default: allow, no check at platform level.

## References

- Feature seed Theme 8: [`v2-design-seeds.md#theme-8`](../../../FR-YAC/reports/BE-YAC-20260409-1636/fireside-v2-design-seeds.md)
- Reference fields (foundation): [`reference-fields.md`](reference-fields.md)
- Template ID management (interacts with versioning): [`../../../FR-YAC/reports/BE-YAC-20260409-1636/fireside-template-id-management.md`](../../../FR-YAC/reports/BE-YAC-20260409-1636/fireside-template-id-management.md)
- Implementation plan: [`document-relationships-implementation.md`](document-relationships-implementation.md)
