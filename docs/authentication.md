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
curl -k https://localhost:8443/api/def-store/terminologies \
  -H "X-API-Key: dev_master_key_for_testing"
           ▲
           └── This is the API key
```

**How it works:**
1. You send a request with the `X-API-Key` header
2. The service checks if the key matches a configured key
3. If it matches, the request is allowed with the key's identity and groups

**Best for:** Service-to-service communication (e.g., Document-Store calling Template-Store), scripts, and simple setups.

#### Method 2: JWT Tokens (via OIDC)

A **JWT (JSON Web Token)** is a token that proves you logged in with a username and password. It's issued by an **identity provider** (like Dex) after you authenticate.

**What is Dex?**
Dex is a lightweight open-source identity provider (~30MB RAM) that:
- Stores usernames and passwords (in a YAML config file)
- Issues JWT tokens that prove "this user logged in successfully"
- Works over HTTP (no HTTPS/certificates required for development)
- Supports the standard OIDC protocol

**What is OIDC?**
OIDC (OpenID Connect) is a standard protocol for authentication. Many identity providers support it (Dex, Authelia, Authentik, Keycloak, Auth0, Google, etc.). WIP can work with any OIDC-compliant provider.

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         JWT Authentication Flow                          │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   1. User requests a token from Dex                                      │
│      ┌────────┐         ┌──────────┐                                     │
│      │  User  │ ──────► │   Dex    │  "I'm admin@wip.local, pw admin123" │
│      └────────┘         └────┬─────┘                                     │
│                              │                                           │
│   2. Dex validates and returns a JWT token                               │
│                              │                                           │
│                              ▼                                           │
│      "Here's your token: eyJhbGciOiJS..."                                │
│                                                                          │
│   3. User sends token to WIP service                                     │
│      ┌────────┐         ┌───────────┐                                    │
│      │  User  │ ──────► │ Def-Store │  Authorization: Bearer eyJhbG...   │
│      └────────┘         └─────┬─────┘                                    │
│                               │                                          │
│   4. Service validates token with Dex's public key (JWKS)                │
│                               │                                          │
│                               ▼                                          │
│      "Token is valid, user is 'admin' with email admin@wip.local"        │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

**Best for:** User authentication (real people logging in), web applications, and when you need user identity information (who created this document?).

### Comparison

| Aspect | API Key | JWT Token |
|--------|---------|-----------|
| **What it is** | A secret string | A token from login |
| **Who uses it** | Services, scripts | Logged-in users |
| **How you get it** | Configured in environment | Request from Dex |
| **User identity** | Named key with owner/groups | Full user info (name, email, groups) |
| **Expiration** | Never (unless you change it) | Short-lived (24 hours by default) |
| **Header** | `X-API-Key: xxx` | `Authorization: Bearer xxx` |

## Authentication Modes

WIP supports four authentication modes:

| Mode | Description | When to Use |
|------|-------------|-------------|
| `none` | No authentication - all requests allowed | Local development, testing |
| `api_key_only` | Only API key authentication | Service-to-service, scripts, simple setups |
| `jwt_only` | Only JWT tokens from Dex/OIDC | User-facing apps (no service accounts) |
| `dual` | Both API keys AND JWT tokens accepted | **Recommended** - users AND service accounts |

**Current default:** `dual` - accepts both API keys and JWT tokens.

---

## Quick Start

### Get a JWT Token

```bash
# Request a token from Dex (password grant - for development/testing)
curl -s -X POST http://localhost:5556/dex/token \
  -d "grant_type=password" \
  -d "username=admin@wip.local" \
  -d "password=admin123" \
  -d "client_id=wip-console" \
  -d "client_secret=wip-console-secret" \
  -d "scope=openid profile email"
