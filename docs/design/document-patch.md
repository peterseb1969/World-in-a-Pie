# Document Patch — Partial Updates via `PATCH /documents`

**Status:** Design complete, not started
**Related:** `docs/api-conventions.md`, `docs/uniqueness-and-identity.md`, `docs/design/reference-fields.md`
**Fireside report:** `BE-YAC-20260407-2119/report-document-patch-design.md`

## Problem

Documents in WIP are immutable per version. To change a field on an existing document, the caller today must:

1. `GET /documents/{id}` to fetch the full document
2. Modify fields client-side
3. `POST /documents` with all the original identity fields preserved, so the server creates version N+1 via identity dedup

This is cumbersome and error-prone:

- The caller must know which fields are identity fields (template-defined) and preserve them exactly
- The caller must send the entire `data` blob even when changing one scalar
- A race between GET and POST silently overwrites concurrent edits
- AI agents naturally think "update field X" but must learn the WIP-specific dance
- Every update is a two round-trip operation
- React Console cannot offer a "save changes" workflow without this

## Solution

A new bulk endpoint, `PATCH /documents`, that performs server-side partial updates.

The caller sends a list of `{document_id, patch}` items. The server reads each document, deep-merges the patch into the current `data`, re-validates against the template, and creates version N+1. The result is a `BulkResponse` with per-item success/error status, matching every other write endpoint in WIP.

## API

### Endpoint

```
PATCH /documents
Content-Type: application/json

[
  {
    "document_id": "DOC-123",
    "patch": { "status": "approved", "score": 92 },
    "if_match": 3
  },
  {
    "document_id": "DOC-456",
    "patch": { "tags": ["red", "urgent"] }
  }
]
```

| Field | Type | Required | Description |
|---|---|---|---|
| `document_id` | string | yes | The document to patch |
| `patch` | object | yes | Partial data following RFC 7396 (JSON Merge Patch) |
| `if_match` | int | no | Expected current version. If provided and the current version differs, the item fails with a conflict error |

### Response

```
HTTP/1.1 200 OK
Content-Type: application/json

{
  "results": [
    {
      "status": "ok",
      "document": { "document_id": "DOC-123", "version": 4, ... }
    },
    {
      "status": "error",
      "document_id": "DOC-456",
      "error": { "code": "validation_failed", "message": "..." }
    }
  ]
}
```

The endpoint always returns 200 OK, with per-item status. This matches `docs/api-conventions.md` (bulk-first contract).

Per-item status values:
- `ok` — patch applied, returns the full new document version
- `error` — patch failed, returns the error code and message

### Per-item error codes

| Code | When | HTTP analog |
|---|---|---|
| `not_found` | Document does not exist or caller has no read access | 404 |
| `forbidden` | Caller has no write permission on the document's namespace | 403 |
| `archived` | Document is archived; unarchive first | 409 |
| `identity_field_change` | Patch attempts to modify a template-defined identity field | 400 |
| `namespace_change` | Patch attempts to modify the namespace | 400 |
| `concurrency_conflict` | `if_match` was provided and the current version is different | 409 |
| `validation_failed` | Merged document fails template validation | 422 |
| `term_not_found` | Patched value does not resolve to any term in the field's terminology | 422 |
| `file_not_found` | Patched value references a file_id that does not exist | 422 |

## Semantics

### 1. Versioning — always create N+1

Every successful PATCH creates a new document version. There is no in-place update mode, even for namespaces with mutable semantics elsewhere.

Rationale:
- MongoDB's WiredTiger storage engine rewrites the entire document on any update, so "in-place" is not actually cheaper at the storage layer
- Always bumping gives free audit trail (`updated_at`/`updated_by`), free event replay, free reporting sync
- One code path for the server, simpler to reason about
- Old version retention is an orthogonal concern (a future "keep last N" policy can be added separately)

### 2. Merge semantics — RFC 7396 (JSON Merge Patch)

The `patch` body follows JSON Merge Patch (RFC 7396):

- **Object at the same path** → recursively deep-merged
- **Array** → fully replaced (send the whole array, even to add or remove one item)
- **Scalar** → replaced
- **`null`** → field is deleted

