# Namespace-Scoped Data Architecture

Design document for adding namespace support to all WIP services, enabling namespace-scoped backup/restore, data migration between instances, and dev/test workflows.

## Status: Partially Implemented (Phase 1-2 complete)

## Goals

1. **Namespace isolation**: All entities (terminologies, terms, templates, documents) belong to a namespace
2. **Simple backup/restore**: Export/import entire namespaces with single commands
3. **Data migration**: Move namespaces between WIP instances (dev → prod, instance A → instance B)
4. **Dev/test workflows**: Create `-dev` or `-test` suffixed namespaces for experimentation
5. **Namespace archival**: Soft-delete entire namespaces while preserving data

## Current State (as of Feb 2025)

### What's Implemented

**Registry** — Full namespace support:
- Namespaces are first-class entities with configurable ID generation
- Each entry stores `primary_pool_id` and `entry_id`
- Custom namespaces auto-created on first use (e.g., `seed-terminologies`)
- ID prefixes scoped by namespace (e.g., `TERM-` for wip, `SEED-TERM-` for seed)

**All Services** — Namespace stored on every entity:
- All MongoDB models have a `namespace` field (short prefix, e.g., `"wip"`, `"seed"`)
- Registry client calls derive `pool_id` from namespace (e.g., `namespace="seed"` → `pool_id="seed-terminologies"`)
- All list/filter APIs accept `namespace` as a query parameter
- Create endpoints accept `namespace` in the request body (defaults to `"wip"`)
- By-value lookups default to `namespace=None` (search all namespaces)
- Seed script uses `--namespace seed` to isolate test data from production data

**Implementation note:** This design document originally proposed using the full pool name (e.g., `"wip-terminologies"`) as the namespace field value. The actual implementation uses a short prefix (e.g., `"wip"`) and derives the pool name in the registry client. See [Namespace Implementation](../namespace-implementation.md) for current details.

### What's NOT Implemented

- Namespace Group management API (Phase 3)
- Export/Import for namespaces (Phase 4)
- Archive/Delete namespaces (Phase 5)
- CLI commands
- Per-namespace permissions
- Cross-namespace reference validation

## Proposed Architecture

### Namespace Hierarchy

```
wip                          # Default production namespace group
├── wip-terminologies        # Terminology definitions
├── wip-terms                # Term values
├── wip-templates            # Document templates
├── wip-documents            # Documents
└── wip-files                # File attachments

dev                          # Development/testing namespace group
├── dev-terminologies
├── dev-terms
├── dev-templates
├── dev-documents
└── dev-files

customer-abc                 # Tenant-specific namespace group
├── customer-abc-terminologies
├── customer-abc-templates
└── customer-abc-documents
```

### Namespace Group Concept

A **namespace group** is a logical set of related namespaces sharing a prefix:

```python
class NamespaceGroup(BaseModel):
    """A logical grouping of related namespaces."""
    prefix: str              # e.g., "wip", "dev", "customer-abc"
    description: str
    created_at: datetime
    status: Literal["active", "archived", "deleted"]

    # Derived namespace names
    @property
    def terminologies_ns(self) -> str:
        return f"{self.prefix}-terminologies"

    @property
    def terms_ns(self) -> str:
        return f"{self.prefix}-terms"

    @property
    def templates_ns(self) -> str:
        return f"{self.prefix}-templates"

    @property
    def documents_ns(self) -> str:
        return f"{self.prefix}-documents"

    @property
    def files_ns(self) -> str:
        return f"{self.prefix}-files"
```

---

## Model Changes

### Def-Store: Terminology

```python
# Current
class Terminology(Document):
    terminology_id: str
    code: str
    name: str
    # ... other fields

# Proposed
class Terminology(Document):
    terminology_id: str
    namespace: str = Field(default="wip-terminologies", index=True)
    code: str
    name: str
    # ... other fields

    class Settings:
        indexes = [
            IndexModel([("namespace", 1), ("terminology_id", 1)], unique=True),
            IndexModel([("namespace", 1), ("code", 1)], unique=True),
            IndexModel([("namespace", 1), ("status", 1)]),
        ]
```

### Def-Store: Term

```python
# Current
class Term(Document):
    term_id: str
    terminology_id: str
    code: str
    value: str

# Proposed
class Term(Document):
    term_id: str
    namespace: str = Field(default="wip-terms", index=True)
    terminology_id: str
    terminology_namespace: str = Field(default="wip-terminologies")  # Reference
    code: str
    value: str

    class Settings:
        indexes = [
            IndexModel([("namespace", 1), ("term_id", 1)], unique=True),
            IndexModel([("namespace", 1), ("terminology_id", 1), ("code", 1)], unique=True),
            IndexModel([("namespace", 1), ("status", 1)]),
        ]
```

