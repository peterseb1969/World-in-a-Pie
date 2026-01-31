# WIP Authentication Guide

This guide explains how to configure authentication for World In a Pie (WIP) services.

## Overview

WIP uses a pluggable authentication system that supports multiple modes:

| Mode | Description | Use Case |
|------|-------------|----------|
| `none` | No authentication required | Local development |
| `api_key_only` | API key via `X-API-Key` header (default) | Service-to-service |
| `jwt_only` | JWT tokens via `Authorization: Bearer` | User authentication |
| `dual` | Both API keys and JWT tokens | Production with users |

## Where to Set Environment Variables

Environment variables are set in the **docker-compose files** for each service.

### Service Docker-Compose Files

Each service has its own `docker-compose.dev.yml` (or `docker-compose.yml` for production):

| Service | File Location |
|---------|---------------|
| Def-Store | `components/def-store/docker-compose.dev.yml` |
| Template-Store | `components/template-store/docker-compose.dev.yml` |
| Document-Store | `components/document-store/docker-compose.dev.yml` |
| Reporting-Sync | `components/reporting-sync/docker-compose.dev.yml` |

### Example: Configuring Def-Store

Edit `components/def-store/docker-compose.dev.yml`:

```yaml
services:
  def-store:
    # ... other settings ...
    environment:
      # Existing settings
      - MONGO_URI=mongodb://wip-mongodb:27017/
      - DATABASE_NAME=wip_def_store_dev
      - REGISTRY_URL=http://wip-registry-dev:8001
      - REGISTRY_API_KEY=dev_master_key_for_testing
      - CORS_ORIGINS=*
      - PYTHONPATH=/app/src

      # Authentication settings (add these)
      - WIP_AUTH_MODE=api_key_only           # or: none, jwt_only, dual
      - WIP_AUTH_LEGACY_API_KEY=dev_master_key_for_testing

      # For JWT/OIDC (only needed if mode is jwt_only or dual)
      # - WIP_AUTH_JWT_ISSUER_URL=http://wip-authelia:9091
      # - WIP_AUTH_JWT_AUDIENCE=wip
```

## Configuration Scenarios

### Scenario 1: Default (API Key Only)

This is the current default behavior. No changes needed - existing `API_KEY` env var works.

**Each service's docker-compose.dev.yml:**
```yaml
environment:
  - API_KEY=dev_master_key_for_testing
  # OR explicitly:
  - WIP_AUTH_MODE=api_key_only
  - WIP_AUTH_LEGACY_API_KEY=dev_master_key_for_testing
```

### Scenario 2: No Authentication (Development)

For local development without any auth checks.

**Each service's docker-compose.dev.yml:**
```yaml
environment:
  - WIP_AUTH_MODE=none
```

### Scenario 3: With Authelia (User Login)

For user authentication via OIDC.

**Step 1:** Enable Authelia in `docker-compose.infra.yml`:

Uncomment the Authelia service:
```yaml
services:
  # ... other services ...

  authelia:
    image: authelia/authelia:latest
    container_name: wip-authelia
    volumes:
      - ./config/authelia:/config:ro
    ports:
      - "9091:9091"
    environment:
      TZ: UTC
    networks:
      - wip-network
    restart: unless-stopped
```

**Step 2:** Restart infrastructure:
```bash
podman-compose -f docker-compose.infra.yml up -d
```

**Step 3:** Update **each service's** docker-compose.dev.yml:

`components/def-store/docker-compose.dev.yml`:
```yaml
environment:
  - WIP_AUTH_MODE=dual
  - WIP_AUTH_LEGACY_API_KEY=dev_master_key_for_testing
  - WIP_AUTH_JWT_ISSUER_URL=http://wip-authelia:9091
  - WIP_AUTH_JWT_AUDIENCE=wip
```

`components/template-store/docker-compose.dev.yml`:
```yaml
environment:
  - WIP_AUTH_MODE=dual
  - WIP_AUTH_LEGACY_API_KEY=dev_master_key_for_testing
  - WIP_AUTH_JWT_ISSUER_URL=http://wip-authelia:9091
  - WIP_AUTH_JWT_AUDIENCE=wip
```

`components/document-store/docker-compose.dev.yml`:
```yaml
environment:
  - WIP_AUTH_MODE=dual
  - WIP_AUTH_LEGACY_API_KEY=dev_master_key_for_testing
  - WIP_AUTH_JWT_ISSUER_URL=http://wip-authelia:9091
  - WIP_AUTH_JWT_AUDIENCE=wip
```

