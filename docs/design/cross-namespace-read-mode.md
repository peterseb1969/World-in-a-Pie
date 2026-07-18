# Cross-Namespace Read Mode

**Status:** Design (2026-04-04). Implements CASE-08.
**Dependency for:** React Console "All namespaces" mode, any admin UI.

---

## Problem

Every list/read endpoint requires `namespace` as a query parameter. When omitted, some endpoints already call `resolve_accessible_namespaces()` and filter accordingly (terminologies, terms, templates, documents). But many endpoints require namespace as mandatory (`Query(...)`) and return 422 when it's missing. The Console's "All namespaces" mode has no uniform server-side support.

## Current State (2026-04-04)

### Already support namespace-optional (the `allowed_namespaces` pattern)

These endpoints already make namespace optional and use `resolve_accessible_namespaces()`:

| Endpoint | File |
|----------|------|
| `GET /terminologies` | `def-store/api/terminologies.py:66` |
| `GET /terminologies/{id}/terms` | `def-store/api/terms.py:126` |
| `GET /templates` | `template-store/api/templates.py:98` |
| `GET /documents` | `document-store/api/documents.py:121` |
| `POST /documents/query` | `document-store/api/documents.py:359` |

Pattern at API layer:
```python
if namespace:
    await check_namespace_permission(identity, namespace, "read")
else:
    allowed_namespaces = await resolve_accessible_namespaces(identity)

service.list_X(namespace=namespace, allowed_namespaces=allowed_namespaces, ...)
```

Pattern at service layer:
```python
query = {}
if namespace:
    query["namespace"] = namespace
elif allowed_namespaces is not None:
    query["namespace"] = {"$in": allowed_namespaces}
# else: superadmin — no filter
```

### Still require namespace as mandatory (`Query(...)`)

| Endpoint | File | Count |
|----------|------|-------|
| `GET /ontology/parents/{term_id}` | `def-store/api/ontology.py:34` | 8 endpoints |
| `GET /ontology/children/{term_id}` | `def-store/api/ontology.py:69` | |
| `GET /ontology/ancestors/{term_id}` | `def-store/api/ontology.py:105` | |
| `GET /ontology/descendants/{term_id}` | `def-store/api/ontology.py:134` | |
| `GET /ontology/term-relations` | `def-store/api/ontology.py:187` | |
| `POST /ontology/term-relations` | `def-store/api/ontology.py:218` | |
| `DELETE /ontology/term-relations` | `def-store/api/ontology.py:247` | |
| `GET /ontology/term-relations/all` | `def-store/api/ontology.py:270` | |
| `POST /import` | `def-store/api/import_export.py:163` | 1 endpoint |
| `GET /files` | `document-store/api/files.py:151` | 1 endpoint |
| `POST /files` (upload) | `document-store/api/files.py:84` | 1 endpoint |

**Total: 11 mandatory-namespace endpoints that need updating.**

---

## Design

### Principle: namespace stays optional at API, permission check moves to a shared helper

The pattern that already works in 5 endpoints becomes the universal pattern. One helper function handles both modes:

```python
# New helper in wip_auth/permissions.py
async def resolve_namespace_filter(
    identity: UserIdentity,
    namespace: str | None,
    required: Literal["read", "write", "admin"] = "read",
) -> NamespaceFilter:
    """Resolve namespace into a query filter.

    If namespace is provided: check permission, return single-namespace filter.
    If namespace is None: resolve accessible namespaces, return multi-namespace filter.

    Returns a NamespaceFilter with:
      - query: dict to merge into MongoDB query — always {} (superadmin) or {"namespace": {"$in": [...]}}
      - namespaces: the resolved list (for logging/debugging), or None for superadmin

    Two branches only: empty dict or $in list. Single-namespace is just $in with one element
    (MongoDB optimizes this). No special-casing needed in callers.
    """
    if namespace:
        await check_namespace_permission(identity, namespace, required)
        return NamespaceFilter(query={"namespace": {"$in": [namespace]}}, namespaces=[namespace])

    accessible = await resolve_accessible_namespaces(identity)
    if accessible is None:
        # Superadmin: no filter
        return NamespaceFilter(query={}, namespaces=None)
    if not accessible:
        # No access to any namespace
        raise HTTPException(403, "No accessible namespaces")
    return NamespaceFilter(query={"namespace": {"$in": accessible}}, namespaces=accessible)


@dataclass
class NamespaceFilter:
    query: dict          # Merge into MongoDB query
    namespaces: list[str] | None  # None = superadmin (all)
```

