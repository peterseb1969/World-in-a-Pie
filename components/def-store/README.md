# WIP Def-Store Service

Terminology and ontology management service for the World In a Pie ecosystem.

## Features

- **Terminology Management**: Create and manage controlled vocabularies
- **Term Management**: Add, update, deprecate terms within terminologies
- **Validation API**: Validate values against terminologies
- **Import/Export**: JSON and CSV support for bulk operations
- **Multi-language**: Translation support for internationalization
- **Hierarchical Terms**: Support for parent-child relationships
- **Registry Integration**: Automatic ID generation via WIP Registry

## Quick Start

### Development

```bash
# Ensure Registry service is running first
cd ../registry && docker-compose -f docker-compose.yml up -d --build

# Start Def-Store with hot reload
docker-compose -f docker-compose.yml up --build

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
docker-compose up -d
```

## Authentication

All endpoints require API key authentication via the `X-API-Key` header.

### Development API Key

For local development, the default API key is:
```
dev_master_key_for_testing
```

This is configured in `docker-compose.yml` via the `API_KEY` environment variable.

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
| DELETE | `/api/def-store/terminologies/{id}` | Delete terminology |

### Terms

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/def-store/terms/{terminology_id}` | List terms in terminology |
| POST | `/api/def-store/terms/{terminology_id}` | Create a term |
| GET | `/api/def-store/terms/{terminology_id}/{term_id}` | Get term by ID |
| GET | `/api/def-store/terms/{terminology_id}/by-value/{value}` | Get term by value |
| PUT | `/api/def-store/terms/{terminology_id}/{term_id}` | Update term |
| DELETE | `/api/def-store/terms/{terminology_id}/{term_id}` | Delete term |
| POST | `/api/def-store/terms/{terminology_id}/validate` | Validate a value |
| POST | `/api/def-store/terms/{terminology_id}/bulk` | Create terms in bulk |

### Import/Export

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/def-store/import-export/export/{terminology_id}` | Export terminology |
| GET | `/api/def-store/import-export/export` | Export all terminologies |
| POST | `/api/def-store/import-export/import` | Import terminology |
| POST | `/api/def-store/import-export/import/url` | Import from URL |

## Example Usage

### Create a terminology

```bash
curl -X POST http://localhost:8002/api/def-store/terminologies \
  -H "X-API-Key: dev_master_key_for_testing" \
  -H "Content-Type: application/json" \
  -d '{
    "code": "DOC_STATUS",
    "name": "Document Status",
    "description": "Status codes for documents in the system",
    "case_sensitive": false,
    "allow_multiple": false,
    "extensible": true
  }'
```

### Add a term

```bash
curl -X POST http://localhost:8002/api/def-store/terms/TERM-000001 \
  -H "X-API-Key: dev_master_key_for_testing" \
  -H "Content-Type: application/json" \
  -d '{
    "code": "DRAFT",
    "value": "draft",
    "label": "Draft",
    "description": "Document is in draft state",
    "sort_order": 1
  }'
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
      "code": "PRIORITY",
      "name": "Priority Levels",
      "description": "Task priority levels",
      "case_sensitive": false
    },
    "terms": [
      {"code": "LOW", "value": "low", "label": "Low", "sort_order": 1},
      {"code": "MEDIUM", "value": "medium", "label": "Medium", "sort_order": 2},
      {"code": "HIGH", "value": "high", "label": "High", "sort_order": 3}
    ]
  }'
```

## Data Model

### Terminology

| Field | Type | Description |
|-------|------|-------------|
| `terminology_id` | string | Unique ID from Registry (TERM-XXXXXX) |
| `code` | string | Human-friendly code (e.g., DOC_STATUS) |
| `name` | string | Display name |
| `description` | string | Description |
| `case_sensitive` | boolean | Whether values are case-sensitive |
| `allow_multiple` | boolean | Whether multiple values can be selected |
| `extensible` | boolean | Whether users can add new terms |
| `status` | string | active, deprecated, draft |
| `term_count` | integer | Number of terms |

### Term

| Field | Type | Description |
|-------|------|-------------|
| `term_id` | string | Unique ID from Registry (T-XXXXXX) |
| `terminology_id` | string | Parent terminology ID |
| `code` | string | Human-friendly code (e.g., DRAFT) |
| `value` | string | The actual value stored/used |
| `label` | string | Display label |
| `description` | string | Description |
| `sort_order` | integer | Display order |
| `parent_term_id` | string | Parent term for hierarchies |
| `translations` | object | Labels in other languages |
| `status` | string | active, deprecated, draft |

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
├── docker-compose.yml        # Compose configuration
├── docker-compose.override.yml  # Dev overrides (auto-generated)
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
