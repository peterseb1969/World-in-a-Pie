# Namespace Implementation

How namespace scoping works in WIP today.

---

## Overview

Every entity in WIP (terminology, term, template, document, file) belongs to a **namespace** — a short string prefix like `"wip"` or `"seed"` that isolates data sets from each other on the same instance.

The default namespace is `"wip"`. The seed script uses `"seed"` to keep test data separate from production data.

```
WIP Instance
├── Namespace: wip          (production data, system terminologies)
│   ├── Terminologies: GENDER, COUNTRY, DOC_STATUS, ...
│   ├── Templates: PERSON, EMPLOYEE, ...
│   └── Documents: ...
│
└── Namespace: seed         (test data from seed script)
    ├── Terminologies: GENDER, COUNTRY, DEPARTMENT, ...
    ├── Templates: PERSON, EMPLOYEE, MINIMAL, ...
    └── Documents: 500+ seeded documents
```

---

## Model Storage

All MongoDB models store namespace as a required field:

```python
# Same pattern across all services
namespace: str = Field(
    ...,
    description="Namespace for data isolation (e.g., wip, dev, seed)"
)
```

| Service | Model | Field | Required |
|---------|-------|-------|----------|
| Def-Store | `Terminology` | `namespace` | Yes |
| Def-Store | `Term` | `namespace` | Yes |
| Template-Store | `Template` | `namespace` | Yes |
| Document-Store | `WIPDocument` | `namespace` | Yes |
| Document-Store | `FileMetadata` | `namespace` | Yes |

Namespace is also included in all API response models (`TerminologyResponse`, `TemplateResponse`, `DocumentResponse`, etc.).

---

## API Patterns

### Create Endpoints — Namespace in Request Body

All POST (create) endpoints accept `namespace` in the JSON request body:

```bash
# Create terminology in "seed" namespace
curl -X POST http://localhost:8002/api/def-store/terminologies \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev_master_key_for_testing" \
  -d '{
    "value": "GENDER",
    "label": "Gender",
    "namespace": "seed",
    "created_by": "seed_script"
  }'

# Namespace is required on all writes — omitting it returns a validation error
curl -X POST http://localhost:8002/api/def-store/terminologies \
  -d '{"value": "GENDER", "label": "Gender", "namespace": "wip", "created_by": "admin"}'
```

The same pattern applies across all services:

| Endpoint | Request Model | Namespace Field |
|----------|--------------|-----------------|
| `POST /api/def-store/terminologies` | `CreateTerminologyRequest` | `namespace: str` (required) |
| `POST /api/def-store/terminologies/{id}/terms` | — | Inherited from parent terminology |
| `POST /api/template-store/templates` | `CreateTemplateRequest` | `namespace: str` (required) |
| `POST /api/document-store/documents` | `DocumentCreateRequest` | `namespace: str` (required) |
| `POST /api/document-store/files` | `Form(...)` | `namespace` form field (required) |

**Terms inherit namespace from their terminology** — you don't specify namespace when creating terms.

### List/Filter Endpoints — Namespace as Query Parameter

All GET (list) endpoints accept `namespace` as an optional query parameter:

```bash
# List terminologies in "seed" namespace only
curl "http://localhost:8002/api/def-store/terminologies?namespace=seed"

# List all terminologies across all namespaces (omit parameter)
curl "http://localhost:8002/api/def-store/terminologies"
```

When `namespace` is omitted (or `None`), results include entities from **all namespaces**.

### By-Value Lookups — Global by Default

Endpoints like `GET /terminologies/by-value/{value}` and `GET /templates/by-value/{value}` default to `namespace=None` (search all namespaces):

```bash
# Find GENDER terminology regardless of namespace
curl "http://localhost:8002/api/def-store/terminologies/by-value/GENDER"

# Find GENDER only in "seed" namespace
curl "http://localhost:8002/api/def-store/terminologies/by-value/GENDER?namespace=seed"
```

This design prevents silent 404 errors when a caller doesn't know which namespace holds an entity.

---

## Registry Integration

The Registry generates IDs and stores entries with both `namespace` and `entity_type` as separate fields.

### How Services Register Entities

When a service creates an entity, it calls the Registry with:

```json
{
  "namespace": "seed",
  "entity_type": "terminologies",
  "composite_key": {
    "value": "GENDER",
    "label": "Gender"
  }
}
```