### Why a helper instead of changing each endpoint individually

1. **Single point of change.** If the permission model evolves, one function changes.
2. **Consistent error handling.** Empty accessible list → 403. Invalid namespace → 404. Same everywhere.
3. **Testable.** One unit test covers the filter logic; endpoints just merge `ns_filter.query` into their query dict.
4. **Reduces regression risk.** Endpoints don't implement permission branching — they call the helper and merge the result.

---

## Implementation Plan

### Phase 1: Add the helper (no endpoint changes)

**File:** `libs/wip-auth/src/wip_auth/permissions.py`

Add `NamespaceFilter` dataclass and `resolve_namespace_filter()` function. Export from `wip_auth`.

**Tests:** Unit test in wip-auth covering:
- Superadmin → empty query
- Admin key with namespaces → `$in` filter
- Single-namespace key → single filter
- No access → 403
- Explicit namespace → permission check + single filter

**Commit 1.** Safe — no behavioral changes.

### Phase 2: Migrate existing `allowed_namespaces` endpoints

Convert the 5 endpoints that already work to use the new helper. This is a refactor — behavior is identical, code is simpler.

**Before:**
```python
allowed_namespaces = None
if namespace:
    await check_namespace_permission(identity, namespace, "read")
else:
    allowed_namespaces = await resolve_accessible_namespaces(identity)

result = await service.list_X(namespace=namespace, allowed_namespaces=allowed_namespaces, ...)
```

**After:**
```python
ns_filter = await resolve_namespace_filter(identity, namespace)

result = await service.list_X(ns_filter=ns_filter.query, ...)
```

**Service layer change:** Replace `namespace` + `allowed_namespaces` parameters with a single `ns_filter: dict` parameter that gets merged into the query. This is the key simplification — the service layer no longer needs to know about the three cases.

```python
# Before (in service):
async def list_terminologies(self, ..., namespace=None, allowed_namespaces=None):
    query = {}
    if namespace:
        query["namespace"] = namespace
    elif allowed_namespaces is not None:
        query["namespace"] = {"$in": allowed_namespaces}

# After:
async def list_terminologies(self, ..., ns_filter: dict | None = None):
    query = {}
    if ns_filter:
        query.update(ns_filter)
```

**Endpoints to migrate (5):**
1. `GET /terminologies` — def-store/api/terminologies.py
2. `GET /terminologies/{id}/terms` — def-store/api/terms.py
3. `GET /templates` — template-store/api/templates.py
4. `GET /documents` — document-store/api/documents.py
5. `POST /documents/query` — document-store/api/documents.py

**Commit 2.** Behavioral no-op — same logic, cleaner code. Run all tests after.

### Phase 3: Make ontology endpoints namespace-optional

The 8 ontology endpoints currently require `namespace: str = Query(...)`. Change to `namespace: str | None = Query(default=None)` and add the `resolve_namespace_filter` call.

**Important nuance:** Ontology traversal (ancestors, descendants) starts from a specific term. The term has a namespace. For traversal endpoints, namespace could be:
- **Provided:** Filter results to that namespace only
- **Omitted:** Use the term's own namespace (current CASE-07 fix approach)
- **Cross-namespace traversal:** Follow relations across namespaces the identity can access

