# API Conventions

All WIP services follow a consistent set of API conventions. This document is the single source of truth for patterns used across Registry, Def-Store, Template-Store, and Document-Store. Reporting-Sync and Ingest Gateway have additional patterns (SQL queries, async job submission) not covered here.

---

## Bulk-First: Every Write Endpoint is Bulk

**Core principle: all write endpoints accept a JSON array and return a `BulkResponse`. Single operations are just `[item]`.**

There are no single-entity write endpoints. This eliminates the need for separate `/bulk` routes and ensures a uniform contract for all clients.

### Request Format

Every POST (create), PUT (update), and DELETE endpoint accepts:

```
Body: List[ItemRequest]    (via FastAPI Body(...))
```

For creates and updates, each item is the entity payload. For deletes, each item has `id` (and optionally `force`):

```json
// POST /api/def-store/terminologies — create one
[{"value": "GENDER", "label": "Gender", "namespace": "wip"}]

// POST /api/def-store/terminologies — create multiple
[
  {"value": "GENDER", "label": "Gender", "namespace": "wip"},
  {"value": "COUNTRY", "label": "Country", "namespace": "wip"}
]

// PUT /api/template-store/templates — update (ID in body, not URL)
[{"template_id": "019abc...", "label": "Updated Label"}]

// DELETE /api/def-store/terminologies — delete
[{"id": "019abc..."}, {"id": "019def...", "force": true}]
```

### Response Format: `BulkResponse`

Every write endpoint returns HTTP 200 with:

```json
{
  "results": [
    {
      "index": 0,
      "status": "created",
      "id": "019abc12-def3-7abc-..."
    },
    {
      "index": 1,
      "status": "error",
      "error": "Terminology with value 'GENDER' already exists"
    }
  ],
  "total": 2,
  "succeeded": 1,
  "failed": 1
}
```

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `results` | `List[BulkResultItem]` | Per-item results, in same order as input |
| `total` | `int` | Total items processed |
| `succeeded` | `int` | Items that succeeded |
| `failed` | `int` | Items that failed |

**Per-item `BulkResultItem`:**

| Field | Type | Description |
|-------|------|-------------|
| `index` | `int` | Position in the input array |
| `status` | `str` | `"created"`, `"updated"`, `"unchanged"`, `"deleted"`, `"skipped"`, `"error"` |
| `id` | `str?` | Generated or existing entity ID |
| `error` | `str?` | Error message (when `status == "error"`) |
| `error_code` | `str?` | Machine-readable error code (when `status == "error"`). See the PATCH section below for the canonical code set. |

Services extend `BulkResultItem` with extra fields:

- **Template-Store** adds: `value`, `version`, `is_new_version`
- **Document-Store** adds: `document_id`, `identity_hash`, `version`, `is_new`, `warnings`

### Key Rules

1. **Always HTTP 200** — errors are per-item in `results[i].status == "error"`, not HTTP status codes
2. **Never check HTTP status for business errors** — a 409 Conflict will never be returned; check `result.error` instead
3. **Updates use PUT with ID in the body** — `PUT /templates` with `[{"template_id": "...", ...}]`
4. **Deletes use DELETE with body** — `DELETE /terminologies` with `[{"id": "..."}]`
5. **GET endpoints are NOT bulk** — single-entity GET and paginated list GET stay as-is

---

## PATCH (Partial Update)

PATCH is the **partial-update** variant of bulk writes. Only `Document-Store` uses it today (`PATCH /api/document-store/documents`) — other services may adopt the same contract in future. The semantics below are the canonical WIP PATCH contract.

### Request Format

A JSON array of patch items:

```json
[
  { "document_id": "DOC-123", "patch": { "first_name": "Jane" } },
  { "document_id": "DOC-456", "patch": { "score": 92 }, "if_match": 3 }
]
```

**Item fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `document_id` | yes | Canonical UUID or registered synonym — resolved before processing |
| `patch` | yes | RFC 7396 JSON Merge Patch applied to the entity's `data` |
| `if_match` | no | Per-item optimistic concurrency control: if current version != `if_match`, the item fails with `concurrency_conflict` |

### RFC 7396 JSON Merge Patch Semantics

- Objects **deep-merge** with the existing data (recursively)
- Arrays are **replaced** wholesale (no per-element merging)
- `null` **deletes** the key from the merged result
- `null` on a required field fails validation for that item (error_code `validation_failed`)
- Empty patch `{}` or a patch that results in no change returns `status: "unchanged"` — no new version is created

