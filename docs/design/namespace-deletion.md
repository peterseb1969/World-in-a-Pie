# Namespace Deletion

**Status:** Proposed (v1.1)

Delete an entire namespace and all its data, with a persistent delete journal for crash-safe resumption and audit trail.

## Problem

Today, WIP only supports soft-delete (`status: "inactive"`). Data is never physically removed. This creates real problems:

1. **Dev iteration clutter** — AI-assisted data model development produces multiple rounds of discarded templates, terminologies, and documents. The namespace becomes unusable without wiping the database.
2. **Storage pressure** — IoT/HomeAssistant data accumulates indefinitely. On a Pi with limited storage, soft-deleted documents still consume MongoDB and MinIO space.
3. **Data privacy** — GDPR or organisational policy may require permanent erasure of a namespace's data. Not possible today.
4. **Zombie namespaces** — The workaround (export active data, import into new namespace) leaves the old namespace behind with no way to remove it.
5. **Dev→Prod workflow** — There is no clean way to use a disposable namespace for development and delete it after bootstrapping production.

## Design

### Namespace `deletion_mode`

A new field on the Namespace model, set at creation time, changeable by namespace admin:

| Mode | Behaviour |
|------|-----------|
| `retain` (default) | Soft-delete only. Hard-delete and namespace deletion are refused. Current behaviour. |
| `full` | Soft-delete, hard-delete of individual entities, and full namespace deletion are all permitted. |

The `wip` shared namespace is always `retain` and cannot be changed.

Switching `full` → `retain` is allowed (lock down after cleanup). Switching `retain` → `full` requires a confirmation flag (`confirm_enable_deletion=true`) to prevent accidental exposure.

### Namespace deletion flow

```
Request (dry_run=true)
  │
  ├─→ Count all entities, check inbound references
  └─→ Return report (no changes made)

Request (dry_run=false)
  │
  ├─→ Pre-check: deletion_mode == "full"?
  ├─→ Pre-check: requester has admin permission on namespace?
  │
  ├─→ LOCK namespace (status: "locked")
  │     All queries and writes return "namespace not found"
  │     30-second cache window — accepted trade-off
  │
  ├─→ Check inbound references from other namespaces
  │     ├─→ Found, no --force flag → ABORT, unlock namespace
  │     └─→ Found, --force flag → proceed (references listed in journal)
  │
  ├─→ Build delete journal (enumerate all records)
  │     Persist journal to MongoDB (namespace_deletions collection)
  │
  ├─→ Execute journal steps (sequential, idempotent)
  │     Each step: deleteMany by namespace filter
  │     Mark each step completed as it finishes
  │
  ├─→ Finalize: mark journal "completed", delete namespace record
  └─→ Return summary
```

### Dry run

`DELETE /api/registry/namespaces/{prefix}?dry_run=true`

Returns a full impact report without making any changes:

```json
{
  "namespace": "dev_herbs",
  "deletion_mode": "full",
  "dry_run": true,
  "entity_counts": {
    "documents": 1432,
    "files": 23,
    "templates": 5,
    "terminologies": 2,
    "terms": 87,
    "term_relations": 12,
    "term_audit_log": 340,
    "registry_entries": 1527,
    "id_counters": 5,
    "namespace_grants": 3,
    "postgres_rows": 1432
  },
  "minio_objects": 23,
  "inbound_references": [
    {
      "type": "template_extends",
      "source_namespace": "prod_herbs",
      "source_entity": "detailed_herb_profile (template)",
      "target_entity": "base_herb v2 (template)",
      "impact": "Source template loses parent schema. Field inheritance broken."
    },
    {
      "type": "terminology_reference",
      "source_namespace": "cooking",
      "source_entity": "recipe (template, field: herb_type)",
      "target_entity": "HERB_NAMES (terminology)",
      "impact": "Term validation will fail for this field."
    }
  ],
  "safe_to_delete": false,
  "requires_force": true
}
```

### Locking

When a namespace is locked, it is immediately unusable:

- **Permission checks** return `none` for all users (including admins)
- **All reads** return "namespace not found" (same as non-existent namespace)
- **All writes** are rejected

Services cache permissions for up to 30 seconds (see `wip_auth/permissions.py`, `GRANT_CACHE_TTL = 30`). During this window, in-flight operations may still succeed against the locked namespace. This is acceptable because:

- The journal uses filter-based `deleteMany`, not individual IDs — late-arriving records are caught
- The 30-second window is short relative to the deletion process
- A user who deletes a namespace accepts that in-flight operations may fail

The `locked` status is a new addition to the existing `active | archived | deleted` enum on the Namespace model.

### Delete journal

The journal is a MongoDB document in a dedicated `namespace_deletions` collection. It is the single source of truth for what needs to happen and what has been done.

```json
{
  "namespace": "dev_herbs",
  "status": "in_progress",
  "requested_by": "admin@wip.local",
  "requested_at": "2026-03-24T14:00:00Z",
  "force": false,
  "broken_references": [],
  "steps": [
    {
      "order": 1,
      "store": "minio",
      "action": "delete_objects",
      "detail": "storage_keys from file_metadata",
      "storage_keys": ["file_abc123", "file_def456"],
      "status": "completed",
      "deleted_count": 23,
      "completed_at": "2026-03-24T14:00:02Z"
    },
    {
      "order": 2,
      "store": "mongodb",
      "collection": "files",
      "filter": {"namespace": "dev_herbs"},
      "status": "completed",
      "deleted_count": 23,
      "completed_at": "2026-03-24T14:00:02Z"
    },
    {
      "order": 3,
      "store": "mongodb",
      "collection": "documents",
      "filter": {"namespace": "dev_herbs"},
      "status": "completed",
      "deleted_count": 1432,
      "completed_at": "2026-03-24T14:00:05Z"
    },
    {
      "order": 4,
      "store": "mongodb",
      "collection": "templates",
      "filter": {"namespace": "dev_herbs"},
      "status": "pending"
    },
    {
      "order": 5,
      "store": "mongodb",
      "collection": "terms",
      "filter": {"namespace": "dev_herbs"},
      "status": "pending"
    },
    {
      "order": 6,
      "store": "mongodb",
      "collection": "term_relations",
      "filter": {"namespace": "dev_herbs"},
      "status": "pending"
    },
    {
      "order": 7,
      "store": "mongodb",
      "collection": "term_audit_log",
      "filter": {"namespace": "dev_herbs"},
      "status": "pending"
    },
    {
      "order": 8,
      "store": "mongodb",
      "collection": "terminologies",
      "filter": {"namespace": "dev_herbs"},
      "status": "pending"
    },
    {
      "order": 9,
      "store": "postgresql",
      "action": "delete_rows",
      "detail": "DELETE FROM each doc_* table WHERE namespace = 'dev_herbs'",
      "status": "pending"
    },
    {
      "order": 10,
      "store": "mongodb",
      "collection": "registry_entries",
      "filter": {"namespace": "dev_herbs"},
      "status": "pending"
    },
    {
      "order": 11,
      "store": "mongodb",
      "collection": "id_counters",
      "filter": {"namespace": "dev_herbs"},
      "status": "pending"
    },
    {
      "order": 12,
      "store": "mongodb",
      "collection": "namespace_grants",
      "filter": {"namespace_prefix": "dev_herbs"},
      "status": "pending"
    },
    {
      "order": 13,
      "store": "mongodb",
      "collection": "namespaces",
      "filter": {"prefix": "dev_herbs"},
      "status": "pending"
    }
  ]
}
```

#### Step ordering rationale

1. **MinIO objects first** — storage keys are read from `file_metadata` (MongoDB). Must delete blobs before deleting the metadata that references them.
2. **Files (metadata)** — MongoDB `files` collection, now safe to remove.
3. **Documents** — largest collection, biggest space reclaim.
4. **Templates** — must come after documents (no functional dependency, but logical).
5. **Terms, term_relations, term_audit_log** — must come before terminologies.
6. **Terminologies** — after terms are gone.
7. **PostgreSQL rows** — `DELETE FROM doc_* WHERE namespace = 'dev_herbs'`. Tables are shared across namespaces (rows have a `namespace` column), so we delete rows, not drop tables. Reporting-sync also has `_wip_terminologies` and `_wip_terms` tables with namespace-scoped rows.
8. **Registry entries, ID counters** — frees identity hashes so the same data can be re-imported elsewhere.
9. **Namespace grants** — access control for the namespace.
10. **Namespace record** — deleted last. As long as this exists in `locked` state, the deletion is considered in-progress.

