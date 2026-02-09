# Network Configuration Guide

This document describes all 4 deployment scenarios and how networking, Caddy, and service endpoints must be configured in each case.

## Quick Reference

| Scenario | Console Access | API Access | OIDC (Dex) |
|----------|---------------|------------|------------|
| 1. Localhost + OIDC | https://localhost:8443 | Via Caddy (:8443) | https://localhost:8443/dex |
| 2. Localhost - OIDC | http://localhost:3000 | Direct ports (:8001-8005) | N/A |
| 3. Remote + OIDC | https://hostname:8443 | Via Caddy (:8443) | https://hostname:8443/dex |
| 4. Remote - OIDC | http://hostname:3000 | Direct ports (:8001-8005) | N/A |

---

## Scenario 1: Localhost with OIDC (Dex)

**Use case:** Local development with authentication testing.

### Architecture

```
Browser
   │
   └──► https://localhost:8443 ──► Caddy ──┬──► wip-console:80
                                           ├──► wip-dex:5556 (/dex/*)
                                           ├──► wip-registry:8001 (/api/registry/*)
                                           ├──► wip-def-store:8002 (/api/def-store/*)
                                           ├──► wip-template-store:8003 (/api/template-store/*)
                                           └──► wip-document-store:8004 (/api/document-store/*)
```

### Caddy Configuration

```caddyfile
{
    auto_https disable_redirects
}

localhost {
    tls internal  # Self-signed certificate

    # Dex OIDC provider
    handle /dex/* {
        reverse_proxy wip-dex:5556
    }

    # API services (use handle, NOT handle_path)
    handle /api/registry/* {
        reverse_proxy wip-registry:8001
    }

    handle /api/def-store/* {
        reverse_proxy wip-def-store:8002
    }

    handle /api/template-store/* {
        reverse_proxy wip-template-store:8003
    }

    handle /api/document-store/* {
        reverse_proxy wip-document-store:8004
    }

    # Console (default)
    handle {
        reverse_proxy wip-console:80
    }
}
```

### .env Configuration

```bash
# Network
WIP_NETWORK_MODE=localhost
WIP_HOSTNAME=localhost

# OIDC - ALL THREE MUST MATCH
WIP_AUTH_JWT_ISSUER_URL=https://localhost:8443/dex   # Backend JWT validation
WIP_AUTH_JWT_JWKS_URI=http://wip-dex:5556/dex/keys   # Internal JWKS fetch (container network)
VITE_OIDC_AUTHORITY=https://localhost:8443/dex       # Browser OIDC discovery
VITE_OIDC_REDIRECT_URI=https://localhost:8443/auth/callback

# Auth mode
WIP_AUTH_MODE=dual  # Both API key and JWT
```

### Dex config.yaml

```yaml
issuer: https://localhost:8443/dex  # MUST match WIP_AUTH_JWT_ISSUER_URL

staticClients:
  - id: wip-console
    redirectURIs:
      - https://localhost:8443/auth/callback  # MUST match VITE_OIDC_REDIRECT_URI
```

### Critical Points

1. **Issuer URL consistency**: The token's `iss` claim comes from Dex's `issuer` config. Backend validates against `WIP_AUTH_JWT_ISSUER_URL`. These MUST match exactly.

2. **JWKS URI is internal**: `WIP_AUTH_JWT_JWKS_URI` uses container-internal hostname (`wip-dex:5556`) because backend services fetch keys directly, not through browser.

3. **Browser uses external URL**: `VITE_OIDC_AUTHORITY` is what the browser uses for OIDC discovery. It goes through Caddy.

4. **Container restart vs recreate**: Environment variables are read at container **creation**. After changing `.env`, you must `docker-compose down && up`, not just `restart`.

---

## Scenario 2: Localhost without OIDC

**Use case:** Simplest local development, API key only.

### Architecture

```
Browser
   │
   ├──► http://localhost:3000 ──► wip-console (direct)
   │
   └──► http://localhost:800X ──► Services (direct)
        8001 = Registry
        8002 = Def-Store
        8003 = Template-Store
        8004 = Document-Store
```

No Caddy needed. No TLS. No OIDC.

### .env Configuration

```bash
# Network
WIP_NETWORK_MODE=localhost
WIP_HOSTNAME=localhost

# No OIDC
WIP_AUTH_MODE=api_key_only
WIP_AUTH_LEGACY_API_KEY=dev_master_key_for_testing

# Console connects directly to services
VITE_OIDC_ENABLED=false
VITE_API_BASE_URL=  # Empty = use relative paths, but since no Caddy, console uses direct ports
```

### Console vite.config.ts proxy

When running without Caddy, the console dev server needs proxy rules:

```typescript
server: {
  proxy: {
    '/api/registry': 'http://localhost:8001',
    '/api/def-store': 'http://localhost:8002',
    '/api/template-store': 'http://localhost:8003',
    '/api/document-store': 'http://localhost:8004',
  }
}
```

### Critical Points

1. **No Caddy** = no reverse proxy, no TLS, no `/dex` path
2. **Direct port access**: Services exposed on their native ports
3. **CORS**: Services must allow `http://localhost:3000` as origin
4. **API key in header**: All requests need `X-API-Key: dev_master_key_for_testing`

---

## Scenario 3: Remote Host with OIDC

**Use case:** Raspberry Pi or server deployment with authentication.

### Architecture

```
Browser (any device on network)
   │
   └──► https://wip-pi.local:8443 ──► Caddy ──┬──► wip-console:80
                                              ├──► wip-dex:5556 (/dex/*)
                                              └──► wip-*:800X (/api/*/*)
```

### Caddy Configuration

