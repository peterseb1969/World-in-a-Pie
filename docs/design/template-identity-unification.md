# Template Identity Unification — v2 Design

**Status:** Accepted direction (Peter + BE-YAC, 2026-07-19). One rule flagged for explicit confirmation (§4.3). Green-field design: migration cost was deliberately excluded from the discussion.

**Provenance:** Distilled from FIRESIDE-23 (which contests and then resolves FIRESIDE-15's directional decisions; extends FIRESIDE-3 and FIRESIDE-17 Theme 5). The fireside holds the discussion and the why; this document holds only the resulting design.

---

## 1. Summary

Templates are unified with documents wherever the mechanics allow: **identity registered in the Registry, one stable canonical UUID per logical entity, integer version coordinates, create-as-upsert.** The one deliberate difference is version semantics: document versions are *history* (one live head); template versions are a *catalog* (coexisting active variants).

A key finding shrank this design: the long-standing claim that "every WIP entity follows new-version → new-ID, templates are the exception" is **false**. WIP has exactly two versioned entities — documents and templates — and both keep a stable canonical ID across versions (unique key `(namespace, <id>, version)` in both stores). No ID semantics change in this design. What actually changes: template identity becomes *registered*, template create becomes an *upsert*, and reporting becomes *honest about schema versions*.

## 2. Vocabulary (normative)

| Term | Meaning |
|---|---|
| **the template** | The logical entity: canonical UUID, identity `(namespace, value)`, carries the version catalog. |
| **a template version** | A coordinate `(template_id, version)`. Not an entity: no canonical ID, no synonyms. Independently activatable (catalog semantics). |
| **identity** | The Registry-registered key that determines "which one is this." Never the entity itself. |
| **fork** | A change that creates a *new entity* rather than a new version (identity change). |

The term "family" (used during the design discussions to disambiguate v1's overloaded "template") is retired; "the template" always means the logical entity.

## 3. The Registry ontology principle

> **The Registry registers identities, never states. Versions — of documents and templates alike — are coordinates on an entity, not Registry citizens.**

Rationale: the Registry's mechanisms (composite-key upsert, synonyms, federation lookup) are all many-names-one-thing operations; a version has exactly one name, `(id, n)`, is never upserted (it is the *output* of an upsert), and is never resolved from an alternate identifier. Registering versions would add a Registry write to every document write (O(writes) growth, hot-path round-trips on Pi-class targets) for zero resolution capability.

Deferred escape hatch: if a concrete need for citable version handles emerges (e.g. regulatory citation of an exact record state), handles are minted **lazily, per use case** — never eagerly for every version.

## 4. The template identity model

### 4.1 Registered identity

The template's identity is `(namespace, value)`, registered in the Registry as the composite key `{ns, type: "template", value}`. This replaces the current **empty composite key** (`registry_client.py` mints template IDs with `composite_key={}`; the name today enters only as an auto-synonym side effect). The auto-synonym remains as the ordinary resolution mapping every named entity gets.

Consequences:
- The Registry gains upsert semantics for templates: registering the same `(ns, value)` again resolves to the existing entity — the mechanism behind create-as-upsert (§5.1).
- Principle 3 (Registry is the identity authority) now actually covers templates; today their identity lives only in template-store's Mongo indexes.

### 4.2 Uniqueness

Unchanged from v1: `(namespace, template_id, version)` and `(namespace, value, version)`. A specific version is addressed by name+version or UUID+version. **No per-version UUIDs** — see §3.

### 4.3 Identity is immutable — changes are forks

- **Rename is a fork.** The name *is* the identity; a template with a new name is a new entity. (Mirrors documents: identity-field value changes are rejected by PATCH; a changed identity is a new document.)
- **`identity_fields` are immutable across versions of one template.** Document identity must be comparable across template versions (that is what makes cross-version upsert work); versions with differing `identity_fields` would break the shared hash scope. Changing `identity_fields` therefore requires a fork (new `value`). Confirmed by Peter and enforced (create-as-upsert rejects with `identity_fields_immutable`; update rejects with fork guidance).

### 4.4 Document identity (recap, unchanged in mechanics)

Document identity scope is `(namespace, template, identity_hash)` — the *template entity*, never the template version. The Registry composite key for documents (`{namespace, identity_hash, template_id}`) already expresses this because `template_id` is the stable entity UUID; it must stay pointed at the entity, never at a version coordinate.

## 5. Write semantics

### 5.1 Template create is an upsert

Creating a template whose `(namespace, value)` already exists is not an error; it is a version event, exactly like a document upsert:

- byte-identical schema → `unchanged` (existing id/version returned)
- any difference → **new version**, loudly reported (impact analysis, §7.1), never blocking

This replaces the current default (`on_conflict=error` rejection; versioning only via `on_conflict=validate` for compatible diffs). Accidental version creation by agents is acknowledged as development pain, mitigated by the §7 tooling, not a design blocker.

### 5.2 Document writes: explicit-version validation, pin-to-what-validated

- A document write may pass an explicit `template_version` to validate against; default is latest-active. (Exists today; confirmed as designed v2 semantics, not an accident.) Domain example: a clinical sample is collected under the standard valid at collection time and its submissions carry that version forever.
- **Pin-to-what-validated (decided):** an upsert's new document version pins to the template version the incoming write validated against. Document version history may therefore span template versions. PATCH continues to validate against the document's pinned version.
- Migration between versions remains always explicit (`migrate_documents` dry-run/apply; freeze-as-lock cycle). No auto-migration, ever.

### 5.3 Version lifecycle (catalog semantics — unchanged)

Template versions coexist as active write targets; per-version activate/deactivate/draft, `reactivate_template`, and the validated migrate primitive continue as shipped. This is the one place templates deliberately do *not* mirror documents (whose versions are linear history).

## 6. Reporting architecture (separate workstream)

Principle: **the backend is honest about what it cannot do.** Cross-version schema reconciliation (e.g. a field split: `smoking: yes/no` → `packs_per_day`, `from`, `to`) is an app-developer decision; the platform never silently merges columns it cannot prove compatible. MongoDB remains full-fidelity ground truth regardless.

1. **Per-version tables are the physical default.** One table per `(template, version)`, named version-explicitly (e.g. `doc_sample__v3`). Own table always — including byte-identical schemas (simplicity over cleverness). Rationale: a unified table's `NULL` conflates "not in this row's schema version" with "submitted empty" — structural lossiness the split eliminates.
2. **Deterministic backfill:** a new version's table starts empty and fills from writes/migrations; `start_replay` is the explicit rebuild path. Never a silent backfill.
3. **Identity-core matview, always generated:** `identity_fields` are stable across versions (§4.3), so a cross-version registry view — identity fields + `document_id` + `template_version` + timestamps + provably-unchanged columns — is derivable with zero app input. This is the guaranteed floor: "which entities exist, latest state, which schema shape."
4. **Opt-in cross-version matview:** a template config item controls generation of a combined materialized view across selected versions; column mappings beyond the identity core come from declared renames (§7.2) or app-supplied definitions. Splits stay app territory.
5. **Family-first discovery:** `list_report_tables`, `get_table_view`, and MCP guidance present the entity → versions → matview structure so agent-written SQL does not silently miss rows in sibling version tables.
6. With `latest_only` sync, a document updated against a newer template version **moves** between version tables (consequence of §5.2 pin-to-what-validated).

## 7. Guardrail tooling

1. **Impact analysis at version-create** (the brake that makes §5.1 safe; load-bearing for Thesis 2): schema diff + live per-version document counts returned with the create response — "v3 drops `smoking`: 4,118 docs carry non-empty values; 213 empty and auto-migratable." Today a diff exists only on the bootstrap path (`on_conflict=validate`); plain version creation runs none.
2. **Declared renames:** a new version may annotate `new_field renames old_field`, making renames losslessly auto-migratable and matview-mappable. Without the annotation a rename is indistinguishable from drop+add. Splits/derivations are business logic and stay in apps.
3. **Migration offers** on the compatibility check: offered when (a) fields were added, (b) dropped fields are empty in all live docs, or (c) a combination.
4. **Dry-run validation against a candidate version** before creating it ("would my last 500 manifests validate against this draft v3?").

## 8. Documentation corrections

The pattern "new version → new ID" cited by PoNIF #2's corollary ("the exception to WIP's new-version → new-ID pattern") and by FIRESIDE-15's founding premise **does not exist** — no WIP entity mints per-version IDs. Corrections owed: PoNIF #2 phrasing, `docs/uniqueness-and-identity.md` framing, an annotation on the FIRESIDE-15 record, plus recording the §3 ontology principle and §2 vocabulary in the permanent docs.

## 9. What does NOT change

- ID semantics anywhere: stable entity UUIDs, integer version coordinates, for both documents and templates.
- Document → template reference shape (stable `template_id` + integer `template_version`); backup/restore unaffected.
- Synonym model: synonyms resolve to canonical entities only; no version-qualified synonyms.
- Version lifecycle primitives (deactivate-with-force, reactivate, validated migrate) and PATCH semantics.
- MongoDB document storage layout.

## 10. Workstreams

| # | Workstream | Case | Scope | Depends on |
|---|---|---|---|---|
| 1 | Identity core | CASE-709 | §4 registration, §5.1 create-as-upsert, fork rules, §5.2 test coverage | — |
| 2 | Reporting | CASE-710 | §6 entirely (design-then-implement) | — (coordinates exist today) |
| 3 | Guardrail tooling | CASE-711 | §7 | Workstream 1 (§5.1 semantics) |
| 4 | Documentation | CASE-712 | §8 | — |

Workstreams 1 and 2 proceed in parallel; the reporting stream is deliberately separated (different blast radius, own open specs, operationally the breaking one). All cases are edged to FIRESIDE-23 in the kb.
