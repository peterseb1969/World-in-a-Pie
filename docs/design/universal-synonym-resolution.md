# Universal Synonym Resolution

**Status:** Design complete — ready for implementation (2026-03-27)
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
- Synonyms are unique — no two entities can share the same composite key hash
- Composite key hash uniqueness is enforced **globally** (not per-namespace) — the `$or` query in `entries.py` checks `primary_composite_key_hash` and `synonyms.composite_key_hash` without a namespace filter
- Export includes synonyms (`_fetch_synonyms` in toolkit exporter)
- The MCP server has `add_synonym`, `remove_synonym`, `lookup_entry` tools

### What's missing

| Capability | Status |
|-----------|--------|
| Synonyms stored and queryable | Done |
| Synonyms accepted as entity IDs in service APIs | Not implemented |
| Auto-synonyms created at entity creation | Not implemented |
| Inter-entity references stored as synonyms | Not implemented |
| Synonym namespace rewriting on import | Not implemented |
| Batch synonym resolution for bulk operations | Not implemented |

---

## Design Decisions (All Resolved)

### D1: Auto-synonyms at creation time (not on-demand)

**Decision:** Register a human-readable synonym automatically when every entity is created.

**Rationale:** Auto-synonyms make every entity immediately addressable by its human-readable value. The cost is one additional synonym per entity, which is negligible — synonyms are small embedded documents within the Registry entry, not separate collections. On-demand registration (e.g., only before export) would mean synonyms aren't available for API ergonomics during normal operation, defeating half the purpose.

### D2: Keep namespace in the composite key

**Decision:** Include namespace in all auto-synonym composite keys to ensure global uniqueness.

**Rationale:** The Registry enforces composite key hash uniqueness globally, not per-namespace. Two namespaces with a template named `ADDRESS` would collide if namespace were excluded. Including namespace makes collisions impossible by construction.

### D3: Namespace rewriting on import (not namespace-free keys)

**Decision:** When importing into a different namespace, the import tool rewrites the namespace component in all synonym composite keys and recomputes their hashes.

**Rationale:** There are two ways to handle namespace portability:

- _Namespace-free keys:_ Remove namespace from synonyms → collides across namespaces on the same instance
- _Namespace in key + rewrite on import:_ Collision-proof at rest, portable via a single-pass transformation

The rewrite approach keeps synonyms collision-proof during normal operation and concentrates the portability logic in one place (the import tool). The import tool already knows source and target namespace, so the rewrite is trivial:

```
Export from "wip":
  Template ADDRESS → synonym: {ns: "wip", type: "template", value: "ADDRESS"}
  Document references template via synonym composite key containing ns: "wip"

Import into "clintrials":
  Import tool rewrites: {ns: "wip", ...} → {ns: "clintrials", ...}
  Recomputes hashes
  Registers synonyms against new canonical IDs
  All inter-entity references already rewritten → resolve correctly
```

### D4: Store canonical IDs internally, resolve at API boundary

**Decision:** Services always store canonical IDs. Synonym resolution happens at the API entry point — synonyms are resolved to canonical IDs before any business logic runs.

**Rationale:** Storing synonyms in documents would mean every read path needs resolution (cascading complexity). Storing canonical IDs keeps internal data consistent with today's behaviour. The resolution layer is purely additive — a translation step at the API boundary.

### D5: Endpoint context resolves entity type ambiguity

**Decision:** When a synonym string is used in an API call, the entity type is inferred from the endpoint context. `/api/def-store/terminologies/STATUS` resolves `STATUS` as a terminology synonym. The generic `POST /resolve` batch endpoint accepts an explicit `entity_type` parameter for cases without endpoint context.

**Rationale:** In practice, every API call targets a specific entity type. Requiring a prefix like `tpl:ADDRESS` adds syntax that developers and AI agents would need to learn. Endpoint-inferred resolution is zero-ceremony.

### D6: Colon notation for term references (not context-sensitive)

**Decision:** When referencing a term by synonym in an API parameter, always use `"TERMINOLOGY:TERM"` colon notation (e.g., `"STATUS:approved"`). Context-sensitive resolution (inferring the terminology from field definitions) is explicitly rejected.

