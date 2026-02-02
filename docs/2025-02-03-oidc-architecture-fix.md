# OIDC Configuration Architecture Fix

**Date:** 2025-02-03

## Problem

The JWT issuer URL was configured in multiple places (docker-compose files and setup.sh), leading to mismatches that caused "Session expired" errors after login. Additionally, podman-compose does not expand `${VAR:-default}` syntax, so hardcoded defaults in docker-compose files were being passed literally to containers.

## Solution: Single Source of Truth

All authentication configuration now flows from a single `.env` file:

```
setup.sh generates .env with auth config
         ↓
All docker-compose.dev.yml files use env_file: ../../.env
         ↓
Services read auth config from .env (no hardcoded values)
```

This ensures the JWT issuer URL is always consistent between:
1. Dex configuration (what goes into tokens)
2. Backend services (what they validate against)
3. WIP Console (OIDC client configuration)

## Files Modified

### Docker-compose files (added `env_file`, removed hardcoded auth vars):
- `components/def-store/docker-compose.dev.yml`
- `components/template-store/docker-compose.dev.yml`
- `components/document-store/docker-compose.dev.yml`
- `components/reporting-sync/docker-compose.dev.yml`
- `ui/wip-console/docker-compose.dev.yml`

### setup.sh (fixed issuer URL logic):
- When Caddy is enabled, use `https://localhost:8443/dex` for localhost mode
- Previously incorrectly used `http://localhost:5556/dex` even when Caddy was enabled
- The issuer URL must match what the browser sees (through Caddy)

### Documentation:
- `docs/oidc-configuration.md` - Updated to explain the architecture

## Key Technical Details

### Why the mismatch happened
1. Dex puts the `issuer` value from its config into JWT tokens
2. Backend services validate that the token's `iss` claim matches `WIP_AUTH_JWT_ISSUER_URL`
3. When Caddy is enabled, browser accesses Dex via `https://localhost:8443/dex`
4. But setup.sh was setting issuer to `http://localhost:5556/dex` for localhost mode
5. Result: Token had wrong issuer, backend rejected it

### The fix
- setup.sh now checks if Caddy is enabled (`WIP_INCLUDE_CADDY=true`)
- If Caddy enabled: issuer is `https://localhost:${HTTPS_PORT}/dex`
- If Caddy disabled: issuer is `http://localhost:5556/dex`
- Docker-compose files use `env_file: ../../.env` instead of hardcoded values

## Verification

After running `./scripts/setup.sh`:

```bash
# Check Dex config
grep issuer config/dex/config.yaml
# Output: issuer: https://localhost:8443/dex

# Check backend service
podman exec wip-def-store-dev printenv | grep WIP_AUTH_JWT_ISSUER_URL
# Output: WIP_AUTH_JWT_ISSUER_URL=https://localhost:8443/dex
```

Both now correctly match.

## Usage

```bash
# For localhost development
./scripts/setup.sh

# For Pi with network access
./scripts/setup.sh --hostname wip-pi.local

# For minimal deployment (API keys only)
./scripts/setup.sh --profile pi-minimal
```

The `.env` file is the single source of truth. All services automatically read from it via the `env_file` directive in their docker-compose files.

## Related Documentation

- `docs/oidc-configuration.md` - Full OIDC configuration guide
- `config/dex/config.yaml.example` - Example Dex configuration
