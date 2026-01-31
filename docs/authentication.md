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

## Quick Start

### Default: API Key Only

By default, WIP services use API key authentication. No additional configuration is needed:

```bash
# Start infrastructure
podman-compose -f docker-compose.infra.yml up -d

# Start services (they use API_KEY env var)
cd components/def-store && podman-compose -f docker-compose.dev.yml up -d
# ... repeat for other services

# Make authenticated requests
curl http://localhost:8002/api/def-store/terminologies \
  -H "X-API-Key: dev_master_key_for_testing"
```

### With User Authentication (Authelia)

To enable user login with JWT tokens:

1. **Enable Authelia** in `docker-compose.infra.yml`:

   Uncomment the Authelia service section:
   ```yaml
   authelia:
     image: authelia/authelia:latest
     container_name: wip-authelia
     volumes:
       - ./config/authelia:/config:ro
     ports:
       - "9091:9091"
     # ...
   ```

2. **Restart infrastructure**:
   ```bash
   podman-compose -f docker-compose.infra.yml up -d
   ```

3. **Configure services** to use dual mode:
   ```bash
   export WIP_AUTH_MODE=dual
   export WIP_AUTH_JWT_ISSUER_URL=http://authelia:9091
   export WIP_AUTH_JWT_AUDIENCE=wip
   ```

4. **Access Authelia**: http://localhost:9091

## Configuration Reference

### Environment Variables

All auth settings use the `WIP_AUTH_` prefix:

| Variable | Default | Description |
|----------|---------|-------------|
| `WIP_AUTH_MODE` | `api_key_only` | Auth mode: `none`, `api_key_only`, `jwt_only`, `dual` |
| `WIP_AUTH_LEGACY_API_KEY` | - | API key for backward compatibility |
| `WIP_AUTH_JWT_ISSUER_URL` | - | OIDC issuer URL (e.g., `http://authelia:9091`) |
| `WIP_AUTH_JWT_JWKS_URI` | - | JWKS endpoint (auto-derived from issuer if not set) |
| `WIP_AUTH_JWT_AUDIENCE` | `wip` | Expected JWT audience claim |
| `WIP_AUTH_JWT_GROUPS_CLAIM` | `groups` | JWT claim containing user groups |
| `WIP_AUTH_API_KEY_HEADER` | `X-API-Key` | HTTP header for API key |
| `WIP_AUTH_DEFAULT_GROUPS` | `wip-users` | Default groups for authenticated users |
| `WIP_AUTH_ADMIN_GROUPS` | `wip-admins` | Groups considered admin |

### Legacy Compatibility

For backward compatibility, these env vars are automatically mapped:
- `API_KEY` → `WIP_AUTH_LEGACY_API_KEY`
- `MASTER_API_KEY` → `WIP_AUTH_LEGACY_API_KEY`

## Authelia Setup

### Default Users

The development configuration includes these users:

| Username | Password | Groups |
|----------|----------|--------|
| `admin` | `admin123` | wip-admins, wip-editors, wip-viewers |
| `editor` | `editor123` | wip-editors, wip-viewers |
| `viewer` | `viewer123` | wip-viewers |

### Adding Users

Edit `config/authelia/users.yml`:

```yaml
users:
  newuser:
    displayname: New User
    password: '$argon2id$...'  # Generate with command below
    email: newuser@example.com
    groups:
      - wip-editors
```

Generate password hash:
```bash
docker run authelia/authelia:latest authelia crypto hash generate argon2 --password 'your_password'
```

### Changing Client Secret

The OIDC client secret for WIP Console is in `config/authelia/configuration.yml`.

Generate a new secret:
```bash
# Generate hash for use in configuration.yml
docker run authelia/authelia:latest authelia crypto hash generate pbkdf2 \
  --variant sha512 --password 'your_new_secret'
```

## Groups and Permissions

WIP uses group-based access control:

| Group | Purpose |
|-------|---------|
| `wip-admins` | Full administrative access |
| `wip-editors` | Create and modify content |
| `wip-viewers` | Read-only access |
| `wip-services` | Service-to-service access |