**Rationale:** Context-sensitive APIs create ambiguity and are hard to debug. If a term synonym works in one context but fails in another (because the terminology can't be inferred), the developer gets inconsistent behaviour that's difficult to diagnose. Colon notation is unambiguous, self-documenting, and works in every context.

**Important distinction:** This applies to **API parameters that accept entity IDs** (e.g., `term_id` in URL/query params). It does **not** apply to **document field data values**. When a document stores `"approved"` as a term field value, that's the term's value string — not a Registry synonym reference. Term validation in Document-Store already resolves term values within the field's `terminology_ref` context. This existing behaviour is unchanged.

### D7: Additive change (not breaking)

**Decision:** Universal synonym resolution is a purely additive API enhancement. All existing API calls using canonical IDs continue to work unchanged.

**Rationale:**
- Every endpoint continues to accept canonical IDs exactly as today. Zero behaviour change for existing callers.
- Synonym resolution is attempted _only_ when the input doesn't match canonical format. Existing code that passes `TPL-01abc...` never hits the resolution path.
- Responses still return canonical IDs. A client that sends `template_id="PATIENT"` gets back a document with `template_id: "TPL-01abc..."`. The response format doesn't change.
- Auto-synonyms are registered silently alongside entity creation. No new required parameters, no changed response shapes.
- wip-client already accepts `string` for all ID parameters — it works without code changes. Documentation and examples should be updated to show synonym usage.
- MCP server needs zero changes — it passes IDs through to service APIs. AI agents immediately benefit.

### D8: Backfill script for existing data

**Decision:** Provide a one-time `wip-toolkit backfill-synonyms` command that registers auto-synonyms for all existing entities. Run as part of version upgrade procedure.

**Rationale:** Lazy registration (write-on-read) mixes reads and writes, introduces race conditions, and adds complexity to every read path. Forward-only (only new entities get synonyms) creates a confusing inconsistency. A backfill script is simple, idempotent (checks for existing synonyms before registering), and means all entities are immediately usable with the new resolution after upgrade.

### D9: No synonym display in UI by default

**Decision:** Auto-synonyms are not shown in terminology/template/document detail views. They are visible in the Registry entries view (which is already a technical/admin view) where composite keys are already displayed.

**Rationale:** Auto-synonyms are a technical mechanism. Most users don't need to see them. Developers debugging resolution issues can check the Registry view. If demand emerges, a collapsible "Registry Info" section can be added to entity detail views later.

### D10: Resolution before format validation

**Decision:** Services that validate ID format (e.g., "must start with TPL-") must attempt synonym resolution first, then validate the resolved canonical ID.

**Rationale:** With synonym resolution, a `template_id` parameter could be `"PATIENT"` — which would fail upfront format validation. The flow becomes:

1. Receive raw ID from client
2. Is it canonical format? → Yes: proceed. No: resolve via synonym.
3. After resolution, the canonical ID is guaranteed to be in the correct format.
4. Proceed with business logic using canonical ID.

Existing API calls using canonical IDs are unaffected — they take the fast path (step 2 → Yes) and never hit resolution.

---

## Auto-Synonym Definitions

Every entity receives a human-readable synonym at creation time. The composite key includes namespace for global uniqueness and entity type for cross-type disambiguation.

| Entity | Auto-synonym composite key | Example |
|--------|---------------------------|---------|
| Terminology | `{ns, type: "terminology", value}` | `{ns: "wip", type: "terminology", value: "STATUS"}` |
| Term | `{ns, type: "term", terminology, value}` | `{ns: "wip", type: "term", terminology: "STATUS", value: "approved"}` |
| Template | `{ns, type: "template", value}` | `{ns: "wip", type: "template", value: "PATIENT"}` |
| Document (with identity) | `{ns, type: "document", template, identity_hash}` | `{ns: "wip", type: "document", template: "PATIENT", identity: "a1b2c3"}` |
| Document (without identity) | `{ns, type: "document", portable_id}` | `{ns: "wip", type: "document", portable_id: "019..."}` |
| File | `{ns, type: "file", filename, content_hash}` | `{ns: "wip", type: "file", filename: "scan.pdf", content: "d4e5f6"}` |

### API string representation

When using a synonym in an API call, the developer provides a string that the resolution layer maps to a composite key:

| Entity | API string | Composite key built by resolver |
|--------|-----------|-------------------------------|
| Terminology | `"STATUS"` | `{ns: <current>, type: "terminology", value: "STATUS"}` |
| Term | `"STATUS:approved"` | `{ns: <current>, type: "term", terminology: "STATUS", value: "approved"}` |
| Template | `"PATIENT"` | `{ns: <current>, type: "template", value: "PATIENT"}` |

Documents and files are not typically referenced by synonym string in API calls — they use canonical IDs or are found by search. Their synonyms exist for migration portability.

### Documents without identity fields

Most documents don't have identity fields — they're one-off records that received a unique canonical ID from Registry at creation. These have no natural human-readable key. For migration portability, a random UUID7 is assigned as the `portable_id` component. This UUID is meaningless to humans but stable across export/import: it survives namespace rewriting (only the `ns` component changes) and maps to whatever new canonical ID the target Registry assigns.

For API ergonomics, these documents are typically found by search or by following references from other documents, not by typing an ID. The portable UUID7 synonym serves migration, not developer convenience.

### Templates and versioning

Template auto-synonyms resolve to the **entity_id** (stable across versions), not a specific version. This means `{ns: "wip", type: "template", value: "PATIENT"}` always resolves to the template entity, and the service applies latest-active-version semantics as usual.

For the rare case where a specific version is needed (e.g., `extends_version` pinning), a version-qualified synonym `{ns, type: "template", value, version}` can be registered on demand. This is not auto-created — it's an explicit opt-in.

---

## Resolution Flow

### Single ID resolution

```
Client sends request with ID "X"
  → Service receives ID "X"
  → Is "X" in canonical format? (matches entity prefix pattern, e.g., TPL-*, DOC-*, TERM-*)
    → Yes: proceed as today (zero overhead)
    → No: parse synonym string
      → Contains ":"? → split into parent:child (term notation)
      → Build composite key from endpoint context (entity type) + current namespace
      → Check resolution cache (TTL 5 min)
      → Cache miss: Registry lookup by composite_key_hash
        → Found: cache result, substitute canonical ID, proceed
        → Not found: return 404 "Entity not found"
```

### Resolution layer: wip-auth middleware

The resolution logic lives in `wip-auth` (shared by all services), not in each service individually. This is the same pattern used for API key verification and namespace permissions.

```python
# libs/wip-auth/src/wip_auth/resolve.py

async def resolve_entity_id(
    raw_id: str,
    entity_type: str,
    namespace: str,
) -> str:
    """Resolve a synonym or canonical ID to the canonical ID.

    Returns raw_id unchanged if it's already canonical.
    Raises EntityNotFound if synonym lookup fails.
    """
    if is_canonical_format(raw_id):
        return raw_id

    # Build the composite key for this entity type
    composite_key = _build_composite_key(raw_id, entity_type, namespace)
    cache_key = f"{namespace}:{entity_type}:{raw_id}"

    # Check cache first
    cached = _resolution_cache.get(cache_key)
    if cached:
        return cached

    # Registry lookup
    canonical = await registry_client.resolve_synonym(composite_key)
    if not canonical:
        raise EntityNotFound(f"No entity found for identifier: {raw_id}")

    _resolution_cache.set(cache_key, canonical, ttl=300)
    return canonical


def _build_composite_key(raw_id: str, entity_type: str, namespace: str) -> dict:
    """Build a composite key from a synonym string and context.

    Term references use colon notation: "TERMINOLOGY:TERM_VALUE"
    All other entity types use the plain value.
    """
    if entity_type == "term" and ":" in raw_id:
        terminology, value = raw_id.split(":", 1)
        return {
            "ns": namespace,
            "type": "term",
            "terminology": terminology,
            "value": value,
        }

    return {
        "ns": namespace,
        "type": entity_type,
        "value": raw_id,
    }
```

For bulk operations, a batch variant avoids N individual lookups:

```python
async def resolve_entity_ids(
    raw_ids: list[str],
    entity_type: str,
    namespace: str,
) -> dict[str, str]:
    """Batch resolve. Returns {raw_id: canonical_id} for all inputs."""
    to_resolve = [rid for rid in raw_ids if not is_canonical_format(rid)]
    if not to_resolve:
        return {rid: rid for rid in raw_ids}

    composite_keys = [
        _build_composite_key(rid, entity_type, namespace)
        for rid in to_resolve
    ]
    resolved = await registry_client.batch_resolve_synonyms(composite_keys)
    result = {rid: rid for rid in raw_ids}  # canonical IDs pass through
    result.update(resolved)
    return result
```

### Registry API additions

```
POST /api/registry/resolve
  Body: [{"composite_key": {"ns": "wip", "type": "template", "value": "ADDRESS"}}, ...]
  Response: [{"composite_key": {...}, "entry_id": "TPL-01abc...", "status": "found"}, ...]
```

This is a read-only batch endpoint optimised for resolution. It does not create entries.

---

## How References Work End-to-End

### The reference chain today (fragile)

```
Terminology "STATUS" created       → TERM-000001 (canonical ID)
Term "approved" created            → T-000042 (canonical ID)
Template "PATIENT" created         → TPL-01abc (canonical ID)
  field: status (terminology_ref: "TERM-000001")     ← breaks on migration
Document created against PATIENT
  template_id: "TPL-01abc"                           ← breaks on migration
  data.status: "approved"                            ← already portable (term value)
  data.primary_doctor: "DOC-00089"                   ← breaks on migration
```

### The reference chain with auto-synonyms (portable)

```
Terminology "STATUS" created       → TERM-000001
  auto-synonym: {ns:"wip", type:"terminology", value:"STATUS"} → TERM-000001

Term "approved" created            → T-000042
  auto-synonym: {ns:"wip", type:"term", terminology:"STATUS", value:"approved"} → T-000042

Template "PATIENT" created         → TPL-01abc
  auto-synonym: {ns:"wip", type:"template", value:"PATIENT"} → TPL-01abc
  field: status (terminology_ref: "STATUS")          ← resolved via synonym at runtime

Document created
  API call: create_document(template_id="PATIENT")   ← resolved via synonym
  Stored: template_id: "TPL-01abc"                   ← canonical ID stored internally
  data.status: "approved"                            ← term value, already portable
  data.primary_doctor: "DOC-00089"                   ← canonical ID stored
    (the referenced doc also has a portable_id synonym for migration)
```

### Migration with namespace rewriting

```
Export from "wip":
  Archive contains entities + all synonym composite keys

Import into "clintrials":
  1. Import tool scans all synonym composite keys in the archive
  2. Rewrites {ns: "wip", ...} → {ns: "clintrials", ...} in all composite keys
  3. Recomputes composite key hashes for rewritten keys
  4. Entities get new canonical IDs from clintrials Registry
  5. Rewritten synonyms registered against new canonical IDs
  6. Inter-entity references (terminology_ref: "STATUS") resolve via
     {ns: "clintrials", type: "terminology", value: "STATUS"} → new canonical ID
  7. Document-to-document references: stored canonical IDs are rewritten
     using the portable_id synonym mapping (old canonical → portable_id → new canonical)
```

---

## What Changes Per Service

### Phase 1: Auto-synonym registration

Each service registers an auto-synonym when creating an entity. This is a single additional call to Registry after the entity is created.

| Service | Change |
|---------|--------|
| **Def-Store** | After creating terminology/term, register auto-synonym composite key |
| **Template-Store** | After creating template, register auto-synonym composite key |
| **Document-Store** | After creating document, register auto-synonym composite key (identity-hash based or portable UUID7) |

### Phase 2: Resolution layer + Registry endpoint

| Service | Change |
|---------|--------|
| **wip-auth** | New `resolve.py` module with caching, `resolve_entity_id()`, batch variant, `_build_composite_key()` with colon notation parsing |
| **Registry** | New `POST /resolve` endpoint for batch synonym resolution |

### Phase 3: Service integration

| Service | Change |
|---------|--------|
| **Def-Store** | Resolve `terminology_id` and `term_id` parameters through wip-auth before processing |
| **Template-Store** | Resolve `template_id`, `extends` references through wip-auth |
| **Document-Store** | Resolve `template_id` in document creation/query. Resolve reference field values (template_ref, terminology_ref in field definitions) |
| **MCP Server** | No code change needed — resolution is transparent at the service level. AI agents immediately benefit. |

### Phase 4: Import tool namespace rewriting

| Component | Change |
|-----------|--------|
| **WIP-Toolkit importer** | When target namespace differs from source: rewrite `ns` component in all synonym composite keys, recompute hashes, build old-canonical → new-canonical mapping via portable_id synonyms, rewrite stored canonical ID references |
| **WIP-Toolkit exporter** | No change needed — already exports synonyms |

### Phase 5: Backfill + documentation

| Component | Change |
|-----------|--------|
| **WIP-Toolkit** | New `backfill-synonyms` command: iterate all entities, register auto-synonyms for any that don't have one. Idempotent. |
| **wip-client** | Update JSDoc and examples to show synonym usage. No API changes. |
| **Documentation** | Update API docs, MCP docs, app setup guide to document synonym resolution |

---

## Developer / AI Ergonomics

With universal resolution, API calls become significantly simpler:

### Before (lookup-then-act)

```python
# AI agent needs 3 calls to create a document
template = await mcp.get_template_by_value("PATIENT")
terminology = await mcp.get_terminology_by_value("STATUS")
doc = await mcp.create_document(
    template_id=template["template_id"],  # "TPL-01abc..."
    data={"status": "approved"}
)
```

### After (direct reference)

```python
# AI agent needs 1 call
doc = await mcp.create_document(
    template_id="PATIENT",  # resolved via auto-synonym
    data={"status": "approved"}
)
```

### Term references with colon notation

```python
# Explicit, unambiguous term reference
term = await mcp.get_term("STATUS:approved")

# Terminology reference
terminology = await mcp.get_terminology("STATUS")

# Template reference
template = await mcp.get_template("PATIENT")
```

---

## Performance Considerations

**Latency:** One Registry HTTP call per non-canonical ID, cached with TTL. On a Pi, a Registry call is ~2ms over localhost. The cache eliminates repeat lookups within the TTL window.

**Bulk operations:** The batch resolve endpoint handles 100+ IDs in a single call. For a bulk import of 1000 documents all referencing the same template, one batch call resolves all unique referenced IDs — typically just a handful.

**Cache invalidation:** Synonyms are immutable once created (no updates, only add/remove). A TTL cache (5 minutes) is sufficient — stale cache entries resolve to the same canonical ID or to a removed synonym (which correctly fails).

**Canonical ID fast path:** If the ID is already in canonical format (matches entity prefix pattern), no Registry call is made. Existing code paths that use canonical IDs see zero overhead.

**Registry entry size:** Auto-synonyms add one embedded `Synonym` object per entity. At ~200 bytes per synonym, 10,000 entities adds ~2MB to the Registry collection. Negligible.

**Registry as a dependency:** With synonym resolution, non-canonical API calls require Registry to be available. This is an accepted trade-off: Registry is a core service that must always be running. The TTL cache (5 min) provides resilience against brief Registry outages. For distributed deployments where Registry runs on a separate machine, developers should prefer canonical IDs in performance-critical paths and rely on synonyms for ergonomic/interactive use.

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
- **Automatic synonym cleanup.** Synonyms persist until explicitly removed. Automated lifecycle management is a future enhancement.
- **GraphQL or alternative API surface.** This design applies to the existing REST API.
- **Version-pinned template synonyms.** Auto-synonyms resolve to the entity (latest version). Version-specific synonyms can be registered manually if needed.
- **Context-sensitive term resolution.** Term synonyms always require `TERMINOLOGY:TERM` colon notation. No implicit terminology inference from field definitions or other context.