Examples:

```json
// Current document data:
{ "name": "Acme", "address": { "street": "Main St", "city": "NYC" }, "tags": ["a", "b"] }

// Patch:
{ "address": { "city": "Boston" }, "tags": ["a", "b", "c"] }

// Result:
{ "name": "Acme", "address": { "street": "Main St", "city": "Boston" }, "tags": ["a", "b", "c"] }
```

```json
// Patch to delete a field:
{ "address": null }

// Result:
{ "name": "Acme", "tags": ["a", "b"] }
// Note: if `address` is a required template field, this fails template validation (422).
```

Array element-level operations (insert at index, remove element by position) are intentionally not supported. They are racy under concurrent edits and the API surface is much larger. Send the whole array.

### 3. Identity fields — flat reject

Every template defines a set of identity fields whose values compose the document's identity hash. PATCH cannot modify them. If the patch body mentions any identity field, the item fails with `identity_field_change` (400 in error code, returned per item in the bulk response).

The check happens at parse time, before any merge work. The error message includes the offending field names.

To "change" an identity field, the caller creates a new document via POST. PATCH is strictly for updating data on an existing entity.

### 4. Namespace — also rejected

`namespace` is part of identity addressing and cannot be patched. Same handling as identity fields.

### 5. Concurrency — LWW default, opt-in OCC via `if_match`

**Default behavior (LWW):**
- Server reads current document version
- Merges patch into current `data`
- Atomically writes new version using `findOneAndUpdate` with a version filter
- If the version filter fails (some other writer raced ahead), retry the read-merge-write up to 3 times
- After 3 failed retries, return `concurrency_conflict`

This means simultaneous PATCHes touching *different* fields both succeed (via the retry loop). Simultaneous PATCHes touching the *same* field result in last-writer-wins on that field.

**Opt-in OCC (`if_match`):**
- Caller sends `if_match: <version>` per item
- Server's atomic update filter requires version to equal `if_match`
- If not, item fails immediately with `concurrency_conflict` and the current version
- Caller is expected to re-read and decide (re-apply patch, prompt user, or abandon)

This matches HTTP `If-Match` semantics (RFC 7232) at the per-item level instead of header level. Headers don't fit per-item bulk operations.

Pessimistic locking is intentionally not used. WIP's usage pattern (rare concurrent edits on the same document) does not justify the overhead and operational complexity.

### 6. Term references — server-side resolution

If the patched fields include any term-valued fields, the server re-resolves the term references automatically. The caller never sends `term_references` directly.

For each patched field:
- If the template declares it as a term-valued field
- And the new value is a term value or synonym
- Server looks it up in the field's terminology
- Updates `term_references[field_path]` accordingly

If the value cannot be resolved → item fails with `term_not_found`.

This matches POST behavior when `term_references` is omitted.

### 7. File references — server-side resolution

If the patched fields include any file-valued fields, the server re-resolves the file references automatically. The caller sends only the new `file_id` in the data.

For each patched file field:
- Server looks up the file_id in the files service
- Updates `file_references[field_path]` with the file metadata
- If `null` is sent, removes from both `data` and `file_references`

If the file_id does not exist → item fails with `file_not_found`.

The old file is **not** cleaned up — it is still reachable via previous versions of the document. Orphan detection (separate background job) handles removal when no version references it.

### 8. Validation — full re-validation of merged document

After merge, the server validates the **entire merged document** against the template. Not just the changed subtree. Reasons:

- Cross-field constraints (e.g., "if status is approved, score must be set") need the whole doc
- Required-field checks must see fields that weren't in the patch
- Template validation is cheap relative to the cost of a corrupted document

If validation fails → item fails with `validation_failed`.

### 9. No-op detection — smart, not strict

After merge, the server compares the merged `data` to the current `data`. If byte-equal (same canonical JSON), no new version is created. The item returns `ok` with the current version.

This means:
- Empty patch body → no-op, returns current
- Patch that sets `status: "active"` when current is already `active` → no-op
- Patch that re-sends an unchanged value → no-op