```

Response:
```json
{
  "access_token": "eyJhbGciOiJSUzI1NiIs...",
  "token_type": "bearer",
  "expires_in": 86399,
  "id_token": "eyJhbGciOiJSUzI1NiIs..."
}
```

### Use the Token

```bash
# Extract token and call API
TOKEN=$(curl -s -X POST http://localhost:5556/dex/token \
  -d "grant_type=password&username=admin@wip.local&password=admin123" \
  -d "client_id=wip-console&client_secret=wip-console-secret&scope=openid profile email" \
  | jq -r '.access_token')

curl -k https://localhost:8443/api/def-store/terminologies \
  -H "Authorization: Bearer $TOKEN"
```

### Or Use an API Key

```bash
curl -k https://localhost:8443/api/def-store/terminologies \
  -H "X-API-Key: dev_master_key_for_testing"
```

---

## Dex User Management

Dex uses **static configuration** for users - they are defined in a YAML file, not a database. This is simple and lightweight, perfect for development and small deployments.

### User Configuration File

Users are defined in `config/dex/config.yaml`. Note that this file is **generated by `scripts/setup.sh`** based on your deployment configuration. Manual edits may be overwritten.

```yaml
# Static users (for development and minimal deployments)
# Passwords are bcrypt hashed
staticPasswords:
  - email: admin@wip.local
    hash: "$2a$10$..."
    username: admin
    userID: "admin-001"
  - email: editor@wip.local
    hash: "$2a$10$..."
    username: editor
    userID: "editor-001"
  - email: viewer@wip.local
    hash: "$2a$10$..."
    username: viewer
    userID: "viewer-001"
```

### Default Test Users

| Email | Username | Password | User ID |
|-------|----------|----------|---------|
| `admin@wip.local` | admin | `admin123` | admin-001 |
| `editor@wip.local` | editor | `editor123` | editor-001 |
| `viewer@wip.local` | viewer | `viewer123` | viewer-001 |

### Adding a New User

**Step 1:** Generate a bcrypt password hash:

```bash
# Using Python (in a container to avoid dependency issues)
podman run --rm python:3.11-slim python3 -c "
import subprocess
subprocess.run(['pip', 'install', '-q', 'bcrypt'])
import bcrypt
password = b'mynewpassword123'
hash = bcrypt.hashpw(password, bcrypt.gensalt())
print(hash.decode())
"
```

Output: `$2b$12$xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

**Step 2:** Add the user to `config/dex/config.yaml`:

```yaml
staticPasswords:
  # ... existing users ...

  - email: newuser@wip.local
    hash: "$2b$12$xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    username: newuser
    userID: "newuser-001"
```

**Step 3:** Restart Dex:

```bash
podman-compose -f docker-compose.infra.yml restart dex
```

**Step 4:** Test the new user:

```bash
curl -s -X POST http://localhost:5556/dex/token \
  -d "grant_type=password&username=newuser@wip.local&password=mynewpassword123" \
  -d "client_id=wip-console&client_secret=wip-console-secret&scope=openid profile email"
```

### Changing a User's Password

**Step 1:** Generate a new bcrypt hash (see above)

**Step 2:** Update the `hash` field in `config/dex/config.yaml`

**Step 3:** Restart Dex:

```bash
podman-compose -f docker-compose.infra.yml restart dex
```

### Removing a User

**Step 1:** Remove or comment out the user entry in `config/dex/config.yaml`

**Step 2:** Restart Dex

**Note:** Existing tokens for the removed user will remain valid until they expire (24 hours by default). To immediately invalidate tokens, restart the WIP services as well.

---

## Groups and Role-Based Access Control

WIP supports group-based access control through both API keys and JWT tokens.

### How Groups Work

Groups are used to control access to specific operations:

| Dependency | Description | Example Use |
|------------|-------------|-------------|
| `require_identity()` | Any authenticated request | Read operations |
| `require_groups(["wip-editors"])` | Must have specific group | Write operations |
| `require_admin()` | Must be in admin group | Delete operations |

### Default Groups

