# WIP Def-Store Service

Terminology and ontology management service for the World In a Pie ecosystem.

> **Note:** The curl examples below use direct service ports (e.g., `localhost:8002`) for local development and testing. In production and for application code, always use the Caddy reverse proxy at `https://<hostname>:8443/api/def-store/...` instead.

## Features

- **Terminology Management**: Create and manage controlled vocabularies
- **Term Management**: Add, update, deprecate terms within terminologies
- **Validation API**: Validate values against terminologies
- **Import/Export**: JSON and CSV support for bulk operations
- **Multi-language**: Translation support for internationalization
- **Hierarchical Terms**: Support for parent-child relationships
- **Registry Integration**: Automatic ID generation via WIP Registry
- **Ontology Relationships**: Create typed relationships between terms (`is_a`, `part_of`, `has_part`, `maps_to`, `related_to`, custom). Traverse hierarchies (ancestors, descendants, parents, children) with configurable depth. Cross-terminology relationships supported.

## Quick Start

### Development

```bash
# Ensure Registry service is running first
cd ../registry && podman-compose -f podman-compose.yml up -d --build

# Start Def-Store with hot reload
podman-compose -f podman-compose.yml up --build

# Access the API
curl http://localhost:8002/health

# View API documentation
open http://localhost:8002/docs
```

### Production

```bash
# Set environment variables
export API_KEY=$(openssl rand -hex 32)
export REGISTRY_API_KEY=your_registry_api_key

# Start services
podman-compose up -d
```

## Authentication

All endpoints require API key authentication via the `X-API-Key` header.

### Development API Key

For local development, the default API key is:
```
dev_master_key_for_testing
```

This is configured in `podman-compose.yml` via the `API_KEY` environment variable.

### Using the Swagger UI

1. Open http://localhost:8002/docs
2. Click the **"Authorize"** button (lock icon, top right)
3. Enter the API key: `dev_master_key_for_testing`
4. Click "Authorize" then "Close"
5. Now you can test any endpoint

## API Overview

### Terminologies

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/def-store/terminologies` | List all terminologies |
| POST | `/api/def-store/terminologies` | Create a terminology |
| GET | `/api/def-store/terminologies/{id}` | Get terminology by ID |
| GET | `/api/def-store/terminologies/by-value/{value}` | Get terminology by value |
| PUT | `/api/def-store/terminologies/{id}` | Update terminology |
| DELETE | `/api/def-store/terminologies/{id}` | Delete terminology (soft-delete) |

### Terms

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/def-store/terminologies/{terminology_id}/terms` | List terms in terminology |
| POST | `/api/def-store/terminologies/{terminology_id}/terms` | Create a term |
| GET | `/api/def-store/terminologies/{terminology_id}/terms/{term_id}` | Get term by ID |
| GET | `/api/def-store/terminologies/{terminology_id}/terms/by-value/{value}` | Get term by value |
| PUT | `/api/def-store/terminologies/{terminology_id}/terms/{term_id}` | Update term |
| DELETE | `/api/def-store/terminologies/{terminology_id}/terms/{term_id}` | Delete term (hard-delete, mutable terminologies only) |
| POST | `/api/def-store/terms/{terminology_id}/validate` | Validate a value |
| POST | `/api/def-store/terms/{terminology_id}/bulk` | Create terms in bulk |

### Import/Export

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/def-store/import-export/export/{terminology_id}` | Export terminology |
| GET | `/api/def-store/import-export/export` | Export all terminologies |
| POST | `/api/def-store/import-export/import` | Import terminology |
| POST | `/api/def-store/import-export/import/url` | Import from URL |

### Ontology

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/def-store/ontology/relationships` | Create term relationships (bulk) |
| GET | `/api/def-store/ontology/relationships` | List relationships for a term |
| DELETE | `/api/def-store/ontology/relationships` | Delete relationships (bulk) |
| GET | `/api/def-store/ontology/terms/{id}/ancestors` | Traverse ancestors |
| GET | `/api/def-store/ontology/terms/{id}/descendants` | Traverse descendants |

## Example Usage

### Create a terminology