**Simplification (from cross-agent review):** Don't branch on admin/partial/single in endpoint code. `resolve_namespace_filter()` returns either an empty dict (superadmin, no filter) or `{"namespace": {"$in": [...]}}`. The `$in` with a single-element list works identically to `{"namespace": "ns1"}` — MongoDB optimizes this. Two branches, not three.

**Decision needed:** Should ontology traversal cross namespace boundaries?

**Proposed answer: No, not yet.** Ontology relations are namespace-scoped today. Cross-namespace ontology is a separate design item (namespace isolation modes). For now:
- `GET /ontology/term-relations` and `/relations/all` → make namespace optional, apply `resolve_namespace_filter` (these are list endpoints)
- Traversal endpoints (`parents`, `children`, `ancestors`, `descendants`) → keep namespace required (they need a starting namespace for the traversal algorithm)

**Endpoints to change (2 list + 0 traversal):**
1. `GET /ontology/term-relations` — list, make optional
2. `GET /ontology/term-relations/all` — list, make optional

**Endpoints to leave as-is (6):**
- `GET /ontology/parents/{term_id}` — traversal, namespace required
- `GET /ontology/children/{term_id}` — traversal, namespace required
- `GET /ontology/ancestors/{term_id}` — traversal, namespace required
- `GET /ontology/descendants/{term_id}` — traversal, namespace required
- `POST /ontology/term-relations` — write, namespace required (scopes the new relation)
- `DELETE /ontology/term-relations` — write, namespace required

**Commit 3.** Small scope — only 2 endpoints change.

### Phase 4: Make file list endpoint namespace-optional

`GET /files` currently requires namespace. Change to optional with `resolve_namespace_filter`.

The file service layer needs the same `ns_filter` pattern. Check how file queries are built.

`POST /files` (upload) keeps namespace required — you're creating a file, you must specify where.

**Commit 4.** 1 endpoint.

### Phase 5: Add `namespace` field to all list response items

Verify that every list response includes the `namespace` field on each item. When querying across namespaces, the caller must know which namespace each result belongs to. This should already be the case (namespace is a model field), but verify.

**Commit 5.** Verification only — likely no code changes.

---

## What This Does NOT Change

1. **Write endpoints** — `POST /terminologies`, `POST /templates`, `POST /documents`, `POST /files`, `POST /ontology/term-relations` — all require explicit namespace. You must know where you're writing.

2. **Get-by-ID endpoints** — `GET /terminologies/{id}`, `GET /templates/{id}`, `GET /documents/{id}`, `GET /files/{id}` — these retrieve a specific entity. Namespace is not needed (ID is globally unique). Permission check happens post-fetch against the entity's namespace.

3. **Ontology traversal endpoints** — `parents`, `children`, `ancestors`, `descendants` — namespace required for the traversal algorithm.