### Template-Store: Template

```python
# Current
class Template(Document):
    template_id: str
    code: str
    name: str
    version: int

# Proposed
class Template(Document):
    template_id: str
    namespace: str = Field(default="wip-templates", index=True)
    code: str
    name: str
    version: int

    class Settings:
        indexes = [
            IndexModel([("namespace", 1), ("template_id", 1)], unique=True),
            IndexModel([("namespace", 1), ("code", 1), ("version", 1)], unique=True),
            IndexModel([("namespace", 1), ("status", 1)]),
        ]
```

### Document-Store: Document

```python
# Current
class WIPDocument(Document):
    document_id: str
    template_id: str
    identity_hash: str
    data: dict

# Proposed
class WIPDocument(Document):
    document_id: str
    namespace: str = Field(default="wip-documents", index=True)
    template_id: str
    template_namespace: str = Field(default="wip-templates")  # Reference
    identity_hash: str
    data: dict

    # Term references include namespace
    term_references: dict[str, TermReference]  # field_name -> {term_id, namespace}

    class Settings:
        indexes = [
            IndexModel([("namespace", 1), ("document_id", 1)], unique=True),
            IndexModel([("namespace", 1), ("template_id", 1)]),
            IndexModel([("namespace", 1), ("identity_hash", 1)]),
            IndexModel([("namespace", 1), ("status", 1)]),
        ]
```

### Document-Store: File

```python
# Current
class FileMetadata(Document):
    file_id: str
    document_id: str
    filename: str

# Proposed
class FileMetadata(Document):
    file_id: str
    namespace: str = Field(default="wip-files", index=True)
    document_id: str
    document_namespace: str = Field(default="wip-documents")  # Reference
    filename: str

    class Settings:
        indexes = [
            IndexModel([("namespace", 1), ("file_id", 1)], unique=True),
            IndexModel([("namespace", 1), ("document_id", 1)]),
        ]
```

---

## API Changes

### URL Pattern

Option 1: **Query Parameter** (backward compatible)
```
GET /api/def-store/terminologies?namespace=dev-terminologies
POST /api/def-store/terminologies?namespace=dev-terminologies
```

Option 2: **Path Prefix** (cleaner, breaking change)
```
GET /api/def-store/ns/dev/terminologies
POST /api/def-store/ns/dev/terminologies
```

**Recommendation**: Option 1 with default namespace for backward compatibility.

### API Examples

```python
# Def-Store
GET  /api/def-store/terminologies                           # Default: wip-terminologies
GET  /api/def-store/terminologies?namespace=dev-terminologies
POST /api/def-store/terminologies?namespace=dev-terminologies
GET  /api/def-store/terms?namespace=dev-terms

# Template-Store
GET  /api/template-store/templates?namespace=dev-templates
POST /api/template-store/templates?namespace=dev-templates

# Document-Store
GET  /api/document-store/documents?namespace=dev-documents
POST /api/document-store/documents?namespace=dev-documents
```

### Registry Client Changes

```python
# Current (hardcoded)
async def register_terminology(code: str, name: str) -> str:
    return await self._register("wip-terminologies", {"code": code, "name": name})

# Proposed (namespace parameter)
async def register_terminology(code: str, name: str, namespace: str = "wip-terminologies") -> str:
    return await self._register(namespace, {"code": code, "name": name})
```

### Cross-Namespace References

When a document references a template from a different namespace:

```python
# Document in dev-documents referencing template in wip-templates
{
    "document_id": "...",
    "pool_id": "dev-documents",
    "template_id": "TPL-000001",
    "template_pool_id": "wip-templates",  # Cross-reference
    "data": {...}
}
```

**Validation rule**: Cross-namespace references are allowed but must be explicit.

---

## Namespace Management API

### New Registry Endpoints

```python
# Create namespace group (creates all 5 namespaces)
POST /api/registry/namespace-groups
{
    "prefix": "dev",
    "description": "Development and testing",
    "id_prefix_modifier": "DEV-"  # Optional: DEV-TERM-000001 instead of TERM-000001
}

# List namespace groups
GET /api/registry/namespace-groups

# Get namespace group details
GET /api/registry/namespace-groups/dev

# Archive namespace group (soft-delete all data)
POST /api/registry/namespace-groups/dev/archive

# Delete namespace group (permanent, requires confirmation)
DELETE /api/registry/namespace-groups/dev?confirm=true
```

### Namespace Group Creation

When creating a namespace group with prefix `dev`:

1. Create `dev-terminologies` namespace with ID generator `DEV-TERM-XXXXXX`
2. Create `dev-terms` namespace with ID generator `DEV-T-XXXXXX`
3. Create `dev-templates` namespace with ID generator `DEV-TPL-XXXXXX`
4. Create `dev-documents` namespace with ID generator UUID7
5. Create `dev-files` namespace with ID generator `DEV-FILE-XXXXXX`

---

## Backup & Restore

### Export Format

```
namespace-export-dev-20240215/
├── manifest.json           # Metadata, checksums, version info
├── registry-entries.jsonl  # All registry entries for namespace group
├── terminologies.jsonl     # Def-Store terminologies
├── terms.jsonl             # Def-Store terms
├── templates.jsonl         # Template-Store templates
├── documents.jsonl         # Document-Store documents
├── files.jsonl             # File metadata
└── files/                  # Actual file content (from MinIO)
    ├── FILE-000001.pdf
    └── FILE-000002.png
```

### Export API

```python
# Export entire namespace group
POST /api/registry/namespace-groups/dev/export
{
    "format": "jsonl",           # jsonl or mongodb-dump
    "include_files": true,       # Include binary files from MinIO
    "compress": true             # gzip compression
}

# Response
{
    "export_id": "export-dev-20240215-abc123",
    "download_url": "/api/registry/exports/export-dev-20240215-abc123.tar.gz",
    "expires_at": "2024-02-16T00:00:00Z",
    "stats": {
        "terminologies": 15,
        "terms": 2500,
        "templates": 8,
        "documents": 1200,
        "files": 45,
        "total_size_bytes": 52428800
    }
}
```

### Import API

```python
# Import namespace group (creates new or merges)
POST /api/registry/namespace-groups/import
{
    "source_url": "https://...",  # Or upload file
    "target_prefix": "imported",  # Rename namespace group
    "mode": "create",             # create, merge, or replace
    "remap_references": true      # Update cross-namespace references
}

# Response
{
    "import_id": "import-abc123",
    "status": "completed",
    "stats": {
        "created": {"terminologies": 15, "terms": 2500, ...},
        "skipped": {"terminologies": 0, ...},
        "errors": []
    }
}
```

### Import Modes

| Mode | Behavior |
|------|----------|
| `create` | Fail if namespace exists |
| `merge` | Add new entities, skip existing (by ID) |
| `replace` | Delete existing, import fresh |

### Reference Remapping

When importing `dev` namespace as `staging`:

```python
# Original (in export)
{
    "document_id": "...",
    "pool_id": "dev-documents",
    "template_pool_id": "dev-templates"
}

# After import with remap
{
    "document_id": "...",
    "pool_id": "staging-documents",
    "template_pool_id": "staging-templates"
}
```

---

## Dev/Test Workflow

### Creating a Dev Environment

```bash
# 1. Create dev namespace group
curl -X POST http://localhost:8001/api/registry/namespace-groups \
  -H "Content-Type: application/json" \
  -d '{"prefix": "dev", "description": "Development testing"}'

# 2. Clone production data (optional)
curl -X POST http://localhost:8001/api/registry/namespace-groups/wip/export \
  -d '{"include_files": false}'

curl -X POST http://localhost:8001/api/registry/namespace-groups/import \
  -d '{"source_url": "...", "target_prefix": "dev", "mode": "create"}'

# 3. Use dev namespace in API calls
curl http://localhost:8002/api/def-store/terminologies?namespace=dev-terminologies
```

### Promoting Dev to Production

```bash
# 1. Export dev namespace
curl -X POST .../namespace-groups/dev/export

# 2. Review changes (diff against production)
curl -X POST .../namespace-groups/diff \
  -d '{"source": "dev", "target": "wip"}'

# 3. Merge into production
curl -X POST .../namespace-groups/import \
  -d '{"source_url": "...", "target_prefix": "wip", "mode": "merge"}'
```

### Archiving a Namespace

```bash
# Archive (soft-delete all entities, keep data)
curl -X POST .../namespace-groups/dev/archive

# Later: restore from archive
curl -X POST .../namespace-groups/dev/restore

# Permanent deletion (after archive)
curl -X DELETE .../namespace-groups/dev?confirm=true
```

---

## Migration Path

### Phase 1: Add Namespace Fields (Non-Breaking) — COMPLETE

1. ~~Add `namespace` field with default value to all models~~
2. ~~Add indexes for namespace + existing unique constraints~~
3. ~~Deploy updated services~~
4. ~~**No API changes yet** - all requests use default namespace~~

**Implementation note:** The field is named `namespace` (not `pool_id` as originally proposed) and stores the short prefix (e.g., `"wip"`) rather than the full pool name. The Registry client derives pool names by appending the entity type suffix (e.g., `"seed"` → `"seed-terminologies"`).