| Group | Description |
|-------|-------------|
| `wip-admins` | Full administrative access |
| `wip-editors` | Can create and modify data |
| `wip-viewers` | Read-only access |
| `wip-users` | Default group for all authenticated users |

### Groups via API Keys

API keys can have groups assigned. The legacy API key (`dev_master_key_for_testing`) automatically gets admin groups.

```json
// Example API key configuration
{
  "name": "etl-service",
  "key": "secret_key_here",
  "owner": "system:etl",
  "groups": ["wip-editors"]
}
```

### Groups via JWT

JWT tokens can include a groups claim (configured via `WIP_AUTH_JWT_GROUPS_CLAIM`). The groups from the token are used for authorization.

**Note:** Dex with static passwords doesn't include groups by default. To get groups in JWT tokens, you need to:
1. Use a Dex connector that supports groups (LDAP, GitHub, etc.)
2. Or switch to Authentik (full user/group management UI)
3. Or use API keys with explicit groups for service accounts

---

## Named API Keys

WIP supports multiple named API keys with different permissions. This is useful for:
- Different services with different access levels
- Audit trail showing which key was used
- Revoking specific keys without affecting others

### Configuration Options

**Option 1: Single legacy key (backward compatible)**

```yaml
environment:
  - WIP_AUTH_LEGACY_API_KEY=dev_master_key_for_testing
```

**Option 2: JSON in environment variable**

```yaml
environment:
  - WIP_AUTH_API_KEYS_JSON=[{"name":"admin","key":"secret1","owner":"admin@wip.local","groups":["wip-admins"]},{"name":"etl","key":"secret2","owner":"system:etl","groups":["wip-editors"]}]
```

**Option 3: JSON file**

```yaml
environment:
  - WIP_AUTH_API_KEYS_FILE=/etc/wip/api-keys.json
```

Example file (`api-keys.json`):
```json
{
  "keys": [
    {
      "name": "admin-console",
      "key": "plaintext_key_here",
      "owner": "admin@wip.local",
      "groups": ["wip-admins"],
      "description": "Admin console access"
    },
    {
      "name": "etl-service",
      "key": "another_key_here",
      "owner": "system:etl",
      "groups": ["wip-editors"],
      "description": "ETL pipeline service account"
    }
  ]
}
```

### API Key Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Unique name for the key |
| `key` | Yes* | Plaintext key (hashed on load) |
| `key_hash` | Yes* | Pre-hashed key (alternative to `key`) |
| `owner` | No | Owner identifier (user or service) |
| `groups` | No | Groups/roles for this key |
| `description` | No | Human-readable description |
| `enabled` | No | Whether key is active (default: true) |

*Either `key` or `key_hash` is required.

### Identity Tracking

When a request is authenticated, the identity is available for audit:

```python
# In your service code
from wip_auth import get_current_identity

identity = get_current_identity()
print(identity.identity_string)  # "apikey:etl-service" or "user:admin-001"
```

The `identity_string` format:
- API key: `apikey:<key_name>`
- JWT user: `user:<user_id>`
- No auth: `anonymous`

This is used for `created_by` and `updated_by` fields in documents.

---

## OAuth2 Clients

Dex issues tokens to **OAuth2 clients** - applications that request tokens on behalf of users.

### Configured Clients

```yaml
staticClients:
  # Web application client
  - id: wip-console
    name: WIP Console
    secret: wip-console-secret
    redirectURIs:
      - http://localhost:3000/auth/callback
      - http://localhost:3000/auth/silent-renew
      - https://localhost:8443/auth/callback
      - https://localhost:8443/auth/silent-renew
    public: false
```

### Adding a New Client

Edit `config/dex/config.yaml` and add to `staticClients`:

```yaml
staticClients:
  # ... existing clients ...

  - id: my-new-app
    name: My New Application
    secret: my-app-secret-change-in-production
    redirectURIs:
      - http://localhost:4000/callback
    public: false
```