### Response Format

Standard `BulkResponse`, HTTP 200. Per-item `status` values:

| Status | Meaning |
|--------|---------|
| `updated` | New version created |
| `unchanged` | No-op (empty patch, or merged document is byte-identical) — version not bumped |
| `error` | Item failed — `error_code` indicates the reason |

### Per-item `error_code`

Bulk-first endpoints extend `BulkResultItem` with an optional `error_code: str | None` field. It is set **only** when `status == "error"`, and it carries a machine-readable code so callers can branch on failure type without parsing message strings.

Codes defined by `PATCH /documents`:

| `error_code` | When |
|--------------|------|
| `not_found` | Document does not exist or has been soft-deleted |
| `forbidden` | Caller lacks write permission on the document's namespace |
| `archived` | Latest version has `status=ARCHIVED` — unarchive first |
| `identity_field_change` | Patch attempts to change a template-defined identity field — not allowed |
| `concurrency_conflict` | `if_match` mismatch, or internal version race lost after retries |
| `validation_failed` | Merged document fails template validation |
| `reference_violation` | Cross-namespace reference validation failed |
| `internal_error` | Unexpected exception (logged with stack trace) |

**New services adopting PATCH** should reuse the same set of codes where they apply and extend it with entity-specific codes only when necessary. Client libraries (`@wip/client`, MCP, WIP-Toolkit) expose `error_code` through their bulk-error types so callers can `instanceof`-check or switch on it.

### Key Rules (PATCH-specific)

1. **PATCH always creates a new version** on success — it is not an in-place mutation of the MongoDB document. The previous version stays in history.
2. **PATCH cannot change identity fields** — attempting to do so fails with `identity_field_change`. Use POST to create a new entity under a different identity.
3. **PATCH preserves `template_version` and `identity_hash`** — the new version validates against the template version recorded on the document, not the latest template version.
4. **PATCH reuses existing NATS event types** — e.g., `EventType.DOCUMENT_UPDATED`. Reporting-sync and other downstream consumers need no changes.

---

## Idempotent Bootstrap

App bootstrap scripts need to provision a namespace and a set of templates against any WIP instance and re-run cleanly. Two endpoints support this directly so callers don't have to fake idempotency with `GET → 404 → POST` dances.

### Namespace upsert — `PUT /api/registry/namespaces/{prefix}`

`PUT` is an upsert: it creates the namespace on missing and updates it when present. Any field omitted from the body uses the platform default on create (`isolation_mode='open'`, `deletion_mode='retain'`, `description=''`, `allowed_external_refs=[]`, `id_config=null`). On update, only fields explicitly supplied are touched.

```python
client.registry.upsert_namespace("my-app", {
    "description": "My App namespace",
    "isolation_mode": "strict",
})
```

The response is always `200 OK` with the resulting `NamespaceResponse`. Calling it twice with the same body is a no-op on the second call.

### Template create with conflict validation — `POST /api/template-store/templates?on_conflict=validate`

`POST /templates` accepts an `on_conflict` query parameter:

| Mode | Behavior on `(namespace, value)` collision |
|------|---------------------------------------------|
| `error` (default) | Returns a per-item `status: "error"`, `error: "Template with value '...' already exists ..."`. Existing behavior — backwards compatible. |
| `validate` | Schema-aware: identical → `unchanged`; compatible → `updated` (version N+1); incompatible → `error` with structured diff. |

In `validate` mode the per-item result reflects the verdict:

| Verdict | Status | Notes |
|---------|--------|-------|
| Identical schema | `unchanged` | Same `id` and `version` as the existing template. `details` carries the (empty) diff. |
| Compatible (added optional fields only) | `updated` | New version created; `is_new_version: true`, `version: N+1`. `details.added_optional` lists the added field names. |
| Incompatible | `error` | `error_code: "incompatible_schema"`. `details` contains: `removed`, `added_required`, `changed_type` (`{name, old_type, new_type}`), `made_required`, `modified_existing`, `identity_changed` (`{old, new}` or `null`). |

Compatibility is intentionally narrow: **only "added optional field" qualifies as compatible**. Any change to an existing field (label, description, validation, type, mandatory flag), removed field, added required field, or `identity_fields` change is incompatible. The structured diff lets the bootstrap script show the human a useful error.

```typescript
import { WipBulkItemError } from '@wip/client'

try {
  await client.templateStore.createTemplate(personDef, { onConflict: 'validate' })
} catch (e) {
  if (e instanceof WipBulkItemError && e.errorCode === 'incompatible_schema') {
    console.error('PERSON template drift:', e.details)
    process.exit(1)
  }
  throw e
}
```

