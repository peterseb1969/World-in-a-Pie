# Implementation Plan: Document Relationships

Companion to [`document-relationships.md`](document-relationships.md). This file breaks the design into ordered, estimatable units and calls out risks.

## Implementation status (2026-04-25)

| Phase | Status | Commit |
|---|---|---|
| 0 — Rename term-ontology API | **Done** | `2eeb872` |
| 1 — Template `usage` annotation | **Done** | `2764c5b`, `85a11f0`, `33e0c39`, `236bd36` |
| 2 — Document validation + indexing | **Done** | `ef6cc47`, `1e1d7c1` |
| 3 — `versioned: false` lifecycle | **Done** | `218908d`, `80dec94` |
| 4 — Query APIs | **Done** | `23aafa5` |
| 5 — MCP tools | **Done** | `c3c55c7` |
| 6 — NATS event enrichment | **Done** | `02eee03`, `bb15ee9` |
| 7 — Postgres reporting | **Done** | `309deb5` |
| 8 — Archetype integration | **Deferred** — depends on Theme 11 (archetype system) which has no code yet. Per the contingency plan, MCP tools are exposed unconditionally; archetype gating becomes a developer-experience layer when Theme 11 lands. |
| 9 — Documentation | **In progress** — this pass. |

**Calibration note for the next agent.** The original plan estimated 7–10 BE-YAC working days (56–80h) across three sessions for Phases 0–9. Actual through Phase 7: ~5h in a single session, 22 commits. The decomposition was pessimistic because it assumed verification round-trips and human-in-the-loop bottlenecks that didn't materialise once the design doc was concrete and the live `wip-dev-local` deployment gave 60s build-and-smoke cycles. Per-phase estimates downstream of this calibration: 1–2h, not 1d.

## Scope

This plan covers the **platform capability** only: the `usage: relationship` template annotation, validation, query APIs, MCP tools, NATS event enrichment, and optional Postgres reporting. Specific templates for any particular application (knowledge base, lab journal, CRM, etc.) are out of scope — those belong in APP-YAC projects that build on top of the platform per [Vision.md](../Vision.md). See "Downstream work" at the bottom.

## Summary

**Platform capability: ~7–10 BE-YAC working days, ~15–20 commits across Phases 0–9.** Delivers the full `usage: relationship` template feature, the two new query APIs, MCP tools, NATS event enrichment, and optional Postgres reporting.

**Critical path:** Phases 0 → 1 → 2 → 4 → 5. Phases 3, 6, 7, 8, 9 can land in parallel or follow.

## Scope-budget reality check

Per CLAUDE.md §8, features are 3–7 commits, past 10 means reassess. This work is larger than a single feature — it is five stacked sub-features (rename, usage annotation, validation, query APIs, reporting). Decompose across **three BE-YAC sessions**:

1. Session A: Phases 0–3 (rename + template model + validation + versioning)
2. Session B: Phases 4–6 (query APIs + MCP + events)
3. Session C: Phases 7–9 (reporting + archetype + docs)

Each session stays within the scope budget.

---

## Phase 0 — Rename term-ontology API (prerequisite) — **DONE**

Completed in commit `2eeb872` (2026-04-25). Cleared the naming collision so "relationship" can mean document-to-document unambiguously in every subsequent phase.

**Changes shipped:**

| What | Where |
|---|---|
| API handler | `components/def-store/src/def_store/api/ontology.py` — `create_relationships` → `create_term_relations` |
| Service method | `components/def-store/src/def_store/services/ontology_service.py` — `create_relationships` → `create_term_relations` (and `list_*`, `delete_*`, `list_all_*`) |
| Model class | `components/def-store/src/def_store/models/term_relationship.py` → `term_relation.py`, `TermRelationship` → `TermRelation` |
| API model classes | `Create/DeleteRelationshipRequest` → `Create/DeleteTermRelationRequest`; `RelationshipResponse` → `TermRelationResponse`; `RelationshipListResponse` → `TermRelationListResponse` |
| Mongo collection | `term_relationships` → `term_relations` |
| HTTP routes | `/ontology/relationships` → `/ontology/term-relations` |
| NATS subject + event types | `wip.relationships.>` → `wip.term_relations.>`; `RELATIONSHIP_CREATED/DELETED` → `TERM_RELATION_CREATED/DELETED` |
| MCP tools | `mcp__wip__create_relationships` / `list_relationships` / `delete_relationships` → `*_term_relations` (server.py, client.py, tools.yaml) |
| Cross-component | reporting-sync (Postgres table `term_relations`, NATS consumer), document-store backup (`term_relations` archive entity), registry namespace deletion, WIP-Toolkit (archive entity, EntityCounts field, helper functions), scripts/dev-delete.py, scripts/import_obo_graph.py, scripts/test_ontology_e2e.py |

