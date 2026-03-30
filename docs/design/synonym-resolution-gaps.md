# Synonym Resolution: Gap Analysis and Side Effects

**Status:** Analysis (2026-03-30, updated 2026-03-30). Companion to `universal-synonym-resolution.md`.
**Context:** The universal synonym resolution design is sound but incompletely implemented. This document audits the current state, identifies gaps, and analyzes the side effects of closing them.

---

## Design Principle (Restated)

All synonyms are equal for resolution purposes. There is exactly one **canonical ID** per entity — the one generated according to the namespace's ID configuration. This canonical ID is special only in two ways:

1. **Guaranteed to exist.** Every entity has one. Other synonyms are created for convenience (auto-synonyms) or by explicit registration, but are not guaranteed.
2. **Guaranteed to be stable.** The canonical ID never changes for a given entity. Synonyms can be added or removed; the canonical ID cannot.

The corollary: **querying with any valid synonym should behave identically to querying with the canonical ID.** If it doesn't, the system has a consistency gap.

---

## Current State: Where Resolution Actually Happens

### Registry (fully implemented)

`POST /resolve` — searches by `composite_key_hash` across ALL entries with **no namespace filter** (line 1020-1027 in `entries.py`). This is correct: the composite key itself contains the namespace, so the hash is globally unique. Resolution is truly global.

### wip-auth resolution layer (fully implemented)

`resolve_entity_id()` in `libs/wip-auth/src/wip_auth/resolve.py` — builds composite key from input + context (entity_type, namespace), resolves via Registry, caches with 5-min TTL. Batch variant exists. Available to all services.

### Service API boundaries (partially implemented)

| Endpoint | Uses `resolve_entity_id()`? | Notes |
|----------|:-:|-------|
| **def-store** `GET /terminologies/{id}` | Yes | Falls back to value lookup in MongoDB |
| **def-store** `POST /terminologies/{id}/terms` | Yes | Resolves terminology_id |
| **def-store** `GET /terminologies/{id}/terms` | Yes | Resolves terminology_id |
| **def-store** `GET /terms/{id}` | Yes | Resolves term_id |
| **template-store** `GET /templates/{id}` | Yes | Falls back to value lookup |
| **template-store** `GET /templates/{id}/raw` | Yes | Falls back to value lookup |
| **document-store** `POST /documents` | Yes | Resolves template_id per item |
| **document-store** `GET /documents` | Yes | Resolves template_id query param |

### Service internals (NOT implemented — the gap)

| Code path | What it does instead | Impact |
|-----------|---------------------|--------|
| **template-store** `_resolve_to_template_id()` | Queries MongoDB directly by `template_id`, falls back to `{namespace, value, status: active}` | Cross-namespace templates not found unless in specified namespace |
| **template-store** `_resolve_to_terminology_id()` | Calls def-store API by ID, falls back to by-value — ~~defaults to `namespace="wip"`~~ **Addressed (2026-03-30):** namespace is now required across the full stack; no more hardcoded "wip" default | ~~Hardcoded namespace; terminologies in other namespaces invisible~~ Namespace must be explicitly provided |
| **template-store** `_normalize_field_references()` | Calls the above two methods for every field reference | All template field references (terminology_ref, template_ref, target_templates, target_terminologies, extends) bypass Registry |
| **template-store** query param `?extends=` | Passed raw to MongoDB query | Cannot filter by synonym |
| **def-store** internal service methods | Direct MongoDB queries on canonical IDs only | After API-boundary resolution, all internal lookups assume canonical |

### Auto-synonym creation (partially implemented)

The design calls for auto-synonym registration at entity creation time. Current state:

- def-store: registers auto-synonyms for terminologies and terms (via Registry call after creation)
- template-store: registers auto-synonyms for templates
- document-store: registers auto-synonyms for documents

These registrations are best-effort (non-blocking, fire-and-forget). If the Registry is unavailable at creation time, the auto-synonym silently doesn't get created. The `backfill-synonyms` toolkit command exists as a safety net.

---

## The Core Gap: Template-Store Internal Resolution

`_resolve_to_terminology_id()` and `_resolve_to_template_id()` are called during:
- Template creation (normalizing field references)
- Template update (normalizing changed references)
- Template activation (resolving `extends` chains and field references)

