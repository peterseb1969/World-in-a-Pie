# WIP Auth Library

A pluggable authentication library for World In a Pie (WIP) services.

## Features

- **Multiple auth modes**: `none`, `api_key_only`, `jwt_only`, `dual`
- **Pluggable providers**: API keys, OIDC/JWT, or custom
- **FastAPI integration**: Middleware and dependencies
- **Backward compatible**: Works with existing `X-API-Key` authentication
- **OIDC support**: Works with Authelia, Authentik, Zitadel, or any OIDC provider

## Installation

```bash
# From the WIP project root
pip install -e libs/wip-auth

# Or add to requirements.txt
-e ../libs/wip-auth
```

## Quick Start

### Basic Setup (API Key Only)

```python
from fastapi import FastAPI, Depends
from wip_auth import setup_auth, require_identity

app = FastAPI()

# Setup auth - reads from WIP_AUTH_* environment variables
setup_auth(app)

@app.get("/protected")
async def protected_route(identity = Depends(require_identity())):
    return {"user": identity.username, "groups": identity.groups}
```

Environment variables:
```bash
export WIP_AUTH_MODE=api_key_only
export WIP_AUTH_LEGACY_API_KEY=dev_master_key_for_testing
```

### Dual Mode (API Key + JWT)

```python
from wip_auth import setup_auth, require_identity, require_admin

app = FastAPI()
setup_auth(app)

@app.get("/api/data")
async def get_data(identity = Depends(require_identity())):
    # Accepts both API key (X-API-Key header) and JWT (Authorization: Bearer)
    return {"user": identity.username}

@app.get("/api/admin")
async def admin_only(identity = Depends(require_admin())):
    # Requires wip-admins group
    return {"admin": identity.username}
```

Environment variables:
```bash
export WIP_AUTH_MODE=dual
export WIP_AUTH_LEGACY_API_KEY=dev_master_key_for_testing
export WIP_AUTH_JWT_ISSUER_URL=http://authelia:9091
export WIP_AUTH_JWT_AUDIENCE=wip
```

## Configuration

All settings are read from environment variables with the `WIP_AUTH_` prefix:

| Variable | Default | Description |
|----------|---------|-------------|
| `WIP_AUTH_MODE` | `api_key_only` | Auth mode: `none`, `api_key_only`, `jwt_only`, `dual` |
| `WIP_AUTH_LEGACY_API_KEY` | - | Single API key for backward compatibility |
| `WIP_AUTH_API_KEYS_FILE` | - | Path to JSON file with API key definitions |
| `WIP_AUTH_API_KEY_HEADER` | `X-API-Key` | Header name for API key auth |
| `WIP_AUTH_JWT_PROVIDER` | `generic_oidc` | OIDC provider name (for logging) |
| `WIP_AUTH_JWT_ISSUER_URL` | - | OIDC issuer URL |
| `WIP_AUTH_JWT_JWKS_URI` | - | Explicit JWKS endpoint (derived from issuer if not set) |
| `WIP_AUTH_JWT_AUDIENCE` | `wip` | Expected JWT audience |
| `WIP_AUTH_JWT_GROUPS_CLAIM` | `groups` | JWT claim containing user groups |
| `WIP_AUTH_DEFAULT_GROUPS` | `wip-users` | Groups for users without explicit groups |
| `WIP_AUTH_ADMIN_GROUPS` | `wip-admins` | Groups considered admin |

### API Keys File Format

```json
{
  "keys": [
    {
      "name": "service-key",
      "key_hash": "sha256-hash-of-key",
      "owner": "reporting-sync",
      "groups": ["wip-services"],
      "description": "Key for Reporting Sync service"
    }
  ]
}
```

Generate key hash:
```python
from wip_auth import hash_api_key
print(hash_api_key("your_secret_key"))
```

## Auth Modes

### `none` - Development Mode

All requests are allowed with an anonymous identity. Use for local development.

```bash
WIP_AUTH_MODE=none
```

### `api_key_only` - Service Authentication (Default)

Only API key authentication via `X-API-Key` header. This is the default mode
for backward compatibility with existing WIP services.

```bash
WIP_AUTH_MODE=api_key_only
WIP_AUTH_LEGACY_API_KEY=dev_master_key_for_testing
```

### `jwt_only` - User Authentication

Only JWT/OIDC authentication via `Authorization: Bearer` header. Requires
an OIDC provider like Authelia.

```bash
WIP_AUTH_MODE=jwt_only
WIP_AUTH_JWT_ISSUER_URL=http://authelia:9091
WIP_AUTH_JWT_AUDIENCE=wip
```

### `dual` - Both Methods

Accepts both API keys and JWT tokens. JWT is checked first, then API key.
Use this for production with both service-to-service and user authentication.

```bash
WIP_AUTH_MODE=dual
WIP_AUTH_LEGACY_API_KEY=service_key_here
WIP_AUTH_JWT_ISSUER_URL=http://authelia:9091
```

## Dependencies

### `require_identity()`

Require any authenticated identity. Returns 401 if not authenticated.

```python
@app.get("/protected")
async def route(identity = Depends(require_identity())):
    return {"user": identity.user_id}
```

### `require_groups(groups, require_all=False)`

Require specific group membership. Returns 403 if missing required groups.

