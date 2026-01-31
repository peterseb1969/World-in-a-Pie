# WIP Authentication Guide

This guide explains how to configure authentication for World In a Pie (WIP) services.

## Introduction: What is API Authentication?

When you call a WIP API (like `GET /api/def-store/terminologies`), the service needs to know **who you are** and **whether you're allowed** to make that request. This is called **API authentication**.

### Two Separate Authentication Layers

WIP has **two independent authentication layers**:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                         │
│   You (Browser/Script)          WIP Service              Database       │
│   ────────────────────          ───────────              ────────       │
│                                                                         │
│   curl/browser  ────── API Auth ──────►  Def-Store  ──── DB Auth ──► MongoDB
│                       (this guide)                     (MONGO_URI)      │
│                                                                         │
│   1. API Auth: Who can call the REST API? (X-API-Key or JWT)           │
│   2. DB Auth:  How does the service connect to MongoDB? (MONGO_URI)     │
│                                                                         │
│   These are INDEPENDENT - this guide only covers API Auth (#1)          │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

**This guide only covers API authentication** - how clients authenticate to WIP services. Database authentication (MongoDB, PostgreSQL) is configured separately via connection strings and is not affected by the settings described here.

### Authentication Methods Explained

#### Method 1: API Keys

An **API key** is a secret string that you include in your HTTP request. It's like a password for your application.

```bash
curl http://localhost:8002/api/def-store/terminologies \
  -H "X-API-Key: dev_master_key_for_testing"
           ▲
           └── This is the API key
```

**How it works:**
1. You send a request with the `X-API-Key` header
2. The service checks if the key matches the configured key
3. If it matches, the request is allowed

**Best for:** Service-to-service communication (e.g., Document-Store calling Template-Store), scripts, and simple setups.

#### Method 2: JWT Tokens (via OIDC)

A **JWT (JSON Web Token)** is a token that proves you logged in with a username and password. It's issued by an **identity provider** (like Authelia) after you authenticate.

**What is Authelia?**
Authelia is an open-source identity provider - a service that:
- Stores usernames and passwords
- Lets users log in
- Issues JWT tokens that prove "this user logged in successfully"

**What is OIDC?**
OIDC (OpenID Connect) is a standard protocol for authentication. Many identity providers support it (Authelia, Authentik, Keycloak, Auth0, Google, etc.). WIP can work with any OIDC-compliant provider.

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         JWT Authentication Flow                          │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   1. User logs in to Authelia                                            │
│      ┌────────┐         ┌──────────┐                                     │
│      │  User  │ ──────► │ Authelia │  "I'm admin, password is admin123"  │
│      └────────┘         └────┬─────┘                                     │
│                              │                                           │
│   2. Authelia returns a JWT token                                        │
│                              │                                           │
│                              ▼                                           │
│      "Here's your token: eyJhbGciOiJS..."                                │
│                                                                          │
│   3. User sends token to WIP service                                     │
│      ┌────────┐         ┌───────────┐                                    │
│      │  User  │ ──────► │ Def-Store │  Authorization: Bearer eyJhbG...   │
│      └────────┘         └─────┬─────┘                                    │
│                               │                                          │
│   4. Service validates token with Authelia's public key                  │
│                               │                                          │
│                               ▼                                          │
│      "Token is valid, user is 'admin' with groups [wip-admins]"          │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

**Best for:** User authentication (real people logging in), web applications, and when you need user identity information (who created this document?).

### Comparison

| Aspect | API Key | JWT Token |
|--------|---------|-----------|
| **What it is** | A secret string | A token from login |
| **Who uses it** | Services, scripts | Logged-in users |
| **How you get it** | Configured in environment | Login to Authelia |
| **User identity** | Just "valid key" | Full user info (name, email, groups) |
| **Expiration** | Never (unless you change it) | Short-lived (e.g., 1 hour) |
| **Header** | `X-API-Key: xxx` | `Authorization: Bearer xxx` |

## Authentication Modes

WIP supports four authentication modes:

| Mode | Description | When to Use |
|------|-------------|-------------|
| `none` | No authentication - all requests allowed | Local development, testing |
| `api_key_only` | Only API key authentication (default) | Service-to-service, scripts, simple setups |
| `jwt_only` | Only JWT tokens from Authelia/OIDC | User-facing apps (no service accounts) |
| `dual` | Both API keys AND JWT tokens accepted | Production with users AND service accounts |

**Default:** `api_key_only` - this is the current behavior, no changes needed.

## Where to Set Environment Variables

Environment variables are set in the **docker-compose files** for each service.

### Service Docker-Compose Files

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
      # Existing settings (database, etc.)
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

### Scenario 1: API Key Only (Default)

This is the current default behavior. **No changes needed** - existing `API_KEY` env var works.

**Each service's docker-compose.dev.yml:**
```yaml
environment:
  - API_KEY=dev_master_key_for_testing
  # OR explicitly:
  - WIP_AUTH_MODE=api_key_only
  - WIP_AUTH_LEGACY_API_KEY=dev_master_key_for_testing
```

**Usage:**
```bash
curl http://localhost:8002/api/def-store/terminologies \
  -H "X-API-Key: dev_master_key_for_testing"
```

### Scenario 2: No Authentication (Development)

For local development without any auth checks. All requests are allowed.

**Each service's docker-compose.dev.yml:**
```yaml
environment:
  - WIP_AUTH_MODE=none
```

**Usage:**
```bash
# No header needed
curl http://localhost:8002/api/def-store/terminologies
```

### Scenario 3: Dual Mode with Authelia (User Login)

For user authentication via OIDC while still allowing API keys for services.

**Step 1:** Enable Authelia in `docker-compose.infra.yml`:

Uncomment the Authelia service section:
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

```yaml
environment:
  # ... existing settings ...

  # Authentication
  - WIP_AUTH_MODE=dual
  - WIP_AUTH_LEGACY_API_KEY=dev_master_key_for_testing
  - WIP_AUTH_JWT_ISSUER_URL=http://wip-authelia:9091
  - WIP_AUTH_JWT_AUDIENCE=wip
```

**Step 4:** Restart services:
```bash
cd components/def-store && podman-compose -f docker-compose.dev.yml down && podman-compose -f docker-compose.dev.yml up -d
# Repeat for other services
```

**Usage with API key:**
```bash
curl http://localhost:8002/api/def-store/terminologies \
  -H "X-API-Key: dev_master_key_for_testing"
```

**Usage with JWT token:**
```bash
# First, get a token by logging in to Authelia
# Then use the token:
curl http://localhost:8002/api/def-store/terminologies \
  -H "Authorization: Bearer eyJhbGciOiJS..."
```

## Environment Variable Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `WIP_AUTH_MODE` | `api_key_only` | Auth mode: `none`, `api_key_only`, `jwt_only`, `dual` |
| `WIP_AUTH_LEGACY_API_KEY` | - | API key value (plain text) |
| `WIP_AUTH_JWT_ISSUER_URL` | - | Authelia URL (e.g., `http://wip-authelia:9091`) |
| `WIP_AUTH_JWT_AUDIENCE` | `wip` | Expected JWT audience claim |
| `WIP_AUTH_JWT_GROUPS_CLAIM` | `groups` | JWT claim containing user groups |
| `WIP_AUTH_ADMIN_GROUPS` | `wip-admins` | Groups considered admin |

### Legacy Compatibility

The old `API_KEY` environment variable still works:
```yaml
environment:
  - API_KEY=dev_master_key_for_testing  # Still works!
```

## Authelia Default Users

When using Authelia, these test users are pre-configured in `config/authelia/users.yml`:

| Username | Password | Groups |
|----------|----------|--------|
| `admin` | `admin123` | wip-admins, wip-editors, wip-viewers |
| `editor` | `editor123` | wip-editors, wip-viewers |
| `viewer` | `viewer123` | wip-viewers |

## Testing Authentication

### Test API Key Auth

```bash
# Should succeed (200 OK)
curl http://localhost:8002/api/def-store/terminologies \
  -H "X-API-Key: dev_master_key_for_testing"

# Should fail (401 Unauthorized)
curl http://localhost:8002/api/def-store/terminologies \
  -H "X-API-Key: wrong_key"
```

### Test No Auth Mode

```bash
# With WIP_AUTH_MODE=none, no header needed
curl http://localhost:8002/api/def-store/terminologies
```

## Troubleshooting

### "Invalid API key" Error

1. Check the environment variable is set correctly:
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

Use the container name `wip-authelia` (not `localhost`) in the issuer URL:
```yaml
- WIP_AUTH_JWT_ISSUER_URL=http://wip-authelia:9091  # Correct
- WIP_AUTH_JWT_ISSUER_URL=http://localhost:9091     # Wrong (from inside container)
```

## Summary

| I want to... | Set `WIP_AUTH_MODE` to... | Also set... |
|--------------|---------------------------|-------------|
| Disable auth for testing | `none` | Nothing else needed |
| Use API keys only (default) | `api_key_only` | `WIP_AUTH_LEGACY_API_KEY` or `API_KEY` |
| Use Authelia for user login | `dual` | `WIP_AUTH_JWT_ISSUER_URL`, enable Authelia |
| Use only JWT (no API keys) | `jwt_only` | `WIP_AUTH_JWT_ISSUER_URL`, enable Authelia |