History is not polluted with N+1 = N versions.

### 10. Audit fields

The new version's `created_at` and `updated_at` are set to "now". `created_by` and `updated_by` are set to the caller's identity_string (same logic as POST). The original document's `created_at`/`created_by` from version 1 are not preserved on the new version — each version stands on its own and the lineage is the version chain.

This matches existing POST semantics for new versions.

### 11. Permissions

Same as POST: the caller needs **write permission** on the document's namespace. The server resolves the document → reads its namespace → checks permission. No new permission grant or scope is added.

### 12. Reporting sync

Each new version fires a NATS event identical to the existing "document version created" event. Reporting-sync consumes it via the existing pipeline. Zero changes needed in reporting-sync.

### 13. Template version

PATCH uses the template version recorded on the document, not the latest active template version. This matches POST behavior for new versions of an existing document. If the template has evolved since the document was created, the patched version still validates against the original template.

## Server Implementation Outline

```python
# Pseudocode — components/document-store/src/document_store/api/documents.py

@router.patch("/documents", response_model=BulkResponse)
async def patch_documents(
    items: list[PatchDocumentItem],
    identity: UserIdentity = Depends(...),
) -> BulkResponse:
    results = []
    for item in items:
        try:
            result = await _patch_one(item, identity)
            results.append({"status": "ok", "document": result})
        except PatchError as e:
            results.append({"status": "error", "document_id": item.document_id, "error": e.to_dict()})
    return BulkResponse(results=results)


async def _patch_one(item, identity):
    # 1. Reject patches that touch identity fields or namespace
    template = await get_template_for(item.document_id)
    _reject_identity_changes(item.patch, template)
    _reject_namespace_change(item.patch)

    # 2. Retry loop for atomic read-merge-write
    for attempt in range(3):
        current = await Document.get(item.document_id)
        if not current:
            raise NotFound(item.document_id)
        if current.status == "archived":
            raise Archived(item.document_id)
        await check_write_permission(identity, current.namespace)

        # Optional OCC
        if item.if_match is not None and current.version != item.if_match:
            raise ConcurrencyConflict(current.version)

        # 3. Merge data (RFC 7396)
        merged_data = json_merge_patch(current.data, item.patch)

        # 4. No-op detection
        if canonical_json(merged_data) == canonical_json(current.data):
            return current.to_response()

        # 5. Re-resolve term and file references for patched fields
        merged_term_refs = await resolve_terms(merged_data, template, current.term_references)
        merged_file_refs = await resolve_files(merged_data, template, current.file_references)

        # 6. Full template validation
        validate_against_template(merged_data, template)

        # 7. Create new version atomically with version filter
        new_version = current.version + 1
        new_doc = Document(
            document_id=current.document_id,
            namespace=current.namespace,
            template_id=current.template_id,
            template_version=current.template_version,
            version=new_version,
            data=merged_data,
            term_references=merged_term_refs,
            file_references=merged_file_refs,
            identity_hash=current.identity_hash,  # unchanged
            status=current.status,
            updated_at=now(),
            updated_by=identity.identity_string,
            created_at=now(),
            created_by=identity.identity_string,
        )
        try:
            await new_doc.insert_with_version_check(expected_previous_version=current.version)
        except VersionConflict:
            continue  # retry

        # 8. Emit NATS event (existing path)
        await publish_document_version_event(new_doc)
        return new_doc.to_response()

    raise ConcurrencyConflict(current.version)
```

The `insert_with_version_check` is implemented as a MongoDB transaction or as a conditional insert that checks no version > current.version exists for this document_id.

## Client Library Outline

### `@wip/client` (TypeScript)