---

## Client Code Examples

### Python (WIP-Toolkit / scripts)

```python
from wip_toolkit.client import WIPClient, WIPClientError

client = WIPClient(config)

# Create a terminology
result = client.post("def-store", "/terminologies", json=[{
    "value": "GENDER",
    "label": "Gender",
    "namespace": "wip",
}])
r = result["results"][0]
if r["status"] == "created":
    terminology_id = r["id"]
elif r["status"] == "error" and "already exists" in r.get("error", ""):
    print("Skipped — already exists")
else:
    raise RuntimeError(f"Failed: {r.get('error')}")

# Update a template
result = client.put("template-store", "/templates", json=[{
    "template_id": template_id,
    "label": "Updated Label",
}])
r = result["results"][0]
if r["status"] == "error":
    raise RuntimeError(r["error"])

# Delete terminologies
result = client.post("def-store", "/terminologies", json=[
    {"id": tid1},
    {"id": tid2, "force": True},
])
for r in result["results"]:
    if r["status"] == "error":
        print(f"Failed to delete item {r['index']}: {r['error']}")
```

> **Why POST for deletes?** HTTP DELETE with a JSON body is non-standard. The bulk-first pattern uses POST for all write operations — creates, updates, and deletes — so they all accept arrays and return BulkResponse.

### TypeScript (WIP Console UI)

The UI wraps bulk operations with convenience helpers:

```typescript
// api/client.ts — base helpers

protected async bulkWrite<T>(
  method: 'post' | 'put' | 'delete',
  url: string,
  items: T[]
): Promise<BulkResponse> {
  const response = await this.client.request<BulkResponse>({
    method,
    url,
    data: items,
  })
  return response.data
}

protected async bulkWriteOne<T>(
  method: 'post' | 'put' | 'delete',
  url: string,
  item: T
): Promise<BulkResultItem> {
  const resp = await this.bulkWrite(method, url, [item])
  const result = resp.results[0]
  if (result.status === 'error') {
    throw new Error(result.error || 'Operation failed')
  }
  return result
}
```

**Usage in API client methods:**

```typescript
// Create one terminology — wraps in array, unwraps single result
async createTerminology(data: CreateTerminologyRequest): Promise<BulkResultItem> {
  return this.bulkWriteOne('post', '/terminologies', data)
}

// Update one template — ID in body
async updateTemplate(data: UpdateTemplateRequest): Promise<BulkResultItem> {
  return this.bulkWriteOne('put', '/templates', data)
}

// Delete one term
async deleteTerm(id: string): Promise<BulkResultItem> {
  return this.bulkWriteOne('delete', '/terms', { id })
}
```

**Usage in stores (unwrap + re-fetch):**

```typescript
// stores/terminology.ts
async createTerminology(data: CreateTerminologyRequest) {
  const result = await defStoreClient.createTerminology(data)
  // Re-fetch the full entity after creation
  return await defStoreClient.getTerminology(result.id!)
}
```

### Common Mistake: Checking HTTP Status Codes

```python
# WRONG — bulk endpoints always return HTTP 200
try:
    result = client.post("def-store", "/terminologies", json=[payload])
except WIPClientError as e:
    if e.status_code == 409:  # This NEVER fires!
        print("Duplicate")

# CORRECT — check per-item result status
result = client.post("def-store", "/terminologies", json=[payload])
r = result["results"][0]
if r["status"] == "error" and "already exists" in r.get("error", ""):
    print("Duplicate")
```

---

## Pagination

All list (GET) endpoints use consistent pagination:

| Parameter | Default | Maximum | Description |
|-----------|---------|---------|-------------|
| `page` | 1 | — | Page number (1-based) |
| `page_size` | 50 | 100 | Items per page |

All list responses include:

```json
{
  "items": [...],
  "total": 150,
  "page": 1,
  "page_size": 50,
  "pages": 3
}
```

The `pages` field is computed as `ceil(total / page_size)`.

---

## Synonym Resolution

All service APIs accept **human-readable synonyms** wherever a canonical ID (UUID) is expected. This is transparent — callers can use either form interchangeably.

### How It Works

When a non-UUID identifier is passed (e.g., `template_id="PATIENT"` instead of `template_id="019abc..."`), the service resolves it via the Registry's synonym lookup before processing. Canonical UUIDs pass through without any Registry call.