### Phase 2: API Namespace Parameter (Backward Compatible) — COMPLETE

1. ~~Add optional `namespace` query parameter to all endpoints~~
2. ~~Default to `wip` namespace if not specified~~
3. ~~Update registry client to accept namespace parameter~~
4. ~~Deploy and test~~

**Implementation note:** Create endpoints accept `namespace` in the JSON request body (default: `"wip"`). List/filter endpoints accept it as a query parameter. By-value lookups default to `namespace=None` (search all namespaces) to avoid silent 404s when callers don't know which namespace holds the entity.

### Phase 3: Namespace Group Management — NOT STARTED

1. Add `NamespaceGroup` model to Registry
2. Implement namespace group CRUD endpoints
3. Add validation for cross-namespace references
4. Deploy and test

### Phase 4: Export/Import

1. Implement export API
2. Implement import API with remapping
3. Add CLI commands for backup/restore
4. Deploy and test

### Phase 5: Archive/Delete

1. Implement archive (bulk soft-delete)
2. Implement restore from archive
3. Implement permanent delete with confirmation
4. Deploy and test

---

## CLI Commands

```bash
# Namespace group management
wip namespace list
wip namespace create dev --description "Development testing"
wip namespace info dev
wip namespace archive dev
wip namespace delete dev --confirm

# Export/import
wip export dev --output dev-backup.tar.gz --include-files
wip import dev-backup.tar.gz --as staging --mode create
wip import dev-backup.tar.gz --as wip --mode merge

# Clone namespace
wip clone wip dev  # Clone wip -> dev

# Diff namespaces
wip diff dev wip --format table
```

---

## Security Considerations

### Namespace-Level Permissions

Extend API key and OIDC group permissions to namespace level:

```python
# API key with namespace restrictions
{
    "name": "dev-only-key",
    "namespace_groups": ["dev"],  # Can only access dev-* namespaces
    "groups": ["wip-editors"]
}

# OIDC group mapping
{
    "wip-admins": ["*"],           # All namespaces
    "wip-editors": ["wip"],        # Only wip-* namespaces
    "dev-testers": ["dev"]         # Only dev-* namespaces
}
```

### Cross-Namespace Reference Validation

- By default, allow references within same namespace group
- Cross-group references require explicit flag or permission
- Example: `dev-documents` can reference `dev-templates` freely
- `dev-documents` referencing `wip-templates` requires explicit allowance

---

## Performance Considerations

### Indexing Strategy

Every collection should have:
```python
IndexModel([("namespace", 1), ("primary_key", 1)], unique=True)
IndexModel([("namespace", 1), ("status", 1)])
IndexModel([("namespace", 1), ("created_at", -1)])
```

### Query Optimization

- Always include namespace in queries
- Use compound indexes (namespace + field)
- Avoid cross-namespace joins where possible

### Export/Import Performance

- Stream JSONL for large datasets
- Batch inserts during import (1000 documents at a time)
- Parallel file downloads from MinIO
- Progress reporting for long operations

---

## Open Questions

1. **ID collision on import**: If importing `dev` as `staging`, should IDs be regenerated or kept?
   - Option A: Keep IDs (requires globally unique IDs)
   - Option B: Regenerate IDs (requires reference remapping)
   - **Recommendation**: Keep IDs, they're already globally unique by format

2. **Shared terminologies**: Should some terminologies be global (shared across all namespaces)?
   - Option A: No, always namespace-scoped
   - Option B: Yes, with special `shared-` namespace group
   - **Recommendation**: Start with A, add shared terminologies later if needed

3. **PostgreSQL reporting**: How to handle namespace in reporting sync?
   - Add `namespace` column to all reporting tables
   - Filter syncs by namespace
   - **Recommendation**: Include namespace in reporting from day one

---

## Implementation Estimate

| Phase | Effort | Dependencies |
|-------|--------|--------------|
| Phase 1: Model changes | 2 days | None |
| Phase 2: API changes | 2 days | Phase 1 |
| Phase 3: Namespace groups | 2 days | Phase 2 |
| Phase 4: Export/Import | 3 days | Phase 3 |
| Phase 5: Archive/Delete | 1 day | Phase 3 |
| CLI commands | 2 days | Phase 4 |
| Documentation | 1 day | All |

**Total: ~2 weeks**

---

## Success Criteria

1. Can create `dev` namespace group and use it for all operations
2. Can export `dev` namespace to file
3. Can import namespace to different WIP instance
4. Can archive namespace (soft-delete all data)
5. Can restore archived namespace
6. All existing `wip-*` data continues to work unchanged
7. Performance remains acceptable (queries < 100ms)