Restart Dex to apply changes.

---

## Service Configuration

### Current Configuration (All Services)

All WIP services are configured for **dual mode** in their `docker-compose.yml`:

```yaml
environment:
  # Authentication - dual mode allows both API keys and JWT tokens
  - WIP_AUTH_MODE=dual
  - WIP_AUTH_LEGACY_API_KEY=dev_master_key_for_testing
  # Issuer URL matches what's in the token (localhost for browser/CLI compatibility)
  - WIP_AUTH_JWT_ISSUER_URL=http://localhost:5556/dex
  # JWKS fetched via host.containers.internal (reachable from inside container)
  - WIP_AUTH_JWT_JWKS_URI=http://host.containers.internal:5556/dex/keys
  - WIP_AUTH_JWT_AUDIENCE=wip-console
```

**Why the split configuration?**
- `WIP_AUTH_JWT_ISSUER_URL` uses `localhost` because that's what appears in the JWT token (and is accessible from browser/CLI)
- `WIP_AUTH_JWT_JWKS_URI` uses `host.containers.internal` because containers need to reach the host machine to fetch the signing keys

This configuration allows tokens to work from both browser/CLI and containerized services without requiring `/etc/hosts` modifications.

### Environment Variable Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `WIP_AUTH_MODE` | `api_key_only` | Auth mode: `none`, `api_key_only`, `jwt_only`, `dual` |
| **API Key Settings** | | |
| `WIP_AUTH_LEGACY_API_KEY` | - | Single API key (backward compatible, gets admin groups) |
| `WIP_AUTH_API_KEYS_JSON` | - | JSON array of API key definitions |
| `WIP_AUTH_API_KEYS_FILE` | - | Path to JSON file with API key definitions |
| `WIP_AUTH_API_KEY_HEADER` | `X-API-Key` | HTTP header name for API key |
| `WIP_AUTH_API_KEY_HASH_SALT` | `wip_auth_salt` | Salt for hashing API keys |
| **JWT Settings** | | |
| `WIP_AUTH_JWT_ISSUER_URL` | - | Expected JWT issuer (e.g., `http://localhost:5556/dex`) |
| `WIP_AUTH_JWT_JWKS_URI` | - | JWKS endpoint URL (auto-derived from issuer if not set) |
| `WIP_AUTH_JWT_AUDIENCE` | `wip` | Expected JWT audience claim |
| `WIP_AUTH_JWT_GROUPS_CLAIM` | `groups` | JWT claim containing user groups |
| `WIP_AUTH_JWT_ALGORITHMS` | `RS256,ES256` | Allowed JWT signing algorithms |
| `WIP_AUTH_JWT_VERIFY_EXP` | `true` | Whether to verify JWT expiration |
| `WIP_AUTH_JWT_LEEWAY_SECONDS` | `30` | Clock skew tolerance for JWT validation |
| **Group Settings** | | |
| `WIP_AUTH_DEFAULT_GROUPS` | `wip-users` | Default groups for authenticated users without explicit groups |
| `WIP_AUTH_ADMIN_GROUPS` | `wip-admins` | Groups considered admin (for `require_admin`) |

### Legacy Compatibility

The old `API_KEY` environment variable still works:
```yaml
environment:
  - API_KEY=dev_master_key_for_testing  # Still works!
```

---

## Configuration Scenarios

### Scenario 1: Dual Mode (Recommended)

Accept both API keys (for services/scripts) and JWT tokens (for users).

**This is the current default configuration.**

```yaml
environment:
  - WIP_AUTH_MODE=dual
  - WIP_AUTH_LEGACY_API_KEY=dev_master_key_for_testing
  - WIP_AUTH_JWT_ISSUER_URL=http://localhost:5556/dex
  - WIP_AUTH_JWT_JWKS_URI=http://host.containers.internal:5556/dex/keys
  - WIP_AUTH_JWT_AUDIENCE=wip-console
```