### Using Groups in Code

```python
from fastapi import Depends
from wip_auth import require_identity, require_groups, require_admin

# Require any authenticated user
@app.get("/api/data")
async def get_data(identity = Depends(require_identity())):
    return {"user": identity.username}

# Require specific group
@app.post("/api/data")
async def create_data(identity = Depends(require_groups(["wip-editors"]))):
    return {"created_by": identity.identity_string}

# Require admin
@app.delete("/api/data/{id}")
async def delete_data(id: str, identity = Depends(require_admin())):
    return {"deleted_by": identity.username}
```

## API Authentication

### Using API Keys

```bash
curl http://localhost:8002/api/def-store/terminologies \
  -H "X-API-Key: your_api_key"
```

### Using JWT Tokens

First, obtain a token from Authelia:

```bash
# OAuth2 Password Grant (for testing)
curl -X POST http://localhost:9091/api/oidc/token \
  -d "grant_type=password" \
  -d "client_id=wip-console" \
  -d "client_secret=wip_console_secret" \
  -d "username=admin" \
  -d "password=admin123" \
  -d "scope=openid profile email groups"
```

Then use the token:

```bash
curl http://localhost:8002/api/def-store/terminologies \
  -H "Authorization: Bearer eyJhbG..."
```

## Troubleshooting

### "Invalid API key" Error

- Check that `WIP_AUTH_LEGACY_API_KEY` or `API_KEY` is set correctly
- Verify the header name matches `WIP_AUTH_API_KEY_HEADER`

### "Token validation failed" Error

- Check `WIP_AUTH_JWT_ISSUER_URL` matches Authelia's URL
- Verify the token hasn't expired
- Check `WIP_AUTH_JWT_AUDIENCE` matches the token's audience

### Authelia Won't Start

- Ensure `config/authelia/` directory exists with configuration files
- Check volume mount in docker-compose.yml points to correct path
- Review Authelia logs: `podman logs wip-authelia`

### JWT Claims Missing Groups

- Check `WIP_AUTH_JWT_GROUPS_CLAIM` matches the claim name in your tokens
- Authelia uses `groups` by default
- Other providers may use `roles` or custom claims

## Production Considerations

### Security Checklist

- [ ] Change default passwords in `config/authelia/users.yml`
- [ ] Generate new client secret for WIP Console
- [ ] Use HTTPS for all services
- [ ] Set `authorization_policy: two_factor` in Authelia config
- [ ] Configure proper session storage (Redis/PostgreSQL)
- [ ] Set up notification provider (SMTP) for password resets

### Scaling

For production deployments:

1. **Use external database** for Authelia:
   ```yaml
   storage:
     postgres:
       host: postgres
       database: authelia
       username: authelia
       password: ${AUTHELIA_DB_PASSWORD}
   ```

2. **Use Redis for sessions**:
   ```yaml
   session:
     redis:
       host: redis
       port: 6379
   ```

3. **Configure SMTP** for notifications:
   ```yaml
   notifier:
     smtp:
       host: smtp.example.com
       port: 587
       username: authelia@example.com
       password: ${SMTP_PASSWORD}
       sender: "WIP Auth <authelia@example.com>"
   ```

## Alternative Providers

The wip-auth library works with any OIDC-compliant provider:

### Authentik

```bash
WIP_AUTH_MODE=dual
WIP_AUTH_JWT_ISSUER_URL=http://authentik:9000/application/o/wip/
WIP_AUTH_JWT_AUDIENCE=wip
```

### Keycloak

```bash
WIP_AUTH_MODE=dual
WIP_AUTH_JWT_ISSUER_URL=http://keycloak:8080/realms/wip
WIP_AUTH_JWT_AUDIENCE=wip
```

### Generic OIDC

```bash
WIP_AUTH_MODE=jwt_only
WIP_AUTH_JWT_ISSUER_URL=https://auth.example.com/
WIP_AUTH_JWT_AUDIENCE=wip
WIP_AUTH_JWT_GROUPS_CLAIM=groups  # or 'roles', 'permissions', etc.
```