#### Idempotency

Every step is safe to re-execute:

- `deleteMany({namespace: "dev_herbs"})` on an already-empty collection returns `deleted_count: 0`
- MinIO delete on a non-existent key is a no-op (S3 semantics)
- PostgreSQL `DELETE WHERE namespace = 'dev_herbs'` on empty rows returns 0

#### Crash recovery

On startup, Registry checks for any `namespace_deletions` documents with `status: "in_progress"`. For each:

1. Find the first step with `status: "pending"`
2. Resume execution from that step
3. Steps already marked `completed` are skipped

No manual intervention needed. The journal is the recovery log.

#### Manual retry

If automatic recovery fails (e.g., PostgreSQL is still down after restart), an admin can trigger retry:

`POST /api/registry/namespaces/{prefix}/resume-delete`

This re-runs the journal from the first incomplete step.

### Inbound reference check

Before building the journal, Registry queries for references from **other** namespaces into the one being deleted:

| Reference type | How to find | Impact of deletion |
|---|---|---|
| Template extends | Templates where `extends_template` points to a template in the target namespace | Child template loses parent schema. Field inheritance broken. |
| Terminology reference | Templates in other namespaces with fields whose `terminology_id` belongs to the target namespace | Term validation fails for that field. Documents can't be created/updated. |
| Registry synonyms | Synonym links where one entry is in the target namespace and the other is outside | Cross-reference lost. The external synonym remains valid as a standalone entry. Low impact. |

**Synonym handling:** Synonyms are groups of equivalent entries. Deleting one member removes it from the group but does not invalidate the others. This is low-severity and does not require `--force`. The journal records which synonym links were broken for the audit trail.

**Template extends and terminology references** are high-severity. Without `--force`, deletion is refused and the dry-run report lists exactly which external entities are affected, so the admin can fix them first (re-point to a different parent template, create replacement terminology, etc.).

### Audit trail

Completed journals are never deleted. They serve as the audit trail:

```json
{
  "namespace": "dev_herbs",
  "status": "completed",
  "requested_by": "admin@wip.local",
  "requested_at": "2026-03-24T14:00:00Z",
  "completed_at": "2026-03-24T14:00:12Z",
  "force": false,
  "broken_references": [],
  "summary": {
    "documents": 1432,
    "files": 23,
    "templates": 5,
    "terminologies": 2,
    "terms": 87,
    "term_relations": 12,
    "term_audit_log": 340,
    "registry_entries": 1527,
    "id_counters": 5,
    "namespace_grants": 3,
    "postgres_rows": 1432,
    "minio_objects": 23
  }
}
```

Registry checks `namespace_deletions` when creating a namespace — if a completed deletion exists for the same prefix, warn the user (but allow re-creation).

### Who executes the journal

**Registry** owns the entire flow. It already has MongoDB access and is the namespace authority. For the deletion journal, it also needs:

- **MinIO access** — to delete file objects. Registry will need MinIO connection config (same env vars as Document-Store).
- **PostgreSQL access** — to delete reporting rows. Registry will need PostgreSQL connection config (same env vars as Reporting-Sync).

This is new coupling for Registry, but it's pragmatic. The alternative (calling each service's API) adds distributed coordination complexity that the journal pattern explicitly avoids.

If a deployment doesn't use MinIO or PostgreSQL (e.g., `core` preset without reporting or files), those steps are skipped — the journal builder only includes steps for backends that are configured.

### Collection inventory

All MongoDB collections that contain namespace-scoped data, and therefore need a journal step:

| Collection | Service | Filter field | Notes |
|---|---|---|---|
| `documents` | Document-Store | `namespace` | Largest collection |
| `files` | Document-Store | `namespace` | Also requires MinIO object deletion |
| `templates` | Template-Store | `namespace` | |
| `terminologies` | Def-Store | `namespace` | |
| `terms` | Def-Store | `namespace` | |
| `term_relations` | Def-Store | `namespace` | |
| `term_audit_log` | Def-Store | `namespace` | |
| `registry_entries` | Registry | `namespace` | |
| `id_counters` | Registry | `namespace` | |
| `namespace_grants` | Registry | `namespace_prefix` | Different field name |
| `namespaces` | Registry | `prefix` | Deleted last |