**Migration.** *None.* Per "no backward compatibility" guidance, fresh-instance restart is the recovery path. Old Mongo collections / Postgres tables / NATS subjects from a prior run will not be picked up by the new code; deploy a clean instance.

**Preserved (intentionally not renamed):**

- The system-terminology data identifier `_ONTOLOGY_RELATIONSHIP_TYPES`. Apps' `_ONTOLOGY_RELATIONSHIP_TYPES_EXT.json` extension files match against this value; renaming would break those without coordination across app repos. The constant in `system_terminologies.py` is `TERM_RELATION_TYPES_TERMINOLOGY_VALUE` but its value remains `"_ONTOLOGY_RELATIONSHIP_TYPES"`.
- The MCP-server `_generated_schemas.py` is regenerated separately via `scripts/update-schemas.sh` (after rebuilding def-store).

**Tests.** Existing def-store, mcp-server, and reporting-sync tests updated to use the new names. New contract tests in `components/mcp-server/tests/test_client_contracts.py` (`test_term_relations_tool_names_replaced_old_ones`, `test_term_relations_url_path_uses_kebab_form`) pin the rename and assert the old names are gone — regression guard against pattern-matched re-introduction.

**Actual scope:** ~50 files / ~1100 lines changed. Larger than the 2-commit estimate; the cross-component reach (reporting-sync, document-store backup, registry, toolkit, scripts) was understated in the original plan.

---

## Phase 1 — Template model: `usage` annotation + relationship constraints

**Changes:**

| What | Where |
|---|---|
| Add `usage` field | `components/template-store/src/template_store/models/template.py` — enum `TemplateUsage` with values `entity` (default), `reference`, `relationship` |
| Add `source_templates` / `target_templates` fields | Same file, template-level |
| Add `versioned: bool` field | Same file, default `true`, immutable after creation |
| Validation on create | `components/template-store/src/template_store/services/template_service.py` — when `usage=relationship`: require non-empty `source_templates`, `target_templates`, and `source_ref`/`target_ref` reference fields that match template-level lists |
| Registry registration | `source_templates` / `target_templates` template values resolved to template_ids at creation (same pattern as existing `target_templates` on reference fields) |
| Template-store API schema | `components/template-store/src/template_store/models/api_models.py` — mirror the new fields in request/response shapes |
| Tests | Contract tests for valid/invalid relationship templates |

**Estimate:** 2–3 commits, **1 day.**

**Risks:**
- `versioned: bool` being immutable means changing it requires creating a new template. Flag this clearly in error messages.
- The `source_ref`/`target_ref` field-name convention is rigid — if authors want different field names, they can't. Accept this trade-off (see design doc rationale).

---

## Phase 2 — Document-store validation + indexing

**Changes:**

| What | Where |
|---|---|
| Create-path validation | `components/document-store/src/document_store/services/document_service.py` — when creating a document against a `usage=relationship` template: validate source/target refs resolve to documents in allowed templates, same namespace, not archived |
| Cross-namespace rejection | New error code `cross_namespace_relationship` |
| Index creation | On template registration/activation, ensure Mongo indexes exist: `(template_id, data.source_ref)` and `(template_id, data.target_ref)` per namespace collection. Idempotent. |
| Tests | Contract tests covering all rejection paths |

**Estimate:** 2–3 commits, **1–1.5 days.**

**Risks:**
- Existing document collections are per-namespace; new indexes add startup cost. Small per namespace; measure on a Pi-scale install.
- Order of operations: must validate source/target templates BEFORE attempting the Registry-backed ref resolution, or the error messages are confusing.

---

## Phase 3 — `versioned: false` lifecycle

**Changes:**

| What | Where |
|---|---|
| Update path branching | `components/document-store/src/document_store/services/document_service.py` — read template.versioned; if false, take the overwrite-in-place path with existing `if_match` concurrency token |
| Reporting-sync event handling | Latest-only documents should not append version rows — update `reporting-sync/worker.py` to replace instead of insert when template.versioned is false |
| Tests | Concurrent-update test; versioned+non-versioned side-by-side |

**Estimate:** 1–2 commits, **0.5–1 day.**

**Risks:**
- Race conditions on versioned=false updates without if_match. Document clearly that if_match is strongly recommended.

---

## Phase 4 — Query APIs (`/relationships`, `/traverse`)

**Changes:**

