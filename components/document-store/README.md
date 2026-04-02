# WIP Document Store Service

The Document Store service manages documents that conform to templates defined in the Template Store. It provides document validation, versioning, and identity-based upsert logic.

> **Note:** The curl examples below use direct service ports (e.g., `localhost:8004`) for local development and testing. In production and for application code, always use the Caddy reverse proxy at `https://<hostname>:8443/api/document-store/...` instead.

## Features

- **Template Validation**: Validates documents against template schemas
- **Identity-Based Upsert**: Automatic versioning based on identity hash
- **Six-Stage Validation Pipeline**:
  1. Structural validation
  2. Template resolution
  3. Field type validation
  4. Term validation (via Def-Store)
  5. Cross-field rule evaluation
  6. Identity hash computation
- **Version History**: Track all versions of a document
- **Bulk Operations**: Create multiple documents in one request

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/documents` | Create/update document (upsert) |
| GET | `/documents` | List with pagination |
| GET | `/documents/{id}` | Get document by ID |
| GET | `/documents/{id}/versions` | Get all versions |
| GET | `/documents/{id}/versions/{v}` | Get specific version |
| DELETE | `/documents/{id}` | Soft-delete (deactivate) |
| POST | `/documents/{id}/archive` | Archive document |
| POST | `/documents/query` | Complex query with filters |
| POST | `/documents/bulk` | Bulk create/update |
| POST | `/validation/validate` | Validate without saving |
| GET | `/documents/by-identity/{hash}` | Get by identity hash |

### File Storage

Document Store manages binary files via MinIO (when the `files` module is enabled).

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/files` | Upload a file |
| GET | `/files` | List files |
| GET | `/files/{id}` | Get file metadata |
| GET | `/files/{id}/download` | Download file |
| DELETE | `/files/{id}` | Soft-delete file |

Files can be linked to documents via template field references. Orphan detection tracks files not referenced by any document.

### CSV/XLSX Import

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/import/preview` | Preview file structure and headers |
| POST | `/import` | Import documents from CSV/XLSX |

Import supports column-to-field mapping, automatic term value resolution (human-readable values resolved to term IDs), and `skip_errors` mode for partial imports.

## Quick Start

### Development

```bash
# Start infrastructure (MongoDB)
cd ../..
podman-compose -f docker-compose.infra.yml up -d

# Start Registry service
cd components/registry
podman-compose -f docker-compose.yml up -d --build

# Initialize WIP namespaces
curl -X POST http://localhost:8001/api/registry/namespaces/initialize-wip \
  -H "X-API-Key: dev_master_key_for_testing"

# Start Def-Store service
cd ../def-store
podman-compose -f docker-compose.yml up -d --build

# Start Template Store service
cd ../template-store
podman-compose -f docker-compose.yml up -d --build

# Start Document Store service
cd ../document-store
podman-compose -f docker-compose.yml up -d --build
```

### API Documentation

Once running, access the OpenAPI documentation at:
- Swagger UI: http://localhost:8004/docs
- ReDoc: http://localhost:8004/redoc

## Usage Examples

### Create a Document

```bash
curl -X POST https://localhost:8443/api/document-store/documents \
  -H "X-API-Key: ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '[{
    "template_id": "TPL-000001",
    "namespace": "wip",
    "data": {
      "national_id": "123456789",
      "first_name": "John",
      "last_name": "Doe",
      "birth_date": "1990-01-15"
    },
    "created_by": "admin"
  }]'
```

### Validate a Document

```bash
curl -X POST http://localhost:8004/api/document-store/validation/validate \
  -H "X-API-Key: dev_master_key_for_testing" \
  -H "Content-Type: application/json" \
  -d '{
    "template_id": "TPL-000001",
    "data": {
      "national_id": "123456789",
      "first_name": "John",
      "last_name": "Doe"
    }
  }'
```

### Query Documents

```bash
curl -X POST http://localhost:8004/api/document-store/documents/query \
  -H "X-API-Key: dev_master_key_for_testing" \
  -H "Content-Type: application/json" \
  -d '{
    "template_id": "TPL-000001",
    "filters": [
      {"field": "data.first_name", "operator": "eq", "value": "John"}
    ],
    "page": 1,
    "page_size": 20
  }'
```

## Upsert Logic

Documents use identity-based upsert logic:

1. **Identity Fields**: Template defines which fields form the composite identity
2. **Identity Hash**: SHA-256 hash of identity field values
3. **New Document**: If no active document exists with the same identity hash
4. **New Version**: If active document exists:
   - Old version is marked as `inactive`
   - New version is created with incremented version number

```
Document Submission
        │
        ▼
Compute identity_hash = SHA256(sorted identity fields)
        │
        ▼
Find existing: {identity_hash: X, status: "active"}
        │
   ┌────┴────┐
   │         │
Not Found  Found
   │         │
   ▼         ▼
CREATE     UPDATE
version=1  - Deactivate old
           - Create new (version + 1)
```

## Running Tests

```bash
# Inside the container
podman exec -it wip-document-store bash -c \
  "pip install pytest pytest-asyncio httpx && pytest /app/tests -v"

# Or locally with virtual environment
pytest tests/ -v
```

## Configuration

| Environment Variable | Description | Default |
|---------------------|-------------|---------|
| `MONGO_URI` | MongoDB connection string | `mongodb://localhost:27017/` |
| `DATABASE_NAME` | Database name | `wip_document_store` |
| `API_KEY` | API key for authentication | `dev_master_key_for_testing` |
| `REGISTRY_URL` | Registry service URL | `http://localhost:8001` |
| `REGISTRY_API_KEY` | Registry API key | `dev_master_key_for_testing` |
| `TEMPLATE_STORE_URL` | Template Store URL | `http://localhost:8003` |
| `TEMPLATE_STORE_API_KEY` | Template Store API key | `dev_master_key_for_testing` |
| `DEF_STORE_URL` | Def-Store URL | `http://localhost:8002` |
| `DEF_STORE_API_KEY` | Def-Store API key | `dev_master_key_for_testing` |
| `CORS_ORIGINS` | Allowed CORS origins | `*` |

## Integration with Other Services

- **Registry Service** (port 8001): Generates UUID7 document IDs
- **Template Store** (port 8003): Fetches templates for validation
- **Def-Store** (port 8002): Validates term field values

## Port

- Development: `8004`
- Container name: `wip-document-store`
