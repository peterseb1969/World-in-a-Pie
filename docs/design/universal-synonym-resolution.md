# Universal Synonym Resolution

**Status:** Proposed — needs discussion
**Dependency for:** Cross-instance restore (v1.1), dev→prod namespace workflow, MCP ergonomics

---

## Problem

Every WIP API operation requires the canonical entity ID (UUID7 or namespace-configured format). The Registry synonym system can store arbitrary alternative identifiers for any entity, but these synonyms are isolated from the operational data plane — you can look them up, but you can't use them to _do_ things.

This creates three concrete pain points:

1. **Cross-instance migration breaks references.** When entities are exported from instance A and imported into instance B, they get new canonical IDs. Inter-entity references (template_ref, terminology_ref, parent_class, file_references) use the old canonical IDs and break. The toolkit works around this with value-based remapping for terminologies and retry loops for documents, achieving 99% success — but the remaining 1% (14/1384 documents in testing) fails because document-to-document references can't be remapped without ID pass-through.

2. **App development requires unnecessary roundtrips.** An AI building an app via MCP knows the template _value_ (`PATIENT`) but needs the template _ID_ (`TPL-01abc...`) to create documents. This forces a lookup-then-act pattern on every operation: `get_template_by_value` → extract ID → `create_document`. The same applies to terminology values, term values, and any other human-readable identifier.

3. **No portable identity across instances.** A canonical ID is meaningful only on the instance that generated it. External systems (other WIP instances, integration pipelines, third-party tools) that store WIP entity references lose them the moment data moves. There's no instance-independent way to say "this entity" that survives export/import.

---

## Current State

### Synonym infrastructure (already built)

- Registry supports multiple composite keys per entity (`POST /entries/{id}/synonyms`)
- Lookup by any synonym is fast: hash-based, O(1) per key
- Synonyms are unique — no two entities can share the same composite key
- Export includes synonyms (`_fetch_synonyms` in toolkit exporter)
- The MCP server has `add_synonym`, `remove_synonym`, `lookup_entry` tools

### What's missing

| Capability | Status |
|-----------|--------|
| Synonyms stored and queryable | Done |
| Synonyms accepted as entity IDs in service APIs | Not implemented |
| Synonyms included in inter-entity references | Not implemented |
| Synonyms auto-created for portable identity | Not implemented |
| Batch synonym resolution for bulk operations | Not implemented |

---

## Proposed Design

### Core idea

Allow any registered synonym to be used wherever a canonical ID is accepted. Services resolve non-canonical identifiers through the Registry before processing.

### Resolution flow

```
Client sends request with ID "X"
  → Service receives ID "X"
  → Is "X" in canonical format? (UUID7 with entity prefix, or namespace-configured)
    → Yes: proceed as today
    → No: call Registry to resolve synonym → get canonical ID
      → Found: substitute canonical ID, proceed
      → Not found: return 404 "Entity not found"
```

### Resolution layer: wip-auth middleware

The resolution logic lives in `wip-auth` (shared by all services), not in each service individually. This is the same pattern used for API key verification and namespace permissions.

```python
# libs/wip-auth/src/wip_auth/resolve.py

async def resolve_entity_id(raw_id: str, entity_type: str | None = None) -> str:
    """Resolve a synonym or canonical ID to the canonical ID.

    Returns raw_id unchanged if it's already canonical.
    Raises EntityNotFound if synonym lookup fails.
    """
    if is_canonical_format(raw_id):
        return raw_id

    # Check cache first
    cached = _resolution_cache.get(raw_id)
    if cached:
        return cached

    # Registry lookup
    canonical = await registry_client.resolve_synonym(raw_id, entity_type)
    if not canonical:
        raise EntityNotFound(f"No entity found for identifier: {raw_id}")

    _resolution_cache.set(raw_id, canonical, ttl=300)
    return canonical
```

For bulk operations, a batch variant avoids N individual lookups:

```python
async def resolve_entity_ids(raw_ids: list[str]) -> dict[str, str]:
    """Batch resolve. Returns {raw_id: canonical_id} for all inputs."""
    to_resolve = [rid for rid in raw_ids if not is_canonical_format(rid)]
    if not to_resolve:
        return {rid: rid for rid in raw_ids}

    resolved = await registry_client.batch_resolve_synonyms(to_resolve)
    return {rid: resolved.get(rid, rid) for rid in raw_ids}
```

### Registry API additions

```
POST /api/registry/resolve
  Body: [{"identifier": "X", "entity_type": "template"}, ...]
  Response: [{"identifier": "X", "entry_id": "TPL-01abc...", "status": "found"}, ...]
```

This is a read-only batch endpoint optimised for resolution. It does not create entries.

### Synonym types for portable identity

Define a convention for deterministic, human-readable synonyms that survive export/import:

| Entity type | Portable synonym composite key |
|------------|-------------------------------|
| Terminology | `{namespace, "terminology", value}` |
| Term | `{namespace, "term", terminology_value, term_value}` |
| Template | `{namespace, "template", value, version}` |
| Document | `{namespace, "document", template_value, identity_hash}` |
| File | `{namespace, "file", filename, content_hash}` |