```python
@app.get("/editors")
async def route(identity = Depends(require_groups(["wip-editors", "wip-admins"]))):
    return {"editor": identity.username}
```

### `require_admin()`

Shortcut for `require_groups(["wip-admins"])`.

```python
@app.delete("/dangerous")
async def route(identity = Depends(require_admin())):
    return {"admin": identity.username}
```

### `optional_identity()`

Get identity if authenticated, None otherwise. Does not require auth.

```python
@app.get("/public")
async def route(identity = Depends(optional_identity())):
    if identity:
        return {"greeting": f"Hello, {identity.username}"}
    return {"greeting": "Hello, anonymous"}
```

### `require_api_key()` (Legacy)

Alias for `require_identity()` for backward compatibility.

## UserIdentity Model

The identity object returned by dependencies:

```python
class UserIdentity:
    user_id: str          # Unique identifier
    username: str         # Display name
    email: str | None     # Email (if available)
    groups: list[str]     # Group memberships
    auth_method: str      # "jwt", "api_key", or "none"
    provider: str | None  # Provider name
    raw_claims: dict      # Original JWT claims or key metadata

    @property
    def identity_string(self) -> str:
        # For created_by/updated_by fields
        # Returns: "user:<id>", "apikey:<name>", or "anonymous"
```

## Synonym Resolution

The `wip_auth.resolve` module provides universal synonym resolution — converting human-readable identifiers to canonical UUIDs via the Registry.

### Functions

```python
from wip_auth.resolve import resolve_entity_id, resolve_entity_ids, EntityNotFoundError

# Single resolution
canonical_id = await resolve_entity_id("STATUS", "terminology", "wip")

# Batch resolution
id_map = await resolve_entity_ids(["STATUS", "GENDER"], "terminology", "wip")
# Returns: {"STATUS": "019abc...", "GENDER": "019def..."}
```

**`resolve_entity_id(raw_id, entity_type, namespace)`** — Returns `raw_id` unchanged if it's already a UUID. Otherwise, resolves via the Registry's `POST /resolve` endpoint. Raises `EntityNotFoundError` if not found or Registry is unreachable.

**`resolve_entity_ids(raw_ids, entity_type, namespace)`** — Batch variant. UUIDs pass through, cached results are reused, remaining IDs are resolved in a single Registry call. Returns `{raw_id: canonical_id}` dict.

**`EntityNotFoundError`** — Raised when resolution fails. Has `.identifier` and `.entity_type` attributes.

### Usage Pattern in Services

At the API boundary, use `contextlib.suppress` for best-effort resolution:

```python
from contextlib import suppress
from wip_auth.resolve import resolve_entity_id, EntityNotFoundError

@router.get("/terms")
async def list_terms(terminology_id: str, namespace: str = "wip"):
    with suppress(EntityNotFoundError):
        terminology_id = await resolve_entity_id(
            terminology_id, "terminology", namespace,
        )
    # If resolution fails, the raw value passes through —
    # downstream logic handles the "not found" case naturally
    return await service.list_terms(terminology_id)
```

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `WIP_AUTH_REGISTRY_URL` | `REGISTRY_URL` or `http://localhost:8001` | Registry URL for resolution |
| `REGISTRY_API_KEY` | `API_KEY` or `dev_master_key_for_testing` | API key for Registry calls |

### Caching

Results are cached in-process with a 5-minute TTL. Use `clear_resolution_cache()` in tests.

---

## Testing

For testing, you can set up auth with a specific configuration:

```python
import pytest
from fastapi.testclient import TestClient
from wip_auth import AuthConfig, setup_auth, set_auth_config, reset_auth_config

@pytest.fixture
def test_config():
    config = AuthConfig(
        mode="api_key_only",
        legacy_api_key="test_key",
    )
    set_auth_config(config)
    yield config
    reset_auth_config()

@pytest.fixture
def client(test_config):
    app = FastAPI()
    setup_auth(app)
    # ... add routes ...
    return TestClient(app)

def test_protected_route(client):
    response = client.get("/protected", headers={"X-API-Key": "test_key"})
    assert response.status_code == 200
```

## Migration from Existing Auth

Existing WIP services use a simple pattern in `api/auth.py`:

```python
# Old pattern
API_KEY = os.getenv("API_KEY", "dev_master_key_for_testing")

async def require_api_key(api_key: str = Security(API_KEY_HEADER)) -> str:
    if api_key != API_KEY:
        raise HTTPException(401, "Invalid API key")
    return api_key
```

To migrate:

1. Add wip-auth to requirements.txt:
   ```
   -e ../../libs/wip-auth
   ```

2. Update main.py:
   ```python
   from wip_auth import setup_auth

   app = FastAPI()
   setup_auth(app)
   ```

3. Update api/auth.py to re-export from wip-auth:
   ```python
   from wip_auth import require_identity, require_api_key, optional_identity
   ```

4. Set environment variables (optional - uses existing API_KEY by default):
   ```bash
   # These are auto-mapped for backward compatibility:
   # API_KEY -> WIP_AUTH_LEGACY_API_KEY
   # MASTER_API_KEY -> WIP_AUTH_LEGACY_API_KEY
   ```

No changes to route handlers needed - `require_api_key` still works.
