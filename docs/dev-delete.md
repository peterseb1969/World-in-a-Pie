# Dev Delete

Hard-delete entities from MongoDB, MinIO, and PostgreSQL during development.

**Location:** `scripts/dev-delete.py`

Bypasses soft-delete, removes all versions, and cleans up Registry entries, MinIO blobs, and PostgreSQL rows so IDs can be re-used. Designed for iterative development — delete test data, re-import, repeat.

---

## Quick Start

```bash
# Dry run (default) — shows what would be deleted
python scripts/dev-delete.py 019abc01-def3-7abc-8def-123456789abc

# Actually delete
python scripts/dev-delete.py --force 019abc01-def3-7abc-8def-123456789abc

# Delete with full cascade (terminology -> terms -> relationships,
# template -> child templates -> documents -> files)
python scripts/dev-delete.py --cascade --force COUNTRY

# Delete entire namespace
python scripts/dev-delete.py --namespace myapp --force

# Delete by value prefix
python scripts/dev-delete.py --prefix MYAPP_ --type terminology --force

# List entities in a collection
python scripts/dev-delete.py --list templates
python scripts/dev-delete.py --list documents --limit 20
```

---

## Modes

### ID Mode

Delete one or more entities by WIP ID. Accepts UUIDs, value codes, or prefixed IDs.

```bash
python scripts/dev-delete.py --force 019abc01-... 019def04-...
```

Use `--type` to disambiguate when the ID format is ambiguous:

```bash
python scripts/dev-delete.py --type template --force PATIENT_RECORD
```

Use `--cascade` to delete child entities (terms under a terminology, documents under a template, etc.):

```bash
python scripts/dev-delete.py --cascade --force COUNTRY
```

### Namespace Mode

Delete ALL entities in a namespace. Cascade is implied.

```bash
python scripts/dev-delete.py --namespace myapp --force
```

Cleans up across all databases: `wip_def_store`, `wip_template_store`, `wip_document_store`, `wip_registry`. Also drops PostgreSQL `doc_*` tables and removes MinIO file objects.

### Prefix Mode

Delete entities whose value starts with a prefix. Useful for cleaning up test data with a naming convention.

```bash
python scripts/dev-delete.py --prefix TEST_ --type terminology --force
```

### List Mode

Inspect what's in a collection before deleting.

```bash
python scripts/dev-delete.py --list terminologies
python scripts/dev-delete.py --list documents --namespace myapp --limit 50
```

---

## Backends

The script cleans up three backends:

| Backend | Required | Python Package | What It Cleans |
|---------|----------|----------------|----------------|
| MongoDB | Always | `pymongo` (always available) | All entity collections across 4 databases |
| MinIO | If files exist | `boto3` | File objects in S3-compatible storage |
| PostgreSQL | If reporting data exists | `psycopg2-binary` | `doc_*` tables, terminology/term rows |

### Missing Python Modules

If `boto3` or `psycopg2` is not installed and the data being deleted includes files or reportable entities, the script **aborts with install instructions** rather than silently leaving orphaned data.

To intentionally skip a backend:

```bash
python scripts/dev-delete.py --no-minio --no-postgres --force ...
```

---

## Remote Connections

All backends support remote connections:

```bash
# Remote MongoDB
python scripts/dev-delete.py --mongo-uri mongodb://remote-host:27017/ --force ...

# Remote MinIO
python scripts/dev-delete.py --minio-endpoint http://remote-host:9000 \
    --minio-access-key mykey --minio-secret-key mysecret --force ...

# Remote PostgreSQL
python scripts/dev-delete.py --pg-host remote-host --pg-port 5432 \
    --pg-db wip_reporting --pg-user wip --pg-password secret --force ...
```

MinIO and PostgreSQL connection details also read from environment variables (`WIP_FILE_STORAGE_ENDPOINT`, `POSTGRES_HOST`, etc.) — same as WIP services.

---

## Safety

- **Dry run by default** — without `--force`, nothing is deleted
- **Impact report** — shows exactly what would be deleted before proceeding
- **Backend validation** — aborts if Python modules are missing for required backends
- **Development only** — no undo, no soft-delete, no audit trail