These could be registered automatically at entity creation ("auto-synonyms") or on-demand before export. Auto-registration is simpler but increases Registry size. On-demand is more conservative.

### Cross-instance migration with synonyms

```
Instance A (source):
  1. Register portable synonyms for all entities in namespace
  2. Export namespace (synonyms included in archive)

Instance B (target):
  3. Import entities — new canonical IDs assigned
  4. Register portable synonyms from archive
  5. All inter-entity references use portable synonyms → resolve correctly
  6. Optional: batch-update references to use new canonical IDs
  7. Optional: delete portable synonyms (or keep for future re-export)
```

Step 5 is where universal resolution is critical. When Document-Store validates that `parent_class: "{wip, document, DND_CLASS, abc123}"` points to an existing document, it resolves the synonym to the local canonical ID.

### What changes per service

| Service | Change |
|---------|--------|
| **wip-auth** | New `resolve.py` module with caching, `resolve_entity_id()` and batch variant |
| **Registry** | New `POST /resolve` endpoint for batch synonym resolution |
| **Def-Store** | Resolve `terminology_id` and `term_id` parameters through wip-auth before processing |
| **Template-Store** | Resolve `template_id`, `extends` references through wip-auth |
| **Document-Store** | Resolve `document_id`, `template_id`, reference field values (template_ref, file_references, doc-to-doc references) |
| **MCP Server** | No change needed — resolution is transparent at the service level |

### Performance considerations

**Latency:** One Registry HTTP call per non-canonical ID, cached with TTL. On a Pi, a Registry call is ~2ms over localhost. The cache eliminates repeat lookups within the TTL window.

**Bulk operations:** The batch resolve endpoint handles 100+ IDs in a single call. For a bulk import of 1000 documents, one batch call resolves all referenced IDs — not 1000 individual lookups.

**Cache invalidation:** Synonyms are immutable once created (no updates, only add/remove). A TTL cache (5 minutes) is sufficient — stale cache entries resolve to the same canonical ID or to a removed synonym (which correctly fails).

**Canonical ID fast path:** If the ID is already in canonical format, no Registry call is made. Existing code paths that use canonical IDs see zero overhead.

---

## How This Solves the Problems

**Problem 1 (migration):** Portable synonyms provide instance-independent identity. References survive export/import because the synonym resolves on both instances, even though canonical IDs differ.

**Problem 2 (roundtrips):** An app developer or AI can use `template_value` or a custom synonym directly in API calls. No lookup-then-act pattern needed. `create_document({"template_id": "PATIENT", ...})` just works if "PATIENT" is a registered synonym for the template.

**Problem 3 (portable identity):** The portable synonym composite key is deterministic — it can be computed from entity metadata without knowing the canonical ID. External systems can reference WIP entities by portable synonym and survive instance migrations.

---

## Interaction with Existing Features

**Draft entity mode (roadmap):** Complementary. Draft mode skips referential integrity during import; universal resolution makes integrity checks work _with_ synonyms. Draft mode is simpler for the import path; universal resolution is broader.

**Namespace deletion:** No conflict. Deleting a namespace removes its entities and their synonyms.

**Namespace authorization:** Resolution respects namespace permissions. Resolving a synonym for an entity in a namespace you can't access returns "not found."

**Bulk-first API convention:** Batch resolution aligns naturally. The resolve endpoint is itself bulk-first.

**Event replay:** Events use canonical IDs internally. No change needed — resolution is at the API boundary, not in the event stream.

---

## Not in Scope

- **Synonym-based query filters.** This design covers ID resolution (point lookups), not `WHERE synonym LIKE 'X%'` queries. That's a different feature.
- **Automatic synonym cleanup.** If portable synonyms are created for migration, they persist until explicitly removed. Automated lifecycle management is a future enhancement.
- **Synonym conflict resolution across namespaces.** Composite keys include namespace, so conflicts between namespaces are impossible by construction.
- **GraphQL or alternative API surface.** This design applies to the existing REST API.

---

## Open Questions

1. **Auto-synonyms vs on-demand?** Registering a portable synonym at entity creation time is simpler (always available) but doubles the Registry entry count. On-demand (e.g., `wip-toolkit export --with-synonyms`) is more conservative. Leaning toward on-demand for now, auto-synonyms as a later enhancement.

2. **Resolution in reference field validation.** When Document-Store validates a `template_ref` or `parent_class` field value, should it resolve synonyms in the submitted _data_, or only in URL/query parameters? Resolving in data is more powerful (enables the migration pattern) but means Document-Store must inspect and potentially rewrite field values before storage. Should the canonical ID be stored, or the synonym?

3. **Store canonical or synonym?** If a document is created with `template_id: "PATIENT"` (a synonym), should the stored document have `template_id: "TPL-01abc..."` (resolved) or `template_id: "PATIENT"` (as submitted)? Storing canonical is consistent with today's behaviour and avoids cascading resolution. Storing the synonym makes the document more portable but every read would need resolution.

4. **Backwards compatibility.** All existing code uses canonical IDs. The resolution layer is additive — canonical IDs pass through unchanged. But services that currently validate ID format (e.g., "must match UUID7 pattern") would need to relax that check for synonym inputs.