**Step 4:** Restart services:
```bash
cd components/def-store && podman-compose -f docker-compose.dev.yml up -d
cd ../template-store && podman-compose -f docker-compose.dev.yml up -d
cd ../document-store && podman-compose -f docker-compose.dev.yml up -d
```

## Environment Variable Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `WIP_AUTH_MODE` | `api_key_only` | Auth mode: `none`, `api_key_only`, `jwt_only`, `dual` |
| `WIP_AUTH_LEGACY_API_KEY` | - | API key value (plain text) |
| `WIP_AUTH_JWT_ISSUER_URL` | - | OIDC issuer URL (e.g., `http://wip-authelia:9091`) |
| `WIP_AUTH_JWT_AUDIENCE` | `wip` | Expected JWT audience claim |
| `WIP_AUTH_JWT_GROUPS_CLAIM` | `groups` | JWT claim containing user groups |
| `WIP_AUTH_ADMIN_GROUPS` | `wip-admins` | Groups considered admin |

### Legacy Compatibility

The old `API_KEY` environment variable still works and is automatically mapped:
- `API_KEY` → `WIP_AUTH_LEGACY_API_KEY`

So this still works:
```yaml
environment:
  - API_KEY=dev_master_key_for_testing
```

## Quick Reference: Full docker-compose.dev.yml Examples

### Def-Store with Dual Auth

`components/def-store/docker-compose.dev.yml`:
```yaml
version: "3.8"

services:
  def-store:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: wip-def-store-dev
    ports:
      - "8002:8002"
    environment:
      # Database
      - MONGO_URI=mongodb://wip-mongodb:27017/
      - DATABASE_NAME=wip_def_store_dev

      # Service dependencies
      - REGISTRY_URL=http://wip-registry-dev:8001
      - REGISTRY_API_KEY=dev_master_key_for_testing

      # CORS
      - CORS_ORIGINS=*

      # Python
      - PYTHONPATH=/app/src

      # Authentication
      - WIP_AUTH_MODE=dual
      - WIP_AUTH_LEGACY_API_KEY=dev_master_key_for_testing
      - WIP_AUTH_JWT_ISSUER_URL=http://wip-authelia:9091
      - WIP_AUTH_JWT_AUDIENCE=wip

    volumes:
      - ./src:/app/src:ro
      - ./tests:/app/tests:ro
    command: uvicorn def_store.main:app --host 0.0.0.0 --port 8002 --reload
    networks:
      - wip-network

networks:
  wip-network:
    external: true
```

## Authelia Default Users

The development configuration (`config/authelia/users.yml`) includes:

| Username | Password | Groups |
|----------|----------|--------|
| `admin` | `admin123` | wip-admins, wip-editors, wip-viewers |
| `editor` | `editor123` | wip-editors, wip-viewers |
| `viewer` | `viewer123` | wip-viewers |

## Testing Authentication

### Test API Key Auth

```bash
# Should succeed
curl http://localhost:8002/api/def-store/terminologies \
  -H "X-API-Key: dev_master_key_for_testing"

# Should fail (401)
curl http://localhost:8002/api/def-store/terminologies \
  -H "X-API-Key: wrong_key"
```

### Test JWT Auth (requires Authelia)

```bash
# Get token from Authelia
TOKEN=$(curl -s -X POST http://localhost:9091/api/oidc/token \
  -d "grant_type=password" \
  -d "client_id=wip-console" \
  -d "client_secret=wip_console_secret" \
  -d "username=admin" \
  -d "password=admin123" \
  -d "scope=openid profile email groups" | jq -r '.access_token')

# Use token
curl http://localhost:8002/api/def-store/terminologies \
  -H "Authorization: Bearer $TOKEN"
```

## Troubleshooting

### "Invalid API key" Error

1. Check the environment variable is set in docker-compose:
   ```bash
   podman exec wip-def-store-dev env | grep -E "(API_KEY|WIP_AUTH)"
   ```

2. Verify the key matches what you're sending in the header

### Services Not Picking Up New Environment Variables

Restart the service after changing docker-compose.yml:
```bash
podman-compose -f docker-compose.dev.yml down
podman-compose -f docker-compose.dev.yml up -d
```

### Authelia Not Reachable from Services

Make sure Authelia is on the same Docker network (`wip-network`) and use the container name `wip-authelia` in the issuer URL:
```yaml
- WIP_AUTH_JWT_ISSUER_URL=http://wip-authelia:9091  # NOT localhost
```