| What | Where |
|---|---|
| `GET /api/document-store/documents/{id}/relationships` | New handler in `components/document-store/src/document_store/api/` — MongoDB find with indexed filters |
| `GET /api/document-store/documents/{id}/traverse` | New handler — `$graphLookup` aggregation, depth cap 10 |
| Bidirectional merge | Decide during implementation: server-side merge with dedup by `_id` (preferred) vs two-round-trip pattern |
| Pagination | `limit`/`offset` on `/relationships` endpoint; `depth` cap on `/traverse` |
| Tests | Round-trip tests with fixture data: fan-out, fan-in, multi-hop lineage, cycles |

**Estimate:** 3–4 commits, **2 days.**

**Risks:**
- `$graphLookup` has quirks around `connectFromField`/`connectToField` semantics and cycle handling. Prototype the depth-N traversal against realistic data before committing to the aggregation shape.
- Performance at depth > 5 on unindexed data. Require the indexes from Phase 2 before exposing traverse.

---

## Phase 5 — MCP tools

**Changes:**

| What | Where |
|---|---|
| `get_document_relationships` tool | `components/mcp-server/src/wip_mcp/server.py` + `client.py` |
| `traverse_documents` tool | Same files |
| Tool descriptions | Follow existing conventions (brief, example-rich) |
| Contract tests | Per the pattern from commit c10f5dc |

**Estimate:** 1–2 commits, **0.5 day.**

**Risks:** None notable. Wrappers over the Phase 4 endpoints.

---

## Phase 6 — NATS event contract enrichment

**Changes:**

| What | Where |
|---|---|
| Event payload for relationship docs | `components/document-store/src/document_store/services/document_service.py` — `_document_to_event_payload()` (around line 595). When template has `usage=relationship`, include: `template_usage`, `source_ref_resolved`, `source_template_value`, `target_ref_resolved`, `target_template_value` |
| reporting-sync consumer | `components/reporting-sync/src/reporting_sync/worker.py` — populate source_ref_id/target_ref_id columns from the enriched event (see Phase 7) |
| External subscriber story | Document the payload shape in `docs/nats-jetstream.md` |
| Tests | Event payload shape tests |

**Estimate:** 1 commit, **0.5 day.**

**Risks:**
- Event payload growth (~10 extra fields per relationship event). At an experiment-with-20-inputs rate, this is 10 KB / 20 events = 500 bytes/event of growth. Acceptable.
- Back-compat: existing consumers must ignore unknown fields. JetStream consumers already use JSON deserialization with loose schemas — verify.

---

## Phase 7 — Postgres reporting (optional, archetype-gated)

**Changes:**

| What | Where |
|---|---|
| Schema manager | `components/reporting-sync/src/reporting_sync/schema_manager.py` — when processing a `usage=relationship` template, add `source_ref_id TEXT`, `target_ref_id TEXT`, with btree indexes on each |
| Worker | Populate these columns from enriched NATS events (Phase 6) |
| Batch sync | Back-fill from existing MongoDB documents on first sync |
| Tests | Join queries against fixture data confirm indexed path |

**Estimate:** 2 commits, **1 day.**

**Risks:**
- Schema migration for existing reporting tables that already have relationship templates from pre-v2 experiments (none expected in prod; dev may have).
- Not on the platform critical path — can ship after Phases 0–6 are done.

---

## Phase 8 — Archetype integration

**Depends on:** whether the archetype system (feature seeds Theme 11) has landed.

**If archetypes not yet implemented (likely at Phase 8 landing):**
- Expose MCP tools unconditionally (minimal gating).
- Defer archetype-level scaffolding. Apps declaring relationships in their bootstrap manifest get the templates installed regardless.
- Add a TODO linking to the Theme 11 design work.

**If archetypes exist:**
- Gate MCP tool visibility on archetype.
- Add relationship-template scaffolding to `scripts/create-app-project.sh` for integration/authoring archetypes.

**Estimate:** 0.5–1 day, depending on state of Theme 11.

---

## Phase 9 — Documentation

**Changes:**

| What | Where |
|---|---|
| API conventions | `docs/api-conventions.md` — note relationship validation and cross-namespace rejection |
| Data model | `docs/data-models.md` — add `usage` annotation section |
| MCP server docs | `docs/mcp-server.md` — new tools |
| Backend CLAUDE.md | `scripts/setup-backend-agent.sh` heredoc — add `usage: relationship` to the key conventions section |
| NATS event docs | `docs/nats-jetstream.md` — relationship event shape |
| PoNIF consideration | Evaluate whether any aspect of relationships qualifies as a Powerful-Non-Intuitive-Feature (`mcp__wip__ponifs` resource) — probably yes: the `usage` annotation changing lifecycle without changing structure is the kind of thing that looks like a bug if you assume conventional patterns |

**Estimate:** 1 commit, **0.5 day.**

---

## Dependency graph