```typescript
// libs/wip-client/src/services/documents.ts

interface PatchDocumentRequest {
  document_id: string
  patch: Record<string, any>
  if_match?: number
}

class DocumentService {
  // Single-item convenience wrapper
  async update(
    documentId: string,
    patch: Record<string, any>,
    options?: { ifMatch?: number; namespace?: string }
  ): Promise<DocumentResponse> {
    const result = await this.updateBulk(
      [{ document_id: documentId, patch, if_match: options?.ifMatch }],
      { namespace: options?.namespace }
    )
    return unwrapBulkResult(result)
  }

  async updateBulk(
    items: PatchDocumentRequest[],
    options?: { namespace?: string }
  ): Promise<BulkResponse<DocumentResponse>> {
    return this.http.patch('/documents', items, options)
  }
}
```

### `@wip/react`

```typescript
// libs/wip-react/src/hooks/use-mutations.ts

export function useUpdateDocument(
  options?: Omit<UseMutationOptions<DocumentResponse, Error, UpdateDocumentVars>, 'mutationFn'>,
) {
  const client = useWipClient()
  const queryClient = useQueryClient()
  return useMutation({
    ...options,
    mutationFn: ({ documentId, patch, ifMatch }) =>
      client.documents.update(documentId, patch, { ifMatch }),
    onSuccess: (data, variables, context) => {
      queryClient.invalidateQueries({ queryKey: wipKeys.documents.detail(variables.documentId) })
      queryClient.invalidateQueries({ queryKey: wipKeys.documents.all })
      options?.onSuccess?.(data, variables, context)
    },
  })
}
```

## MCP Tool

```python
# components/mcp-server/src/wip_mcp/tools/documents.py

@mcp.tool()
async def update_document(
    document_id: str,
    patch: dict,
    if_match: int | None = None,
) -> dict:
    """Update specific fields on an existing document.

    Sends a partial patch following JSON Merge Patch (RFC 7396):
    - Objects are deep-merged
    - Arrays are replaced (send the full array)
    - null deletes a field

    Identity fields cannot be changed via update_document — create a new
    document instead. Use if_match for optimistic concurrency control.
    """
    return await client.update_document(document_id, patch, if_match=if_match)
```

## CLI

```bash
wip-toolkit document update DOC-123 --patch '{"status": "approved"}'
wip-toolkit document update DOC-123 --patch '{"score": 92}' --if-match 3
```

## What changes outside this design

| Area | Change |
|---|---|
| `components/document-store` | New `PATCH /documents` endpoint. Reuses existing template validation, term/file resolution, NATS event publishing |
| `libs/wip-client` | New `documents.update()` and `documents.updateBulk()` methods, types, version bump |
| `libs/wip-react` | New `useUpdateDocument()` hook, version bump |
| `components/mcp-server` | New `update_document` tool |
| `WIP-Toolkit` | New `document update` command |
| `docs/api-conventions.md` | Add PATCH section (RFC 7396, per-item if_match, BulkResponse) |
| `docs/uniqueness-and-identity.md` | Mention PATCH cannot change identity fields or namespace |
| MCP `wip://conventions` resource | Add update_document tool description |
| Tests | Endpoint tests at every layer; ~400 LOC of new tests |

## What does NOT change

- Existing `POST /documents` flow continues to work — creating a new document or new version via identity dedup is unchanged
- Reporting sync — consumes the same NATS event as before
- Auth model — no new permissions
- Ingest gateway — async bulk ingest still uses POST semantics; PATCH is for interactive clients
- File storage / orphan cleanup — unchanged

## Open questions deferred

- **Retention policy for old versions** — orthogonal. Today all versions are kept forever. A "keep last N" policy is a separate roadmap item.
- **PATCH events in ingest gateway** — async bulk ingest typically sends full documents; if a use case emerges, add a "patch" event type to NATS schema.
- **Conditional patches based on data values** — e.g., "set score to 92 only if current score is null". Out of scope; clients can implement this with `if_match` and a re-read loop.

## Migration / Rollout

This is strictly additive. No existing endpoints, schemas, or behaviors change. Rollout:

1. Backend endpoint deployed → existing clients unaffected
2. `@wip/client` 0.10.0 published → opt-in upgrade
3. `@wip/react` 0.6.0 published → React Console upgrades
4. MCP tool live → AI agents can use immediately
5. CLI command available → operators can use for ad-hoc updates

No data migration. No flag day. No breaking changes.