Resolution happens at the API boundary (in the service's route handler) using `resolve_entity_id()` from `wip-auth`. A 5-minute TTL cache minimises latency for repeated lookups.

### Supported ID Fields

| Service | Fields that accept synonyms |
|---------|-----------------------------|
| **Def-Store** | `terminology_id` in term endpoints |
| **Template-Store** | `terminology_ref`, `template_ref`, `target_templates`, `target_terminologies` in template fields |
| **Document-Store** | `template_id` in document creation |

### Term Colon Notation

For term references, use `TERMINOLOGY:TERM_VALUE` notation:

```
STATUS:approved    → resolves to the term "approved" in terminology "STATUS"
COUNTRY:Germany    → resolves to the term "Germany" in terminology "COUNTRY"
```

### Best-Effort Semantics

Resolution is **best-effort** at the API boundary. If the Registry is unreachable, the synonym is not found, or no namespace context can be determined (multi-namespace key with `namespace` omitted), the raw value passes through unchanged. Namespace context comes from the `namespace` parameter, or implicitly from single-namespace API keys. This means:

- Existing code using canonical UUIDs continues to work unchanged
- Services degrade gracefully when the Registry is down
- Invalid synonyms are caught by downstream validation (e.g., "template not found"), not by the resolve layer

### Auto-Synonyms

Services automatically register synonyms when entities are created:

| Entity | Auto-synonym composite key |
|--------|---------------------------|
| Terminology | `{"ns": namespace, "type": "terminology", "value": "STATUS"}` |
| Term | `{"ns": namespace, "type": "term", "terminology": "STATUS", "value": "approved"}` |
| Template | `{"ns": namespace, "type": "template", "value": "PATIENT"}` |
| Document (with identity) | `{"ns": namespace, "type": "document", "template": "PATIENT", "identity_hash": "abc..."}` |

Auto-synonym registration is fire-and-forget — failures are logged but don't block entity creation.

For details, see `docs/design/universal-synonym-resolution.md`.

---

## Status Codes

| Code | When |
|------|------|
| **200** | All successful responses, including write operations (errors are per-item in BulkResponse) |
| **401** | Missing or invalid authentication |
| **404** | Entity not found (GET by ID) |
| **422** | Invalid request body (validation error from FastAPI) |
| **502** | Upstream service error (e.g., Registry unreachable from Template-Store) |

**Note:** 201 and 409 are never returned. All write results are communicated via `BulkResponse.results[i].status`.

---

## Authentication

All endpoints (except `/health`) require authentication:

- **API Key:** `X-API-Key: <key>` header
- **JWT:** `Authorization: Bearer <token>` header
- **Dual mode:** Either method accepted (configured via `WIP_AUTH_MODE`)

**Namespace scoping:** API keys for non-privileged accounts must include a `namespaces` field listing accessible namespace prefixes. Keys without namespace scoping that are not in `wip-admins` or `wip-services` groups have no access — all namespaces appear as 404 (the namespace's existence is not leaked). See `docs/authentication.md` for details.

**Implicit namespace derivation:** When an API key is scoped to exactly one namespace and the caller omits the `namespace` query parameter, the server derives namespace from the key's scope automatically. This enables synonym resolution without requiring `namespace` on every request. Multi-namespace keys must still provide `namespace` explicitly; omitting it means synonym resolution cannot determine context and raw values pass through unresolved.

---

## Soft Deletion

Data is never physically deleted. All delete operations set `status: "inactive"`. Entities can be restored via dedicated restore endpoints (e.g., `POST /terminologies/{id}/restore`).

**Exception: Files.** Binary files stored in MinIO support a two-step purge: soft-delete (`DELETE /files` → sets status to `inactive`) followed by hard-delete (`DELETE /files/{id}/hard` → permanently removes from MinIO and database). Hard-delete only works on files already in `inactive` status. This exists because binary storage has real cost, unlike soft-deleted MongoDB documents which are negligible.

---

## What is NOT Bulk

These endpoints remain single-entity or have special semantics:

| Endpoint | Reason |
|----------|--------|
| `GET /entity/{id}` | Single-entity read |
| `GET /entities?page=...` | Paginated list |
| `POST /files` | Multipart file upload (inherently single) |
| `POST /templates/{id}/activate` | Cascading operation, not CRUD |
| `POST /templates/{id}/cascade` | Cascading operation |
| `POST /validate`, `POST /validate/bulk` | Read-like operations |
| `POST /terms/{id}/restore` | Single restore by ID |
| `POST /documents/query` | Complex query |