### Scenario 2: API Key Only

For simple setups without user login.

```yaml
environment:
  - WIP_AUTH_MODE=api_key_only
  - WIP_AUTH_LEGACY_API_KEY=dev_master_key_for_testing
```

### Scenario 3: Multiple Named API Keys

For environments with different services needing different access levels.

```yaml
environment:
  - WIP_AUTH_MODE=api_key_only
  - WIP_AUTH_API_KEYS_JSON=[{"name":"admin","key":"admin_secret","groups":["wip-admins"]},{"name":"reader","key":"reader_secret","groups":["wip-viewers"]}]
```

### Scenario 4: No Authentication (Development)

For local development without any auth checks.

```yaml
environment:
  - WIP_AUTH_MODE=none
```

**Usage:**
```bash
# No header needed
curl -k https://localhost:8443/api/def-store/terminologies
```

---

## HTTPS Access via Caddy

WIP uses **Caddy** as a reverse proxy to provide HTTPS access with auto-generated TLS certificates.

### Access URLs

| URL | Description |
|-----|-------------|
| `https://localhost:8443` | Local HTTPS access |
| `https://<hostname>:8443` | Network access (Pi deployments) |

### How It Works

```
┌────────────────────────────────────────────────────────────────────────────┐
│                         Caddy Reverse Proxy                                 │
├────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   Browser/Client                                                            │
│        │                                                                    │
│        │ HTTPS (:8443)                                                      │
│        ▼                                                                    │
│   ┌─────────────────┐                                                       │
│   │     Caddy       │  ← Auto-generated self-signed TLS certificate        │
│   │  :8080 / :8443  │                                                       │
│   └────────┬────────┘                                                       │
│            │                                                                │
│            ├──► /                      → WIP Console (:3000)               │
│            ├──► /api/registry/         → Registry (:8001)                  │
│            ├──► /api/def-store/        → Def-Store (:8002)                 │
│            ├──► /api/template-store/   → Template-Store (:8003)            │
│            ├──► /api/document-store/   → Document-Store (:8004)            │
│            ├──► /api/reporting-sync/   → Reporting-Sync (:8005)            │
│            └──► /dex/                  → Dex OIDC (:5556)                  │
│                                                                             │
└────────────────────────────────────────────────────────────────────────────┘
```

### Why Caddy for OIDC?

The OIDC library (oidc-client-ts) uses PKCE which requires `Crypto.subtle`, available only in secure contexts (HTTPS or localhost). Caddy provides:
- Auto-generated self-signed TLS certificate
- Reverse proxy for all services on single port (443/8443)
- OIDC login works over network without SSH tunnels
- ~25MB RAM overhead

For API-key-only deployments, use `--preset core` to skip Caddy.

---

## Token Details

### JWT Token Structure

Tokens issued by Dex contain these claims:

```json
{
  "iss": "http://localhost:5556/dex",
  "sub": "CglhZG1pbi0wMDESBWxvY2Fs",
  "aud": "wip-console",
  "exp": 1769952326,
  "iat": 1769865926,
  "email": "admin@wip.local",
  "email_verified": true,
  "name": "admin"
}
```

| Claim | Description |
|-------|-------------|
| `iss` | Issuer - the Dex URL |
| `sub` | Subject - unique user identifier |
| `aud` | Audience - the client ID |
| `exp` | Expiration timestamp |
| `iat` | Issued-at timestamp |
| `email` | User's email address |
| `name` | User's display name |
| `groups` | User's groups (if using a connector that provides them) |

### Token Expiration

Default token lifetimes (configured in `config/dex/config.yaml`):

| Token Type | Lifetime |
|------------|----------|
| ID Token | 24 hours |
| Refresh Token | 30 days (if unused) / 90 days (absolute max) |

### Decoding a Token (for debugging)

