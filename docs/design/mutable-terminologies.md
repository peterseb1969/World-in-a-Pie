# Design: Mutable Terminologies

**Status:** Design complete, not started

## Motivation

WIP terminologies are designed as stable, curated reference vocabularies — terms can be deprecated but never truly deleted. This is correct for shared institutional knowledge (country codes, ontologies, status values) but wrong for app-scoped user-defined vocabularies.

The ClinTrial app exposed the gap: users need to extend a curated therapeutic area hierarchy with custom classifications. The current options are:

1. **Extend the terminology** — but `extensible: false` blocks it, terms can't be deleted, and user changes pollute shared reference data
2. **Use documents as a terminology overlay** — works, but reinvents terminology semantics (identity, hierarchy, merge logic) in every app

This will recur. Any app that needs user-editable controlled vocabularies will face the same choice between abusing terminologies or rebuilding them from documents.

**Solution:** Add a `mutable: true` flag on terminologies. Mutable terminologies allow term deletion and behave as app-scoped, freely editable vocabularies while using the full terminology infrastructure (ontology relationships, reporting-sync, MCP tools, ontology browser).

## Design Principles

1. **Zero impact on existing terminologies.** `mutable` defaults to `false`. All current behavior is unchanged.
2. **Opt-in per terminology.** Set at creation time. Immutable by default, mutable when explicitly requested.
3. **Same API, relaxed constraints.** Mutable terms use the same endpoints, same events, same reporting pipeline. The only difference is that deletion is a real delete, not a deprecation.
4. **App-scoped by convention.** Mutable terminologies are expected to live in app namespaces, not shared ones. Not enforced — just documented guidance.

## Model Changes

### Terminology

Add `mutable: bool = False` to the Terminology model in Def-Store.

```python
class Terminology(Document):
    # ... existing fields ...
    mutable: bool = False        # NEW: allows term hard-delete
    extensible: bool = False     # existing: allows adding new terms
```

`mutable: true` implies `extensible: true` — you can't delete terms from a vocabulary you can't add to. Def-Store enforces this: if `mutable=True` and `extensible=False`, auto-set `extensible=True`.

The `mutable` flag is set at creation and **cannot be changed** after terms exist. Changing a mutable terminology to immutable would leave deleted-term references dangling. Changing immutable to mutable would retroactively alter the contract for existing consumers.

### Term Deletion

Today, `DELETE /api/def-store/terms` sets `status: deprecated` and requires `deprecated_reason` and optionally `replaced_by_term_id`.

For mutable terminologies, the same endpoint performs a **hard delete**:
- Remove the term document from MongoDB
- Cascade: delete all `TermRelationship` records where the term is source or target
- Emit `term.deleted` event (existing event type, same payload)
- Registry: mark the entry as inactive (existing behavior)

The endpoint checks `terminology.mutable` before deciding the delete mode. No new endpoint needed.

### Event Handling

No new events. The existing `term.deleted` event already exists and is handled by:

| Consumer | Current Behavior | Change Needed |
|----------|-----------------|---------------|
| **Reporting-sync** | Sets `status = 'inactive'` in PG `terms` table | None — correct for both modes |
| **Reporting-sync** | Relationship cascade in PG | Add: DELETE from `term_relationships` where source or target matches |
| **MCP Server** | Thin wrapper, passes through | None |
| **Ingest Gateway** | No term handling | None |

### API Changes

| Endpoint | Change |
|----------|--------|
| `POST /api/def-store/terminologies` | Accept `mutable` field |
| `GET /api/def-store/terminologies` | Return `mutable` field |
| `DELETE /api/def-store/terms` | Hard delete if `terminology.mutable`, else deprecate (current behavior) |
| `PUT /api/def-store/terminologies` | Reject `mutable` field changes if terms exist |

### Reporting-Sync

The `terms` metadata table in PostgreSQL already has a `status` column. When a mutable term is deleted:
1. `term.deleted` event fires (same as today)
2. Reporting-sync sets `status = 'inactive'` (same as today)
3. **New:** cascade DELETE from `term_relationships` table where `source_term_id` or `target_term_id` matches

No schema changes. The hard delete happens in MongoDB; PostgreSQL reflects it as inactive status. This is intentional — the reporting layer preserves history.

### WIP-Toolkit

Export: include `mutable` flag in terminology JSON. Already round-trips arbitrary fields.

Import (fresh): respect `mutable` flag when creating terminologies. No special handling needed — the flag is just another field on the create request.

Import (restore): same as fresh for this field.

### Console UI

The terminology detail page should show the `mutable` flag. Term delete button behavior doesn't change from the user's perspective — the backend decides deprecate vs hard-delete based on the flag.

Consider a visual indicator (e.g., tag or icon) distinguishing mutable from immutable terminologies in the list view.

### MCP Server

No changes. The `delete_term` tool calls the same API endpoint. The `create_terminology` tool already passes through all fields.

### Documentation

- Update `docs/data-models.md` — add `mutable` to Terminology model
- Update `docs/api-conventions.md` — document delete behavior difference
- Add guidance to `docs/WIP_AppSetup_Guide.md` — when to use mutable vs immutable

## Implementation Plan

### Phase 1: Core (Def-Store)

1. Add `mutable` field to Terminology model
2. Modify term delete endpoint: check `terminology.mutable`, hard delete + relationship cascade if true
3. Add validation: `mutable=True` implies `extensible=True`
4. Add validation: reject `mutable` changes on terminologies with existing terms
5. Tests: mutable term CRUD, cascade, immutable behavior unchanged

### Phase 2: Downstream

6. Reporting-sync: cascade relationship DELETE on `term.deleted` for mutable terms
7. WIP-Toolkit: include `mutable` in export/import
8. Console: show `mutable` indicator, create dialog option
9. Documentation updates

## Risk Assessment

| Area | Risk | Mitigation |
|------|------|------------|
| Existing terminologies | Zero — `mutable` defaults to `false` | No behavior change unless opted in |
| Reporting-sync | Low — already handles `term.deleted` | Add relationship cascade (small change) |
| MCP Server | None — thin wrapper | No changes needed |
| WIP-Toolkit | Low — new field passthrough | Standard import/export pattern |
| Ontology relationships | Moderate — cascade delete must be correct | Test: delete term → all its relationships gone |
| `@wip/client` | Low — delete method exists | May need `mutable` in Terminology type |
| Consumer apps | Low — mutable terminologies are new, no existing consumers | n/a |

## What This Enables

- **ClinTrial:** User-defined therapeutic areas as a mutable terminology with `is_a` relationships to the curated CT_THERAPEUTIC_AREA. Full ontology browser support, no document overlay hack.
- **Any app** needing user-editable picklists, tags, categories, or classifications — use a mutable terminology instead of reinventing the pattern with documents.
- **Pattern:** Curated base terminology (immutable) + user extension terminology (mutable), merged at the app level by combining both term sets. The ontology browser shows the full merged hierarchy automatically.

## Open Questions

1. **Should mutable terms be hard-deleted from PostgreSQL too?** Current design keeps them as `status='inactive'` for audit trail. Could add a `hard_delete` option later if needed.
2. **Should `mutable` imply namespace-scoped?** Tempting to enforce, but unnecessary — the convention is sufficient.
3. **Terminology-level delete:** Should deleting a mutable terminology also hard-delete all its terms? Current namespace deletion already handles this — probably yes for consistency.