These methods do **not** use the Registry. They query MongoDB/def-store directly with namespace-scoped lookups. This means:

1. A valid synonym registered in the Registry will not resolve through these code paths
2. Cross-namespace references only work if the target entity happens to be in the hardcoded default namespace ("wip") or the explicitly passed namespace
3. The "any synonym works anywhere" principle is violated for all template field references

This is the most impactful gap because template field references (`terminology_ref`, `template_ref`, `target_templates`, `target_terminologies`) are the backbone of WIP's data model. Every document is validated against these references.

---

## Side Effects of Fixing the Gap

### Proposed fix: Replace `_resolve_to_*` methods with `resolve_entity_id()`

Below is an analysis of every side effect, from minor to critical.

### 1. Loss of existence verification for canonical IDs

**Current:** `_resolve_to_template_id("some-uuid")` queries MongoDB: `Template.find({"template_id": ref})`. If the template doesn't exist, it falls through to value lookup, then raises `ValueError`. The canonical ID is verified to exist.

**After fix:** `resolve_entity_id()` checks `is_canonical_format()`. If the input looks like a UUID, it returns it as-is without verifying anything exists. A reference to a non-existent template passes resolution silently.

**Severity:** Low. The existence check still happens downstream — when the template is actually used (activation, document creation), the service will fail with a clear error. The error just surfaces at a different point. Also, in normal operation, references point to entities that exist; dangling references are an edge case, not a normal flow.

**Mitigation:** None needed. Existence is validated when the reference is used, not when it's resolved. Resolution and validation are separate concerns.

### 2. Activation-set resolution breaks (critical)

**Current:** `_resolve_to_template_id()` accepts a `known_templates: dict[str, str]` parameter — a mapping of `{value → template_id, template_id → template_id}` for templates being activated in the same batch. During batch activation, template A may extend template B, where B is also in the batch but not yet active. The `known_templates` dict handles this.

**After fix:** `resolve_entity_id()` has no concept of activation sets. It queries the Registry, which only finds active entries (query filter: `status: "active"`). A draft template's auto-synonym exists in the Registry but points to a "reserved" entry, which the resolver filters out.

**Severity:** Critical. Batch activation of interdependent templates would fail. This is a normal operation, not an edge case — ClinTrial has template inheritance chains.

**Root cause:** The Registry's resolve endpoint filters by `status: "active"`. Draft/reserved entities are invisible to resolution.

**Fix options:**

a) **Keep `known_templates` as a fast-path before calling `resolve_entity_id()`**. Resolution order: known_templates dict → Registry. This preserves current behavior while adding Registry resolution as a fallback. Pragmatic but adds a parallel resolution path that could diverge.

b) **Allow resolution of reserved/draft entries.** Add an optional `include_reserved: bool` parameter to the Registry's resolve endpoint. Template-store passes `include_reserved=True` during activation. This is the cleaner fix — the Registry becomes the single source of truth for all resolution.

c) **Register auto-synonyms as active at draft creation time.** Currently, auto-synonyms inherit the entry's status. If the synonym were independently "active" even while the entity is "reserved", resolution would find it. But this violates the principle that inactive entities shouldn't be discoverable.

**Recommendation:** Option (b). It keeps the Registry as the authoritative resolver while allowing template-store to see its own in-progress work. The `include_reserved` parameter should default to `false` — normal resolution excludes drafts, but internal service operations (activation, import) can opt in.

### 3. Namespace resolution semantics change (fundamental)

**Current (pre-2026-03-30):** `_resolve_to_terminology_id(ref, namespace="wip")` tried a single, explicit namespace. The caller controlled which namespace to search. Default was "wip" — a pragmatic choice that handled the 95% case (shared terminologies). **Update (2026-03-30):** The hardcoded `namespace="wip"` default has been removed across the full stack (backend, @wip/client, WIP-Toolkit, Console UI, scripts). Namespace is now always explicitly provided by the caller. This eliminates the silent "wip" fallback but does not yet solve the general cross-namespace resolution problem described below.