```bash
# Get a token
TOKEN=$(curl -s -X POST http://localhost:5556/dex/token \
  -d "grant_type=password&username=admin@wip.local&password=admin123" \
  -d "client_id=wip-console&client_secret=wip-console-secret&scope=openid profile email" \
  | jq -r '.access_token')

# Decode the payload (base64)
echo $TOKEN | cut -d'.' -f2 | base64 -d 2>/dev/null | jq .
```

---

## Testing Authentication

### Test API Key Auth

```bash
# Should succeed (200 OK)
curl -sk https://localhost:8443/api/def-store/terminologies \
  -H "X-API-Key: dev_master_key_for_testing" | jq '.total'

# Should fail (401 Unauthorized)
curl -sk https://localhost:8443/api/def-store/terminologies \
  -H "X-API-Key: wrong_key"
```

### Test JWT Auth

```bash
# Get token and call API
TOKEN=$(curl -s -X POST http://localhost:5556/dex/token \
  -d "grant_type=password&username=admin@wip.local&password=admin123" \
  -d "client_id=wip-console&client_secret=wip-console-secret&scope=openid profile email" \
  | jq -r '.access_token')

# Should succeed (200 OK)
curl -sk https://localhost:8443/api/def-store/terminologies \
  -H "Authorization: Bearer $TOKEN" | jq '.total'

# Should fail (401 Unauthorized) - expired or invalid token
curl -sk https://localhost:8443/api/def-store/terminologies \
  -H "Authorization: Bearer invalid_token"
```

### Test No Auth Mode

```bash
# With WIP_AUTH_MODE=none, no header needed
curl -sk https://localhost:8443/api/def-store/terminologies | jq '.total'
```

---

## Troubleshooting

### "Invalid API key" Error

1. Check the environment variable is set correctly:
   ```bash
   podman exec wip-def-store env | grep -E "(API_KEY|WIP_AUTH)"
   ```

2. Verify the key matches what you're sending in the header

### "Invalid token issuer" Error

The JWT issuer doesn't match what the service expects.

1. Check what issuer is in your token:
   ```bash
   echo $TOKEN | cut -d'.' -f2 | base64 -d 2>/dev/null | jq '.iss'
   ```

2. Verify it matches `WIP_AUTH_JWT_ISSUER_URL` in the service config

3. Make sure Dex is configured with the correct issuer in `config/dex/config.yaml`

### "Failed to fetch JWKS" Error

The service can't reach Dex to get the signing keys.

1. Check Dex is running:
   ```bash
   curl http://localhost:5556/dex/.well-known/openid-configuration
   ```

2. From inside a container, check `host.containers.internal` resolves:
   ```bash
   podman exec wip-def-store curl http://host.containers.internal:5556/dex/keys
   ```

### Services Not Picking Up New Environment Variables

Restart the service after changing docker-compose.yml:
```bash
podman-compose -f docker-compose.yml down
podman-compose -f docker-compose.yml up -d --build
```

### Dex Not Starting

Check Dex logs:
```bash
podman logs wip-dex
```

Common issues:
- Invalid YAML syntax in `config/dex/config.yaml`
- Invalid bcrypt hash format for passwords
- Port 5556 already in use

---

## Security Risks and Production Guidance

### Understanding the Risks

#### API Key Risks

| Risk | Description | Mitigation |
|------|-------------|------------|
| **No expiration** | Compromised keys work forever | Regular key rotation, monitoring |
| **Shared secret** | One key for all services (if using legacy key) | Use named keys with different permissions |
| **Replay attacks** | Key can be reused indefinitely | Network isolation, TLS |
| **Exposure in logs** | Key might appear in error logs | Ensure logging doesn't capture headers |

#### JWT Risks

| Risk | Description | Mitigation |
|------|-------------|------------|
| **Token theft** | Stolen token usable until expiry | Short expiration (24h), secure storage |
| **OIDC provider compromise** | Attacker can issue valid tokens | Secure Dex/Authelia deployment |
| **Algorithm confusion** | Weak algorithm exploitation | Enforce RS256, reject HS256 |