```
Phase 0 (rename) ─┐
                  ├─→ Phase 1 (template model) ──→ Phase 2 (validation) ──┬─→ Phase 4 (query APIs) ──→ Phase 5 (MCP) ──┐
                  │                                                        │                                             │
                  │                                 Phase 3 (versioned=false) ─────────────────────────────────────────┤
                  │                                                                                                      │
                  │                                                        Phase 6 (NATS events) ──→ Phase 7 (reporting) │
                  │                                                                                                      │
                  │                                                                                 Phase 8 (archetype) ─┤
                  │                                                                                                      │
                  └────────────────────────────────────────────────────────────────────────────── Phase 9 (docs) ───────┘
```

**Parallelizable:** once Phase 5 lands, Phases 6, 7, 8, 9 can proceed in any order.

## Acceptance criteria (platform capability)

The platform work is "done" when all of the following hold:

1. A template with `usage: relationship` can be created via template-store API and shows up correctly in `mcp__wip__describe_data_model`.
2. A document created against that template is validated: source/target refs must resolve, must be in allowed templates, must be in same namespace. Invalid inputs return specific error codes.
3. `GET /documents/{id}/relationships` returns relationships pointing at or from the document, in under 50 ms for a document with up to 100 inbound+outbound relationships (MongoDB with indexes).
4. `GET /documents/{id}/traverse?depth=3` returns a correct lineage tree on fixture data in under 200 ms.
5. MCP tools `get_document_relationships` and `traverse_documents` call through correctly and their contract tests pass.
6. NATS events published on relationship create/update/delete include the enriched payload. An external subscriber can rebuild relationship state from the stream without querying back.
7. Term-ontology renames are complete: no code path references the old names; migration script runs cleanly on a test database.
8. Backup + restore round-trips relationship documents correctly, and cross-namespace relationship attempts are rejected.
9. **Full MongoDB-only path passes all of the above with Postgres stopped.** This is the v2 invariant and must be verified explicitly.
10. Docs updated. PoNIF registered if applicable.

## Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| `$graphLookup` doesn't scale past depth 5–7 on real data | Medium | Prototype in Phase 4 spike; cap depth at 10; document performance envelope |
| Template `usage` field becomes immutable but authors need to change it | Medium | Version-2 of the template creates a new template with the same value; document the migration pattern |
| Reporting-sync schema drift when `usage` changes between template versions | Medium | Treat `usage` change as a breaking schema change — block in Phase 5 schema validation |
| Name-clash rename breaks a dependent tool chain | Low (Peter-owned) | Thorough grep before Phase 0; document the rename in change-propagation-checklist.md |
| Archetype system (Theme 11) isn't ready at Phase 8 time | High | Phase 8 designed to degrade gracefully — expose tools unconditionally if archetypes absent |

## What I didn't consider

Known unknowns — flagged so they get addressed during implementation or in the next design round:

- **Console UI** for relationship panels and traversal visualization. Not in scope here. Console receives a case once the APIs land.
- **Permissions.** Can a user with read on namespace A follow an outbound relationship to namespace B if they have read on B? Cross-namespace relationships are rejected at write, so this doesn't matter for v2, but for future cross-namespace support this needs a decision.
- **Ontology-doc convergence.** Term relations (post-rename) and document relationships now share vocabulary shape. Whether they should eventually merge into a single edge abstraction is a v3+ question.
- **Import/export format.** Relationship documents in backup/restore are standard documents; no new format. But CSV/XLSX import (document-store's `import_documents_csv`) doesn't have a natural shape for edges. Probably not a v2 problem — defer.

---

## Downstream work (not in platform scope)

Per [Vision.md](../Vision.md), WIP ships primitives, not solutions. Specific template sets, domain-particular data models, and content migration belong in APP-YAC projects that build on top of the platform. Examples of work this platform capability enables — to be picked up by separate APP-YAC initiatives, not by BE-YAC:

- **Domain-specific template sets** (lab journal, CRM, content management, knowledge management, etc.) — each an APP-YAC's responsibility, scaffolded via `create-app-project.sh` once archetypes (Theme 11) land.
- **Migration of existing content** into WIP (any existing markdown tree, database, or file collection) — script design and content-shape decisions belong to the consuming APP-YAC, not the platform.
- **Console UX for relationship navigation** — app-specific UX patterns (e.g., graph visualizations, panel layouts) are shipped by app frontends, not by WIP Console (which is admin-only per Vision.md).

The platform provides the primitive; apps decide how to use it.

---

## References

- Design: [`document-relationships.md`](document-relationships.md)
- Feature seed: [`v2-design-seeds.md#theme-8`](../../../FR-YAC/reports/BE-YAC-20260409-1636/fireside-v2-design-seeds.md)
- Reference fields (foundation): [`reference-fields.md`](reference-fields.md)
- Vision (platform/app boundary): [`../Vision.md`](../Vision.md)