**After fix with `resolve_entity_id()`:** The resolver builds a composite key using a single namespace: `{ns: namespace, type: "terminology", value: ref}`. If the terminology lives in a different namespace, it's not found.

**The remaining problem:** ~~The current code hardcodes `namespace="wip"`.~~ The resolver would use the caller's namespace. ~~Neither~~ This does not handle the general case: "find this entity regardless of which namespace it's in."

**Why this matters for WIP consistency (not just import):** Consider an app namespace "clintrial" whose templates reference terminologies in the "wip" namespace AND in a "shared-medical" namespace. Today, `_resolve_to_terminology_id()` hardcodes "wip", so "shared-medical" references silently fail. With `resolve_entity_id()` using the caller's namespace "clintrial", both "wip" and "shared-medical" references fail unless the terminology also exists in "clintrial".

**The right design:** Resolution should follow the namespace's isolation rules:

```
resolve_entity_id(ref, entity_type, namespace) should:
  1. Try {ns: namespace} (own namespace — always searched)
  2. If not found and isolation_mode is "open":
     Try {ns: "wip"} (base namespace — always allowed in open mode)
  3. If not found:
     Try each namespace in allowed_external_refs list
  4. If still not found: raise EntityNotFound
```

This mirrors how `reference_validator.py` already validates cross-namespace access. The resolution layer should apply the same rules.

**Severity:** Fundamental. This isn't a side effect — it's the design question that must be answered before the fix can proceed. Getting this wrong means either:
- Too restrictive: legitimate cross-namespace references fail
- Too permissive: entities leak across namespace isolation boundaries

**Implementation note:** The namespace isolation config is in the Registry (namespace model). `resolve_entity_id()` would need to fetch and cache namespace config to know the search order. This adds a dependency: resolution now needs namespace configuration, not just the Registry resolve endpoint.

### 4. Registry becomes a hard dependency for template operations

**Current:** Template creation and activation need MongoDB (direct access) and def-store (HTTP). Both are existing dependencies. Registry is used only at the API boundary (optional — canonical IDs bypass it).

**After fix:** Every template field reference resolution requires a Registry call. Template creation, update, and activation all go through the Registry.

**Impact:**
- Registry unavailable → template creation fails (today: succeeds as long as MongoDB and def-store are up)
- Added latency: ~2ms per resolution on localhost (cached after first call)
- Added failure mode in distributed deployments

**Severity:** Medium. Registry is a core service and should always be running. The TTL cache provides 5-minute resilience. But changing a service from "works without Registry" to "requires Registry" is a meaningful architectural shift.

**Mitigation:** The cache already handles brief Registry outages. For extended outages, template-store could fall back to direct MongoDB lookup (degraded mode). But this re-introduces the parallel resolution path we're trying to eliminate.

**Recommendation:** Accept the dependency. Registry is foundational infrastructure — no different from MongoDB or NATS. If Registry is down, the system is already degraded (no ID generation, no synonym resolution at API boundaries). Making template internals also depend on Registry is consistent.

### 5. Exception type changes

**Current:** `_resolve_to_terminology_id()` and `_resolve_to_template_id()` raise `ValueError` on not-found.

**After fix:** `resolve_entity_id()` raises `EntityNotFoundError` (from wip_auth).

**Impact:** All callers that catch `ValueError` from these methods will miss the new exception. In `_normalize_field_references()` and template activation code, unhandled exceptions would propagate differently.

**Severity:** Medium. Silent behavior change — errors may surface as 500s instead of 400s.

**Fix:** Either:
- Catch `EntityNotFoundError` in the callers and convert to `ValueError` (backward-compatible)
- Update all callers to catch both (transitional)
- Update all callers to catch `EntityNotFoundError` (clean but requires auditing every call site)

### 6. Performance: N sequential HTTP calls change target

**Current:** `_normalize_field_references()` iterates template fields sequentially. Each `_resolve_to_terminology_id()` call is one HTTP GET to def-store. Each `_resolve_to_template_id()` call is one MongoDB query (local).

**After fix:** Each resolution is one HTTP call to Registry (or cache hit).

**Opportunity:** `_normalize_field_references()` could be restructured to collect all references first, batch-resolve via `resolve_entity_ids()`, then apply results. This would reduce N HTTP calls to 1, regardless of whether the target is Registry or def-store.