```caddyfile
{
    auto_https disable_redirects
}

wip-pi.local {
    tls internal  # Self-signed, or use ACME for real certs

    handle /dex/* {
        reverse_proxy wip-dex:5556
    }

    handle /api/registry/* {
        reverse_proxy wip-registry:8001
    }

    # ... other services ...

    handle {
        reverse_proxy wip-console:80
    }
}
```

### .env Configuration

```bash
# Network
WIP_NETWORK_MODE=network
WIP_HOSTNAME=wip-pi.local

# OIDC - Use the external hostname
WIP_AUTH_JWT_ISSUER_URL=https://wip-pi.local:8443/dex
WIP_AUTH_JWT_JWKS_URI=http://wip-dex:5556/dex/keys  # Still internal!
VITE_OIDC_AUTHORITY=https://wip-pi.local:8443/dex
VITE_OIDC_REDIRECT_URI=https://wip-pi.local:8443/auth/callback

WIP_AUTH_MODE=dual
```

### Dex config.yaml

```yaml
issuer: https://wip-pi.local:8443/dex

web:
  allowedOrigins:
    - https://wip-pi.local:8443

staticClients:
  - id: wip-console
    redirectURIs:
      - https://wip-pi.local:8443/auth/callback
```

### Critical Points

1. **Hostname resolution**: Client devices must resolve `wip-pi.local` (mDNS or DNS/hosts file)
2. **Certificate trust**: Self-signed certs require browser trust exception
3. **Dex allowedOrigins**: Must include the external URL
4. **Firewall**: Port 8443 must be accessible from client devices

---

## Scenario 4: Remote Host without OIDC

**Use case:** Simple network deployment, API key authentication only.

### Architecture

```
Browser (any device on network)
   │
   ├──► http://wip-pi.local:3000 ──► wip-console
   │
   └──► http://wip-pi.local:800X ──► Services
```

### .env Configuration

```bash
# Network
WIP_NETWORK_MODE=network
WIP_HOSTNAME=wip-pi.local

# No OIDC
WIP_AUTH_MODE=api_key_only
WIP_AUTH_LEGACY_API_KEY=your_secret_key_here

VITE_OIDC_ENABLED=false
```

### Docker Compose port bindings

Services must bind to `0.0.0.0` not just `127.0.0.1`:

```yaml
ports:
  - "0.0.0.0:3000:80"     # Console
  - "0.0.0.0:8001:8001"   # Registry
  # etc.
```

### Critical Points

1. **Security warning**: No TLS means credentials sent in clear text. Use only on trusted networks.
2. **CORS configuration**: Services need `http://wip-pi.local:3000` in allowed origins
3. **API key exposure**: Without HTTPS, API key can be intercepted

---

## Common Pitfalls

### 1. Issuer URL Mismatch (401 "Invalid token issuer")

**Symptom**: Login succeeds but API calls return 401.

**Cause**: JWT `iss` claim doesn't match `WIP_AUTH_JWT_ISSUER_URL`.

**Fix**: Ensure exact match between:
- `config/dex/config.yaml` → `issuer`
- `.env` → `WIP_AUTH_JWT_ISSUER_URL`
- `.env` → `VITE_OIDC_AUTHORITY`

### 2. JWKS Fetch Failure

**Symptom**: 401 errors, backend logs show JWKS fetch failed.

**Cause**: Backend can't reach Dex to get signing keys.

**Fix**: `WIP_AUTH_JWT_JWKS_URI` must use container-internal hostname (`wip-dex:5556`), not external URL.

### 3. Environment Not Applied After .env Change

**Symptom**: Changed `.env` but services still use old values.

**Cause**: Containers read env vars at creation, not restart.

**Fix**:
```bash
# Wrong - just restarts with same env
podman-compose restart

# Correct - recreates with new env
podman-compose down && podman-compose up -d
```

### 4. Mixed Content (HTTP/HTTPS)

**Symptom**: Browser blocks requests, console errors about mixed content.

**Cause**: Page loaded over HTTPS trying to make HTTP requests.

**Fix**: When using OIDC (HTTPS), ALL resources must be HTTPS. Ensure `VITE_API_BASE_URL` is empty (uses same origin) or explicitly HTTPS.

### 5. Caddy `handle` vs `handle_path`

**Symptom**: 404 errors on API calls.

**Cause**: `handle_path` strips the path prefix, but services expect the full path.

**Fix**: Always use `handle`, not `handle_path`:
```caddyfile
# CORRECT - path preserved
handle /api/def-store/* {
    reverse_proxy wip-def-store:8002
}

# WRONG - path stripped, service gets /terminologies instead of /api/def-store/terminologies
handle_path /api/def-store/* {
    reverse_proxy wip-def-store:8002
}
```

---

## Verification Commands

### Check JWT issuer in running container
```bash
podman exec wip-def-store printenv WIP_AUTH_JWT_ISSUER_URL
```

### Test Dex OIDC discovery
```bash
curl -k https://localhost:8443/dex/.well-known/openid-configuration | jq .issuer
```

### Test service through Caddy
```bash
curl -k https://localhost:8443/api/registry/health
```

### Test service directly
```bash
curl http://localhost:8001/health
```

### Check Caddy is proxying correctly
```bash
podman logs wip-caddy 2>&1 | tail -20
```

---

## Summary Checklist

When setting up or debugging:

- [ ] **Dex `issuer`** matches **`WIP_AUTH_JWT_ISSUER_URL`** matches **`VITE_OIDC_AUTHORITY`**
- [ ] **JWKS URI** uses container-internal hostname
- [ ] **Redirect URIs** match between Dex config and `.env`
- [ ] After `.env` changes, containers were **recreated** (down + up)
- [ ] Caddy uses **`handle`** not `handle_path`
- [ ] If remote: hostname resolves from client device
- [ ] If HTTPS: certificate is trusted or exception added