The Registry uses these to:
1. Look up the namespace's ID generation config for that entity type
2. Generate a counter key: `"{namespace}:{entity_type}:{prefix}"` (e.g., `"seed:terminologies:TERM-"`)
3. Atomically increment the counter and return the ID (e.g., `SEED-LOV-000001`)

### Namespace-Scoped ID Prefixes

Custom namespaces get modified ID prefixes to avoid collisions:

| Namespace | Entity Type | ID Format |
|-----------|-------------|-----------|
| `wip` | terminologies | UUID7 (default) |
| `wip` | terms | UUID7 (default) |
| `wip` | templates | UUID7 (default) |
| `wip` | documents | UUID7 (default) |
| `wip` | files | UUID7 (default) |
| custom | (any) | Configurable via `id_config` — UUID7, prefixed (e.g., `SEED-LOV-000001`), nanoid, pattern |

The `wip` namespace uses UUID7 for all entity types. Custom namespaces can configure per-entity-type ID algorithms including prefixed IDs with custom prefixes and padding.

### Auto-Creation of Namespaces

When the Registry receives a request for an unknown namespace, it auto-creates the namespace with appropriate ID configuration. The `wip` namespace is pre-configured during `POST /api/registry/namespaces/initialize-wip`.

---

## Seed Script Usage

The seed script (`scripts/seed_comprehensive.py`) defaults to `--namespace seed`:

```bash
# Default: creates data in "seed" namespace
python scripts/seed_comprehensive.py --profile standard

# Explicit namespace
python scripts/seed_comprehensive.py --profile standard --namespace dev

# Use production namespace (same as system terminologies)
python scripts/seed_comprehensive.py --profile standard --namespace wip
```

The script passes namespace in request bodies for creates and as query params for lookups:

```python
# Creating a terminology
create_data = {
    "value": "GENDER",
    "label": "Gender",
    "namespace": self.namespace,  # In request body
    ...
}
self.def_store.post("/api/def-store/terminologies", create_data)

# Looking up by value
self.def_store.get(
    "/api/def-store/terminologies/by-value/GENDER",
    params={"namespace": self.namespace}  # As query param
)
```

---

## Cross-Service Lookups

When one service needs to validate references in another service's data, it passes namespace through:

**Template-Store → Def-Store** (validating terminology references):
```python
# Template-store's def_store_client passes namespace as query param
params = {"namespace": namespace} if namespace else None
response = await client.get(
    f"{self.base_url}/api/def-store/terminologies/by-value/{value}",
    params=params
)
```

**Document-Store → Template-Store** (fetching template for validation):
Template lookups use `template_id` (globally unique), so namespace is not needed for ID-based lookups.

---

## Service Layer Query Pattern

All service methods follow the same pattern — only add namespace to the MongoDB query if it's not `None`:

```python
async def list_terminologies(
    namespace: Optional[str] = None,
    status: Optional[str] = None,
    ...
) -> tuple[list[TerminologyResponse], int]:
    query: dict = {}
    if namespace is not None:
        query["namespace"] = namespace
    if status:
        query["status"] = status
    # ... execute query
```

This means:
- `namespace="seed"` → only entities in the seed namespace
- `namespace=None` → entities from all namespaces

---

## What's Not Yet Implemented

Namespace provides basic data isolation today. The following features from the [namespace-scoped-data design](design/namespace-scoped-data.md) are not yet implemented:

| Feature | Status |
|---------|--------|
| Namespace group management API | Not started |
| Namespace export/import | Not started |
| Namespace archive/delete | Implemented (see `docs/design/namespace-deletion.md`) |
| Per-namespace access control | Implemented (Auth Phase 2, commit `0e548f3`) |
| Cross-namespace reference validation | Not started |
| CLI commands (`wip namespace list`, etc.) | Not started |
| PostgreSQL reporting namespace column | Not started |

---

## Verifying Namespace Isolation

```bash
# Check which namespaces exist in MongoDB
podman exec wip-mongodb mongosh --quiet --eval \
  'db.getSiblingDB("wip_def_store").terminologies.distinct("namespace")'
# → [ "seed", "wip" ]

# Count entities per namespace
podman exec wip-mongodb mongosh --quiet --eval \
  'db.getSiblingDB("wip_def_store").terminologies.aggregate([
    {$group: {_id: "$namespace", count: {$sum: 1}}}
  ]).toArray()'

# Verify API response includes namespace
curl -s "http://localhost:8002/api/def-store/terminologies?limit=1" \
  -H "X-API-Key: dev_master_key_for_testing" | python3 -m json.tool
# → each item has "namespace": "wip" or "namespace": "seed"
```