**Severity:** Low (performance improvement opportunity, not a regression). The number of HTTP calls stays the same; only the target changes.

### 7. Cache coherence during creation flows

**Scenario:** Create a terminology, then immediately create a template referencing it.

**Current:** Template-store queries def-store by value. Def-store queries MongoDB. The terminology was just inserted, so it's found immediately.

**After fix:** Template-store calls `resolve_entity_id()`, which checks the cache (miss), then calls Registry `/resolve`. The auto-synonym was registered moments ago. If the Registry write hasn't been acknowledged yet, or if there's a replication delay, the resolution fails.

**Severity:** Low in practice (MongoDB writes are acknowledged before the API returns; auto-synonym registration happens in the same request). But this is a new race condition that didn't exist before.

**Mitigation:** Auto-synonym registration should be synchronous (awaited) during entity creation, not fire-and-forget. This is already the case for most services but should be verified.

---

## Summary: Fix Feasibility

The proposed fix ("replace `_resolve_to_*` with `resolve_entity_id()`") is the right direction but cannot be done as a simple swap. Three prerequisites must be addressed first:

| Prerequisite | Why | Effort |
|-------------|-----|--------|
| **Namespace-aware resolution order** | Current resolver uses one namespace; cross-namespace references need a search order based on isolation_mode and allowed_external_refs | Medium — requires namespace config lookup in resolution layer |
| **Reserved/draft entity resolution** | Batch activation of interdependent templates needs to see draft entries | Small — add `include_reserved` parameter to Registry resolve endpoint |
| **Exception handling update** | All callers of `_resolve_to_*` methods catch ValueError; new code raises EntityNotFoundError | Small — mechanical change, but requires auditing all call sites |

Once these are in place, the swap is straightforward. The remaining side effects (Registry dependency, cache coherence, performance) are acceptable trade-offs for a consistent resolution model.

---

## Recommended Implementation Order

1. **Add namespace-aware resolution to `resolve_entity_id()`**
   - Fetch and cache namespace config (isolation_mode, allowed_external_refs)
   - Implement search order: own namespace → "wip" (if open mode) → allowed_external_refs
   - This is a change to `wip_auth/resolve.py`, not to any service

2. **Add `include_reserved` to Registry resolve endpoint**
   - Default `false`, opt-in for internal service operations
   - Change to `components/registry/src/registry/api/entries.py`

3. **Update `resolve_entity_id()` to accept `include_reserved` parameter**
   - Pass through to Registry call

4. **Replace `_resolve_to_template_id()` in template-store**
   - Keep `known_templates` as a fast-path before `resolve_entity_id()`
   - Update exception handling in all callers
   - Update `_normalize_field_references()` to batch-resolve where possible

5. **Replace `_resolve_to_terminology_id()` in template-store**
   - ~~Remove hardcoded `namespace="wip"` default~~ Done (2026-03-30) — namespace is now required across all layers
   - Pass template's own namespace; let resolution layer handle cross-namespace search

6. **Verify auto-synonym registration is synchronous**
   - Audit all services to ensure auto-synonyms are awaited, not fire-and-forget
   - If any are fire-and-forget, make them synchronous

7. **Test cross-namespace reference scenarios**
   - Template in namespace A references terminology in namespace B
   - Batch activation with interdependent templates (extends chains)
   - Resolution during import when entities are in reserved state

---

## Relationship to Fast Bulk Transfer

This analysis was prompted by the fast bulk transfer design, but the gaps identified here are **WIP consistency issues**, not import/export issues. The resolution layer should work correctly for normal operations first:

- Template creation should resolve cross-namespace references through the Registry
- Batch activation should resolve interdependent templates through the Registry
- All ID lookups should honor the namespace isolation model

Once WIP's internal resolution is consistent, import/export follows naturally: import goes through the API, references resolve correctly, no special-case remapping needed. A sane internal design eliminates the need for import-time hacks.

The fast bulk transfer design should be revisited after the resolution layer is complete. The performance optimization (direct MongoDB insert) is still valid, but the reference handling strategy depends on having a working resolution layer.