### Security by Deployment Profile

#### Minimal/Standard Profile (Pi at Home)

**Threat model:** Trusted home network, no internet exposure

**Acceptable configuration:**
```yaml
WIP_AUTH_MODE=dual
WIP_AUTH_LEGACY_API_KEY=<random-key>  # NOT the dev key
```

**Risks accepted:**
- API keys don't expire (acceptable in isolated network)
- Shared API key across services (acceptable for simplicity)

**Recommendations:**
1. Generate a strong key: `openssl rand -hex 32`
2. Don't expose ports to internet directly
3. Use firewall to restrict access to local network

#### Production Profile (Cloud/Internet-Exposed)

**Threat model:** Untrusted network, potential attackers

**Required configuration:**
```
┌─────────────────────────────────────────────────────────────────┐
│                         Internet                                 │
└────────────────────────────┬────────────────────────────────────┘
                             │
                       ┌─────▼─────┐
                       │  Reverse  │  ← TLS termination (HTTPS)
                       │  Proxy    │  ← JWT validation required
                       │  (Caddy)  │  ← Rate limiting
                       └─────┬─────┘
                             │ Only authenticated requests pass
┌────────────────────────────┼────────────────────────────────────┐
│              Private Network (not internet-accessible)           │
│                            │                                     │
│   ┌───────────┐      ┌─────▼─────┐      ┌──────────────┐        │
│   │ Def-Store │◄────►│Doc-Store  │◄────►│Template-Store│        │
│   └───────────┘      └───────────┘      └──────────────┘        │
│         │                  │                    │                │
│         └──────────────────┼────────────────────┘                │
│                   API keys OK here                               │
│              (internal network only)                             │
└─────────────────────────────────────────────────────────────────┘
```

**Requirements:**

1. **Reverse proxy (Caddy):**
   - Terminates TLS (HTTPS required)
   - Validates JWT for all external requests
   - API key header stripped from external requests
   - Rate limiting to prevent brute force

2. **OIDC provider:**
   - Dex works for development and Pi deployments
   - Use Authelia (requires HTTPS, proper domain) for more security
   - Or Authentik (enterprise features, user management UI)

3. **Network isolation:**
   - Services not directly accessible from internet
   - Only reverse proxy is internet-facing
   - Internal service-to-service uses API keys (acceptable)

4. **Strong API keys:**
   ```bash
   # Generate per-service keys
   REGISTRY_API_KEY=$(openssl rand -hex 32)
   DEF_STORE_API_KEY=$(openssl rand -hex 32)
   TEMPLATE_STORE_API_KEY=$(openssl rand -hex 32)
   # etc.
   ```

5. **Secrets management:**
   - Don't store keys in docker-compose files
   - Use Docker secrets, Kubernetes secrets, or Vault
   - Rotate keys periodically

### API Keys vs JWT: When to Use What

| Scenario | Recommended Auth | Reason |
|----------|-----------------|--------|
| Service-to-service (internal) | API Key | Simple, no token refresh needed |
| User via browser | JWT | Identity, expiration, audit trail |
| External scripts/integrations | JWT | Should go through reverse proxy |
| CI/CD pipelines | API Key (scoped) | Service account, internal network |
| Admin CLI tools | JWT | User identity for audit |

### Risk Assessment Summary

| Deployment | `dual` Mode Acceptable? | Additional Requirements |
|------------|------------------------|------------------------|
| Development | Yes | None (use dev key) |
| Home Pi (isolated) | Yes | Strong random key |
| Home Pi (port forwarded) | Caution | Reverse proxy + JWT required externally |
| Cloud (internal only) | Yes | Network policies, strong keys |
| Cloud (internet-facing) | Caution | Reverse proxy, Authelia/Authentik, network isolation |
| Enterprise/regulated | Needs more | mTLS, audit logging, per-service keys, SIEM integration |