**PostgreSQL tables** with namespace-scoped rows (filter: `WHERE namespace = '{prefix}'`):

| Table pattern | Content |
|---|---|
| `doc_*` | Document data per template |
| `_wip_terminologies` | Terminology sync |
| `_wip_terms` | Term sync |
| `_wip_term_relations` | Relation sync |
| `_wip_sync_status` | Sync status rows for this namespace |

**MinIO:**

| Bucket | Key pattern |
|---|---|
| `wip-attachments` (default) | Keys are `file_id` values — no namespace prefix. Must read from `files` collection before deleting metadata. |

> **Developer note:** If you add a new MongoDB collection with a `namespace` field, add it to the deletion journal builder in `registry/services/namespace_deletion.py`. See the collection inventory above.

## API

### Delete namespace

```
DELETE /api/registry/namespaces/{prefix}
```

Query parameters:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `dry_run` | bool | `false` | Return impact report without making changes |
| `force` | bool | `false` | Proceed despite inbound references from other namespaces |

Request headers: `X-API-Key` with admin permission on the namespace.

**Responses:**

- `200` — Dry run report (when `dry_run=true`)
- `200` — Deletion completed (when `dry_run=false` and all steps succeed)
- `202` — Deletion started but not yet complete (large namespaces; poll status endpoint)
- `400` — Namespace `deletion_mode` is not `full`
- `403` — Insufficient permissions (requires admin)
- `404` — Namespace not found
- `409` — Namespace already locked (deletion in progress)
- `409` — Inbound references found (without `force`)

### Check deletion status

```
GET /api/registry/namespaces/{prefix}/deletion-status
```

Returns the current journal state for an in-progress or completed deletion.

### Resume deletion

```
POST /api/registry/namespaces/{prefix}/resume-delete
```

Retries an incomplete deletion from where it left off. Used when a backend was unavailable during the initial attempt.

### Update deletion mode

```
PATCH /api/registry/namespaces/{prefix}
```

Body:
```json
{
  "deletion_mode": "full",
  "confirm_enable_deletion": true
}
```

The `confirm_enable_deletion` flag is required when changing from `retain` to `full`. Not required for `full` to `retain`.

## Implementation

### New files

- `components/registry/src/registry/services/namespace_deletion.py` — journal builder, executor, reference checker
- `components/registry/src/registry/models/deletion_journal.py` — Beanie document model for the journal
- `components/registry/src/registry/api/namespace_deletion.py` — API endpoints

### Modified files

- `components/registry/src/registry/models/namespace.py` — add `deletion_mode` field, add `locked` to status enum
- `components/registry/src/registry/main.py` — include new router, startup recovery check
- `libs/wip-auth/src/wip_auth/permissions.py` — return `none` for locked namespaces
- `components/mcp-server/src/wip_mcp/server.py` — new `delete_namespace` tool

### Dev→Prod workflow (enabled by this feature)

```bash
# 1. Create disposable dev namespace
curl -X POST .../api/registry/namespaces \
  -d '[{"prefix": "dev_herbs", "deletion_mode": "full"}]'

# 2. Iterate on data model with AI agent (multiple rounds)
# ... create terminologies, templates, test documents ...

# 3. Export the final data model
/export-model  # saves to seed files

# 4. Create production namespace
curl -X POST .../api/registry/namespaces \
  -d '[{"prefix": "herbs", "deletion_mode": "retain"}]'

# 5. Bootstrap production from seed files
/bootstrap --namespace herbs

# 6. Delete dev namespace (dry run first)
curl -X DELETE ".../api/registry/namespaces/dev_herbs?dry_run=true"

# 7. Delete for real
curl -X DELETE .../api/registry/namespaces/dev_herbs
```

## Not in scope (v1.1)

- **Per-entity hard-delete** — individual document/template/terminology hard-delete within a namespace. Useful but not critical. Can be added in a later release using the same journal pattern.
- **Retention policies** — automatic purge of inactive items after N days. Requires a scheduled job. Future enhancement.
- **Selective deletion** — delete only documents older than X, or only inactive items. The journal pattern supports this but the UI/API would need design.
- **Undo** — once deletion starts, it cannot be reversed. The dry-run and force-flag are the safety mechanisms.
