# WIP Registry Service

Identity management service for the World In a Pie ecosystem.

> **Note:** The curl examples below use direct service ports (e.g., `localhost:8001`) for local development and testing. In production and for application code, always use the Caddy reverse proxy at `https://<hostname>:8443/api/registry/...` instead.

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
podman-compose -f docker-compose.yml up --build

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
podman-compose up -d
```

## Authentication

All endpoints require API key authentication via the `X-API-Key` header.

Beyond that, entries are authorized by namespace:
- **Allocation** (`/entries/register`, `/provision`, `/reserve`, `/activate`,
  `PUT` and `DELETE /entries`) is restricted to `wip-admins` / `wip-services`.
  It is the stores' internal protocol; apps create entities through their
  store.
- **Synonym add/remove and merge** need write on every namespace involved.
- **Reads** (entry get, lookups, resolve, search) return only entries in
  namespaces the caller can read; anything else is reported not found.

### Development API Key

For local development, the default API key is:
```
dev_master_key_for_testing
```

This is configured in `docker-compose.yml` via the `MASTER_API_KEY` environment variable.

### Using the Swagger UI

1. Open http://localhost:8001/docs
2. Click the **"Authorize"** button (lock icon, top right)
3. Enter the API key: `dev_master_key_for_testing`
4. Click "Authorize" then "Close"
5. Now you can test any endpoint

### Production API Key

Generate a secure key for production:
```bash
export MASTER_API_KEY=$(openssl rand -hex 32)
```

## API Overview

### Namespaces

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/registry/namespaces` | List all namespaces |
| POST | `/api/registry/namespaces` | Create namespaces (bulk) |
| GET | `/api/registry/namespaces/{id}` | Get namespace |
| PUT | `/api/registry/namespaces/{id}` | Update namespace |
| DELETE | `/api/registry/namespaces/{id}` | Delete namespace (**hard-delete**) |
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
| POST | `/api/registry/synonyms/merge` | Merge entries (bulk) — source ID becomes synonym of target, all synonyms migrate. Can be reversed by removing the synonym, but the deprecated entry remains inactive until a reactivation endpoint is added. |
| POST | `/api/registry/synonyms/set-preferred` | Change preferred ID (bulk) |

### Search

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/registry/search/by-fields` | Search by field values (bulk) |
| POST | `/api/registry/search/by-term` | Free-text search (bulk) |
| POST | `/api/registry/search/across-namespaces` | Search all namespaces (bulk) |

> **For service developers:** Services should resolve identities via `wip_auth/resolve.py` rather than calling the Registry REST API directly. See `docs/design/universal-synonym-resolution.md`.

## Example Usage

### Register a composite key

```bash
curl -X POST http://localhost:8001/api/registry/entries/register \
  -H "X-API-Key: dev_master_key_for_testing" \
  -H "Content-Type: application/json" \
  -d '[{
    "namespace": "wip",
    "entity_type": "terminologies",
    "composite_key": {"product_id": "PROD-001", "region": "EU"}
  }]'
```

### Add a synonym

```bash
curl -X POST http://localhost:8001/api/registry/synonyms/add \
  -H "X-API-Key: dev_master_key_for_testing" \
  -H "Content-Type: application/json" \
  -d '[{
    "target_id": "your_registry_id",
    "synonym_namespace": "vendor1",
    "synonym_entity_type": "terminologies",
    "synonym_composite_key": {"vendor_sku": "V1-SKU-001"}
  }]'
```

### Search across namespaces

```bash
curl -X POST http://localhost:8001/api/registry/search/across-namespaces \
  -H "X-API-Key: dev_master_key_for_testing" \
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
├── docker-compose.yml        # Compose configuration
├── docker-compose.override.yml  # Dev overrides (auto-generated)
├── Dockerfile
└── requirements.txt
```

## WIP Namespace

The registry pre-configures the `wip` namespace with per-entity-type ID algorithms:

| Entity Type | Default Algorithm | Purpose |
|-------------|-------------------|---------|
| `terminologies` | UUID7 | Terminology IDs |
| `terms` | UUID7 | Term IDs |
| `templates` | UUID7 | Template IDs |
| `documents` | UUID7 | Document IDs |
| `files` | UUID7 | File IDs |

ID algorithms are configurable per namespace per entity type (UUID4, UUID7, NanoID, Prefixed, Pattern). For example, a namespace could use `"algorithm": "prefixed", "prefix": "PROD-"` to generate IDs like `PROD-000001`.

Initialize with:
```bash
curl -X POST http://localhost:8001/api/registry/namespaces/initialize-wip \
  -H "X-API-Key: dev_master_key_for_testing"
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