```bash
curl -X POST http://localhost:8002/api/def-store/terminologies \
  -H "X-API-Key: dev_master_key_for_testing" \
  -H "Content-Type: application/json" \
  -d '[{
    "value": "DOC_STATUS",
    "label": "Document Status",
    "description": "Status codes for documents in the system"
  }]'
```

### Add terms

```bash
curl -X POST http://localhost:8002/api/def-store/terminologies/<terminology_id>/terms \
  -H "X-API-Key: dev_master_key_for_testing" \
  -H "Content-Type: application/json" \
  -d '[{
    "value": "draft",
    "label": "Draft",
    "description": "Document is in draft state",
    "sort_order": 1
  }]'
```

### Validate a value

```bash
curl -X POST http://localhost:8002/api/def-store/terms/TERM-000001/validate \
  -H "X-API-Key: dev_master_key_for_testing" \
  -H "Content-Type: application/json" \
  -d '{
    "value": "draft"
  }'
```

### Export a terminology

```bash
curl http://localhost:8002/api/def-store/import-export/export/TERM-000001?format=json \
  -H "X-API-Key: dev_master_key_for_testing"
```

### Import a terminology

```bash
curl -X POST "http://localhost:8002/api/def-store/import-export/import?format=json" \
  -H "X-API-Key: dev_master_key_for_testing" \
  -H "Content-Type: application/json" \
  -d '{
    "terminology": {
      "value": "PRIORITY",
      "label": "Priority Levels",
      "description": "Task priority levels"
    },
    "terms": [
      {"value": "low", "label": "Low", "sort_order": 1},
      {"value": "medium", "label": "Medium", "sort_order": 2},
      {"value": "high", "label": "High", "sort_order": 3}
    ]
  }'
```

## Data Model

### Terminology

| Field | Type | Description |
|-------|------|-------------|
| `terminology_id` | string | Unique ID from Registry (UUID7) |
| `value` | string | Unique code (e.g., DOC_STATUS) |
| `label` | string | Display name |
| `description` | string | Description |
| `status` | string | active, inactive |
| `namespace` | string | Namespace scope |

### Term

| Field | Type | Description |
|-------|------|-------------|
| `term_id` | string | Unique ID from Registry (UUID7) |
| `terminology_id` | string | Parent terminology ID |
| `value` | string | The canonical value stored/used |
| `label` | string | Display label |
| `aliases` | array | Alternative values that resolve to this term |
| `description` | string | Description |
| `sort_order` | integer | Display order |
| `parent_term_id` | string | Parent term for hierarchies |
| `translations` | array | Labels in other languages |
| `status` | string | active, inactive, deprecated |

## Project Structure

```
def-store/
├── src/def_store/
│   ├── api/                  # API endpoints
│   │   ├── terminologies.py
│   │   ├── terms.py
│   │   ├── import_export.py
│   │   └── auth.py
│   ├── models/               # Data models
│   │   ├── terminology.py
│   │   ├── term.py
│   │   └── api_models.py
│   ├── services/             # Business logic
│   │   ├── registry_client.py
│   │   ├── terminology_service.py
│   │   └── import_export.py
│   └── main.py               # FastAPI application
├── tests/                    # Test suite
├── podman-compose.yml        # Compose configuration
├── podman-compose.override.yml  # Dev overrides (auto-generated)
├── Dockerfile
└── requirements.txt
```

## Registry Integration

Def-Store uses the WIP Registry for ID generation:

| Entity | Namespace | ID Format | Example |
|--------|-----------|-----------|---------|
| Terminology | `wip-terminologies` | TERM-XXXXXX | TERM-000001 |
| Term | `wip-terms` | T-XXXXXX | T-000042 |

Ensure the Registry service is running and WIP namespaces are initialized:
```bash
curl -X POST http://localhost:8001/api/registry/namespaces/initialize-wip \
  -H "X-API-Key: dev_master_key_for_testing"
```

## Testing

```bash
# Install test dependencies
pip install pytest pytest-asyncio httpx

# Run tests (requires MongoDB and Registry)
pytest tests/ -v
```

## License

Part of the World In a Pie project.
