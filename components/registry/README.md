# WIP Registry Service

Federated identity management service for the World In a Pie ecosystem.

## Features

- **Namespace Management**: Logical partitions for ID isolation
- **Composite Key Registration**: Register any combination of fields as an identity
- **Synonym Support**: Multiple keys can resolve to the same entity
- **ID-as-Synonym (Merge)**: Resolve duplicate registrations
- **Cross-Namespace Search**: Find entities across all namespaces
- **Pluggable ID Generation**: UUID4, UUID7, NanoID, Prefixed, or Custom
- **API Key Authentication**: Secure access to all endpoints

## Quick Start

### Development

```bash
# Start with hot reload
docker-compose -f docker-compose.dev.yml up

# Access the API
curl http://localhost:8001/health

# View API documentation
open http://localhost:8001/docs
```

### Production

```bash
# Set environment variables
export MASTER_API_KEY=$(openssl rand -hex 32)
export MONGO_PASSWORD=your_secure_password

# Start services
docker-compose up -d
```

## API Overview

All endpoints require API key authentication via the `X-API-Key` header.

### Namespaces

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/registry/namespaces` | List all namespaces |
| POST | `/api/registry/namespaces` | Create namespaces (bulk) |
| GET | `/api/registry/namespaces/{id}` | Get namespace |
| PUT | `/api/registry/namespaces/{id}` | Update namespace |
| DELETE | `/api/registry/namespaces/{id}` | Delete namespace |
| POST | `/api/registry/namespaces/initialize-wip` | Initialize WIP internal namespaces |

### Entries

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/registry/entries/register` | Register composite keys (bulk) |
| POST | `/api/registry/entries/lookup/by-id` | Lookup by ID (bulk) |
| POST | `/api/registry/entries/lookup/by-key` | Lookup by composite key (bulk) |
| PUT | `/api/registry/entries` | Update entries (bulk) |
| DELETE | `/api/registry/entries` | Delete entries (bulk) |

### Synonyms

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/registry/synonyms/add` | Add synonyms (bulk) |
| POST | `/api/registry/synonyms/remove` | Remove synonyms (bulk) |
| POST | `/api/registry/synonyms/merge` | Merge entries (bulk) |
| POST | `/api/registry/synonyms/set-preferred` | Change preferred ID (bulk) |

### Search

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/registry/search/by-fields` | Search by field values (bulk) |
| POST | `/api/registry/search/by-term` | Free-text search (bulk) |
| POST | `/api/registry/search/across-namespaces` | Search all namespaces (bulk) |

## Example Usage

### Register a composite key

```bash
curl -X POST http://localhost:8001/api/registry/entries/register \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '[{
    "namespace": "default",
    "composite_key": {"product_id": "PROD-001", "region": "EU"}
  }]'
```

### Add a synonym

```bash
curl -X POST http://localhost:8001/api/registry/synonyms/add \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '[{
    "target_namespace": "default",
    "target_id": "your_registry_id",
    "synonym_namespace": "vendor1",
    "synonym_composite_key": {"vendor_sku": "V1-SKU-001"}
  }]'
```

### Search across namespaces

```bash
curl -X POST http://localhost:8001/api/registry/search/across-namespaces \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '[{
    "field_criteria": {"vendor_sku": "V1-SKU-001"}
  }]'
```

## Project Structure

```
registry/
├── config/
│   ├── config.yaml          # Production config
│   └── config.dev.yaml      # Development config
├── src/registry/
│   ├── api/                  # API endpoints
│   │   ├── namespaces.py
│   │   ├── entries.py
│   │   ├── synonyms.py
│   │   └── search.py
│   ├── models/               # Data models
│   │   ├── namespace.py
│   │   ├── entry.py
│   │   └── api_models.py
│   ├── services/             # Business logic
│   │   ├── id_generator.py
│   │   ├── hash.py
│   │   ├── search.py
│   │   └── auth.py
│   └── main.py               # FastAPI application
├── tests/                    # Test suite
├── docker-compose.yml        # Production compose
├── docker-compose.dev.yml    # Development compose
├── Dockerfile
└── requirements.txt
```

## WIP Internal Namespaces

The registry pre-configures these namespaces for WIP components:

| Namespace | ID Generator | Purpose |
|-----------|--------------|---------|
| `default` | UUID4 | General use |
| `wip-terminologies` | Prefixed (TERM-) | Terminology IDs |
| `wip-terms` | Prefixed (T-) | Term IDs |
| `wip-templates` | Prefixed (TPL-) | Template IDs |
| `wip-documents` | UUID7 | Document IDs |

Initialize these with:
```bash
curl -X POST http://localhost:8001/api/registry/namespaces/initialize-wip \
  -H "X-API-Key: your_admin_key"
```

## Testing

```bash
# Install test dependencies
pip install pytest pytest-asyncio httpx

# Run tests (requires MongoDB)
pytest tests/ -v
```

## License

Part of the World In a Pie project.