### Future Security Enhancements (Not Yet Implemented)

1. **Per-service API keys** - Fully scoped keys with namespace restrictions
2. **API key registry** - Database-backed key management with rotation
3. **mTLS** - Certificate-based service-to-service authentication
4. **Audit logging** - Record all authentication events to external system

---

## Switching to a Different OIDC Provider

For production deployments, you may want to switch from Dex to a more robust OIDC provider.

Because WIP uses the standard OIDC protocol, you can switch providers by changing environment variables:

### Switch to Authentik (Enterprise)

```yaml
environment:
  - WIP_AUTH_MODE=dual
  - WIP_AUTH_LEGACY_API_KEY=dev_master_key_for_testing
  - WIP_AUTH_JWT_ISSUER_URL=http://authentik:9000/application/o/wip/
  - WIP_AUTH_JWT_JWKS_URI=http://authentik:9000/application/o/wip/jwks/
  - WIP_AUTH_JWT_AUDIENCE=wip
```

### Switch to Authelia

```yaml
environment:
  - WIP_AUTH_MODE=dual
  - WIP_AUTH_LEGACY_API_KEY=dev_master_key_for_testing
  - WIP_AUTH_JWT_ISSUER_URL=https://auth.yourdomain.com
  - WIP_AUTH_JWT_AUDIENCE=wip
```

Note: Authelia requires HTTPS and a proper domain.

---

## Summary

| I want to... | Set `WIP_AUTH_MODE` to... | Also set... |
|--------------|---------------------------|-------------|
| Disable auth for testing | `none` | Nothing else needed |
| Use API keys only | `api_key_only` | `WIP_AUTH_LEGACY_API_KEY` or `WIP_AUTH_API_KEYS_JSON` |
| Use both API keys and JWT | `dual` | All `WIP_AUTH_JWT_*` variables |
| Use only JWT (no API keys) | `jwt_only` | All `WIP_AUTH_JWT_*` variables |

| I want to... | Do this... |
|--------------|------------|
| Add a new user | Edit `config/dex/config.yaml`, add to `staticPasswords`, restart Dex |
| Change a password | Generate new bcrypt hash, update `config/dex/config.yaml`, restart Dex |
| Add a new API key | Add to `WIP_AUTH_API_KEYS_JSON` or JSON file, restart services |
| Add a new OAuth2 client | Edit `config/dex/config.yaml`, add to `staticClients`, restart Dex |
| Switch OIDC providers | Change `WIP_AUTH_JWT_ISSUER_URL` and `WIP_AUTH_JWT_JWKS_URI` |

---

## Production Deployment

For production deployments, use the `--prod` flag with setup.sh to:
- Generate strong random secrets for all services
- Enable MongoDB and NATS authentication
- Add security headers to Caddy configuration

```bash
# Home network deployment (self-signed TLS)
./scripts/setup.sh --preset standard --hostname wip-pi.local --prod -y

# Internet-exposed deployment (Let's Encrypt TLS)
./scripts/setup.sh --preset standard --hostname wip.example.com --prod \
  --email admin@example.com -y
```

### Validating Production Readiness

Run the production check script:
```bash
./scripts/security/production-check.sh
```

### API Key Management

Generate additional API keys:
```bash
./scripts/security/generate-api-key.sh --name backup-service --groups wip-services
```

### API Key Expiration

API keys can optionally have an expiration date:
```json
{
  "name": "temp-access",
  "key_hash": "...",
  "expires_at": "2024-12-31T23:59:59Z"
}
```

Expired keys will receive a 401 response with the message "API key has expired".

### Further Reading

- [Production Deployment Guide](production-deployment.md) - Complete deployment walkthrough
- [Encryption at Rest](security/encryption-at-rest.md) - Data encryption options
- [Key Rotation](security/key-rotation.md) - Secret rotation procedures