4. **Import/export** — namespace required (you're importing into a specific namespace).

5. **Value-based lookups** — `GET /terminologies/by-value/{value}`, `GET /templates/by-value/{value}` — namespace required (values are only unique within a namespace).

---

## Regression Risk Assessment

| Phase | Risk | Why | Mitigation |
|-------|------|-----|------------|
| 1 (helper) | None | New code, no callers yet | Unit tests |
| 2 (migrate existing) | Low | Same behavior, different code path | Run all component tests. Endpoints already work this way. |
| 3 (ontology) | Low | Only 2 list endpoints change. Traversal untouched. | Run def-store tests. |
| 4 (files) | Low | 1 endpoint, straightforward pattern | Run document-store tests. |
| 5 (verify response) | None | Read-only audit | No code changes expected. |

**The biggest regression risk is Phase 2** — changing the service layer signatures from `(namespace, allowed_namespaces)` to `(ns_filter)`. Every test that calls service methods directly will need updating. But the behavior is identical, so failures are compilation errors (wrong args), not logic bugs.

**Total endpoint changes:** 8 out of ~80+ endpoints.
**Total service method changes:** 5 methods (list_terminologies, list_terms, list_templates, list_documents, query_documents).

---

## MongoDB Index Impact

All indexes are compound with namespace as the leading field:
```
(namespace, terminology_id)
(namespace, value)
(namespace, template_id, version)
(namespace, document_id, version)
(namespace, template_id, status)
```

**When namespace is omitted (superadmin):** MongoDB cannot use the leading namespace prefix. It will use an index scan (not a collection scan — other fields in the compound index still help). For small-to-medium datasets (< 100K documents), this is negligible.

**When namespace is `$in`:** MongoDB uses the index efficiently — it performs one range scan per namespace in the `$in` list. For N=5 namespaces, that's 5 index lookups merged. This is well-optimized in MongoDB.

**No new indexes needed.**

---

## Testing Strategy

### Unit tests (Phase 1)
- `resolve_namespace_filter(superadmin, None)` → empty query
- `resolve_namespace_filter(scoped_key, None)` → `$in` filter
- `resolve_namespace_filter(any_user, "wip")` → single filter + permission check
- `resolve_namespace_filter(no_access_user, None)` → 403

### Integration tests (Phase 2-4)
- For each migrated endpoint:
  1. Call with explicit `namespace=wip` → same results as before
  2. Call without namespace, admin key → results from all namespaces
  3. Call without namespace, scoped key → results only from scoped namespaces
  4. Call without namespace, no-access key → 403

### Security contract test (mandatory gate — implement once, run for all services)
Create entities in namespace A and namespace B. Query as identity with access to A only.
- **Must see:** all entities in namespace A
- **Must NOT see:** any entity in namespace B
- **Must see correct count:** pagination total reflects only accessible entities

This is the critical test. If this passes, the filter is correct. If it fails, the change leaks data across namespaces.

### Regression test
- Run full test suite after each phase: `./scripts/wip-test.sh all`
- 735+ tests must pass at every phase boundary
- Existing tests all pass `namespace=X` explicitly — they exercise the backward-compatible path. Any failure = regression in the `$in` wrapping.

---

## Rollout Order

From cross-agent review: don't do all 4 services at once. Start with the simplest, validate, propagate.

1. **template-store** — 1 list endpoint, smallest test surface. Validate in Console.
2. **def-store** — 2 list endpoints (terminologies, terms) + 2 ontology list endpoints.
3. **document-store** — 1 list endpoint + 1 file list + query endpoint.
4. **Console validation** — verify "All namespaces" mode works end-to-end after each service.

Each service is a separate commit with full test suite run between them.

---

## Cross-Agent Review Notes (2026-04-04)

FranC (FR-YAC) reviewed the design and raised concerns about blast radius, caching, and test strategy. Key outcomes:

- **7 endpoints, not 30.** The existing `allowed_namespaces` pattern covers the hard part. Fear was overblown.
- **Console already handles "All = undefined."** `currentNamespaceParam` returns `undefined` for "All" mode. No breaking change.
- **Caching already solved.** `resolve_accessible_namespaces()` has 30s TTL, production-tested across 5 endpoints.
- **Two branches, not three.** `$in` with single element = same as equality. Simplifies all code paths.
- **Template-store first.** Smallest service, easiest to validate, limits blast radius.
- **Security contract test is mandatory.** Create in ns A and B, query as user with A-only access, must not see B.

Phase 2 (refactor existing endpoints) is the riskiest step due to test churn on service method signatures, but since tests hit real MongoDB, wrong filter shapes fail loudly — not silently.

---

## Relation to Other Work

- **CASE-07 (Console term detail):** Fixed by using term's own namespace. Not affected by this design.
- **Namespace-scoped API keys:** Prerequisite. Done (1e3f508).
- **Cross-namespace resolution (synonym-resolution-gaps.md):** Independent. This design is about *querying* across namespaces, not *resolving references* across namespaces.
- **Document update endpoint:** Independent.
- **React Console:** Consumer of this feature. "All namespaces" mode in the namespace selector will work once this is implemented.
