# Template Hard-Delete: Document Orphan Prevention

## Problem

Hard-deleting a template (`hard_delete=true, force=true, deletion_mode='full'`)
permanently removes the template from MongoDB and the Registry while leaving
its documents intact. Those documents reference a `template_id` that no longer
exists — they are orphaned and cannot be validated, migrated, or meaningfully
queried by template.

The current `force` flag was designed for soft-delete (deactivation), where the
template still exists as `status: inactive` and can be reactivated. Hard-delete
is irreversible, but the code treats `force` identically for both paths.

## Current Behavior

| Operation | Documents exist | force=false | force=true |
|---|---|---|---|
| Soft-delete | yes | **blocked** | proceeds (template → inactive) |
| Hard-delete | yes | **blocked** | **proceeds — orphans documents** |
| Hard-delete | no | proceeds | proceeds |

## Required Behavior

Hard-delete with existing documents must either block or cascade — never
silently orphan.

### Phase 1: Block (immediate fix)

Hard-delete refuses when documents exist, regardless of `force`. The operator
must delete documents first, then hard-delete the template.

**Changes:**

`components/template-store/src/template_store/api/templates.py` — in the
delete handler, after the dependency check (line ~507), add a hard-delete
guard:

```python
if item.hard_delete and deps.document_count > 0:
    results.append(BulkResultItem(
        index=i, status="error", id=item.id,
        error=f"Cannot hard-delete: {deps.document_count} document(s) "
              f"reference this template. Hard-delete is irreversible — "
              f"delete the documents first, or use cascade=true."
    ))
    continue
```

This blocks even when `force=true`. The `force` flag continues to work for
soft-delete (deactivation with existing documents).

### Phase 2: Cascade delete (follow-up)

Add `cascade=true` to the delete request. When combined with `hard_delete=true`
and `deletion_mode='full'`, it deletes the template AND all its documents in
one operation.

**New parameter:** `cascade: bool = False` on the delete request model.

**Cascade scope — what gets deleted:**

1. **Documents** — all documents with `template_id` matching the template,
   across all versions. Includes inactive/archived documents.
2. **Registry entries** — the document registry entries (not just the
   template entry). Each document has its own registry entry with synonyms.
3. **Relationship documents** — any edge-type documents where `source_ref`
   or `target_ref` points at one of the deleted documents.
4. **File references** — decrement reference counts on files referenced by
   deleted documents. Files with `reference_count: 0` become candidates for
   file cleanup (but are NOT auto-deleted — file deletion is a separate
   operation).
5. **Reporting tables** — drop the template's per-version reporting tables
   and entity view from the namespace's PostgreSQL schema.
6. **Sync bookkeeping** — remove `_wip_schema_migrations` and
   `_wip_sync_status` rows for the template.

**Cross-service coordination:**

The cascade is inherently cross-service:

```
template-store (receives the request)
  → document-store: bulk-delete documents by template_id
    → registry: hard-delete document entries
    → nats: publish DOCUMENT_DELETED events
    → file-store: decrement reference counts
  → reporting-sync: drop reporting tables (via TEMPLATE_DELETED event)
  → registry: hard-delete template entry
```

**Implementation options:**

A. **Synchronous orchestration** — template-store calls document-store's
   bulk-delete endpoint, waits for completion, then deletes itself. Simple
   but slow for large document sets and couples template-store to
   document-store's API.

B. **Job-based** — template-store creates a cascade-delete job (similar to
   namespace deletion in the registry). The job runs in the background,
   deletes documents in batches, and marks the template as deleted when
   complete. Better for large sets but more complex.

C. **Document-store owned** — move the cascade orchestration to
   document-store (it already owns namespace deletion with cascade). The
   template-store publishes a TEMPLATE_CASCADE_DELETE event, document-store
   picks it up and handles the cleanup. Aligns with existing patterns but
   requires a new event type.

**Recommended: Option B (job-based).** It follows the namespace-deletion
pattern already in the registry (`services/namespace_deletion.py`), handles
large document counts without timeout, and provides progress visibility.

**Safeguards:**

- `cascade=true` requires `hard_delete=true` — cascading a soft-delete
  makes no sense (the template stays, documents shouldn't disappear).
- `cascade=true` requires `deletion_mode='full'` on the namespace — same
  guard as hard-delete.
- Dry-run support: `cascade=true, dry_run=true` returns the impact count
  (documents, files, relationships, reporting tables) without deleting.
- The cascade job should be resumable — if it fails mid-way (pod restart),
  it can pick up where it left off (same pattern as restore jobs).

## Scope of Each Phase

| Capability | Phase 1 | Phase 2 |
|---|---|---|
| Block hard-delete with documents | yes | yes (unless cascade) |
| Cascade delete documents | no | yes |
| Cascade delete registry entries | no | yes |
| Cascade clean up relationships | no | yes |
| Cascade drop reporting tables | no | yes |
| Dry-run impact report | no | yes |
| Job-based progress tracking | no | yes |

## Testing

**Phase 1:**
- Test: hard-delete with documents → error, even with force=true
- Test: hard-delete without documents → succeeds
- Test: soft-delete with documents + force=true → still works (deactivation)
- Existing tests must pass unchanged

**Phase 2:**
- Test: cascade deletes all documents
- Test: cascade decrements file reference counts
- Test: cascade removes reporting tables
- Test: cascade with dry_run returns counts without deleting
- Test: cascade resumes after pod restart
- Test: cascade refuses without hard_delete=true
- Test: cascade refuses without deletion_mode='full'
