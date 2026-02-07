# Production Security Implementation Plan

## Overview

Implement production security hardening for WIP: API key management, password generation, encryption in transit/at rest, and deployment procedures.

**Security Tiers:**
- **Tier 1 (Home Pi)**: Random passwords, self-signed TLS, single API key
- **Tier 2 (Internet-exposed)**: Let's Encrypt, per-service API keys, DB auth, NATS auth
- **Tier 3 (Enterprise)**: External secrets manager, mTLS, Authentik/Keycloak

---

## Phase 1: Secret Generation (setup.sh enhancement)

### 1.1 Add `--prod` flag to setup.sh

Modify `scripts/setup.sh` to accept `--prod` variant that:

1. **Generates random secrets** instead of using defaults:
   ```bash
   if [ "$VARIANT" = "prod" ]; then
       WIP_API_KEY=$(openssl rand -hex 32)
       WIP_POSTGRES_PASSWORD=$(openssl rand -hex 24)
       WIP_MINIO_PASSWORD=$(openssl rand -hex 24)
       WIP_MONGO_PASSWORD=$(openssl rand -hex 24)
       WIP_DEX_CLIENT_SECRET=$(openssl rand -hex 32)
       WIP_NATS_TOKEN=$(openssl rand -hex 32)
   fi
   ```

2. **Prompts for or generates Dex user passwords**

3. **Saves credentials** to `$WIP_DATA_DIR/secrets/` with 600 permissions:
   ```
   secrets/
   ├── api_key              # Master API key
   ├── postgres_password
   ├── minio_password
   ├── mongo_password
   ├── dex_client_secret
   ├── nats_token
   └── credentials.txt      # Human-readable summary (view once)
   ```

### 1.2 Files to modify

| File | Changes |
|------|---------|
| `scripts/setup.sh` | Add `--prod` flag, secret generation, secrets directory creation |
| `.gitignore` | Add `data/secrets/` if not already present |

---

## Phase 2: Database Authentication

### 2.1 MongoDB Authentication

Modify `docker-compose/base.yml`:
```yaml
mongodb:
  environment:
    MONGO_INITDB_ROOT_USERNAME: ${WIP_MONGO_USER:-}
    MONGO_INITDB_ROOT_PASSWORD: ${WIP_MONGO_PASSWORD:-}
```

Update `.env` generation in setup.sh:
```bash
# Only set if in prod mode
if [ -n "$WIP_MONGO_PASSWORD" ]; then
    MONGO_URI="mongodb://${WIP_MONGO_USER}:${WIP_MONGO_PASSWORD}@wip-mongodb:27017/"
fi
```

### 2.2 NATS Token Authentication

Create `config/nats/nats.conf.template`:
```conf
port: 4222
jetstream { store_dir: /data }
authorization { token: "${WIP_NATS_TOKEN}" }
```

Update `docker-compose/base.yml` for prod variant:
```yaml
nats:
  command: ["-c", "/etc/nats/nats.conf"]
  volumes:
    - ./config/nats/nats.conf:/etc/nats/nats.conf:ro
```

Update service NATS_URL: `nats://TOKEN@wip-nats:4222`

### 2.3 Files to modify

| File | Changes |
|------|---------|
| `docker-compose/base.yml` | Add MongoDB auth env vars, NATS config mount |
| `scripts/setup.sh` | Generate NATS config from template |
| `config/nats/nats.conf.template` | New file with token auth |

---

## Phase 3: API Key Expiration

### 3.1 Add expiration to wip-auth

Modify `libs/wip-auth/src/wip_auth/models.py`:
```python
class APIKeyRecord(BaseModel):
    # ... existing fields ...
    expires_at: datetime | None = Field(None, description="When the key expires (None = never)")
```

Modify `libs/wip-auth/src/wip_auth/providers/api_key.py`:
```python
def validate(self, key: str) -> AuthResult:
    # ... existing lookup ...
    if record.expires_at and datetime.utcnow() > record.expires_at:
        return AuthResult(success=False, error="API key expired", error_code="key_expired")
```

### 3.2 API key generation script

Create `scripts/security/generate-api-key.sh`:
```bash
#!/bin/bash
# Generate a new API key with optional expiration
# Usage: ./generate-api-key.sh --name mykey --groups wip-editors --expires 90d

KEY=$(openssl rand -hex 32)
HASH=$(echo -n "wip_auth_salt:$KEY" | sha256sum | cut -d' ' -f1)
# Output JSON for api-keys.json
```

### 3.3 Files to modify/create

| File | Changes |
|------|---------|
| `libs/wip-auth/src/wip_auth/models.py` | Add `expires_at` field |
| `libs/wip-auth/src/wip_auth/providers/api_key.py` | Add expiration check |
| `scripts/security/generate-api-key.sh` | New script |

---

## Phase 4: TLS / Encryption in Transit

### 4.1 Let's Encrypt support

Add to setup.sh:
```bash
--email EMAIL           # Admin email for Let's Encrypt
--acme-staging         # Use Let's Encrypt staging (for testing)
```

Modify Caddyfile generation for prod:
```caddyfile
{
    email {$ADMIN_EMAIL}
}

{$WIP_HOSTNAME} {
    # No "tls internal" = Let's Encrypt auto

    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "SAMEORIGIN"
    }
    # ... existing reverse proxy ...
}
```

### 4.2 Files to modify

| File | Changes |
|------|---------|
| `scripts/setup.sh` | Add `--email`, `--acme-staging` flags |
| `config/caddy/Caddyfile.template` | Add security headers, conditional TLS |

---

## Phase 5: Documentation & Validation

### 5.1 Production checklist script

Create `scripts/security/production-check.sh`:
```bash
#!/bin/bash
# Validates production readiness
# Checks:
# - No default passwords in .env
# - Secrets files exist with 600 permissions
# - TLS configured correctly
# - DB authentication enabled
# - API keys are strong (64+ chars)

ERRORS=0
# ... validation logic ...
exit $ERRORS
```

### 5.2 Documentation

Create `docs/security/production-deployment.md`:
- Complete production deployment walkthrough
- Secret rotation procedures
- Backup encryption guidance
- Security tier comparison

Update `docs/authentication.md`:
- Add production API key management section
- Document key rotation process

### 5.3 Files to create/modify

| File | Purpose |
|------|---------|
| `scripts/security/production-check.sh` | New validation script |
| `docs/security/production-deployment.md` | New deployment guide |
| `docs/security/key-rotation.md` | New rotation procedures |
| `docs/authentication.md` | Update with production guidance |

---

## Phase 6: Encryption at Rest (Documentation)

Create `docs/security/encryption-at-rest.md` documenting:

1. **Recommended: Host-level encryption**
   - LUKS for Linux/Pi
   - FileVault for Mac
   - BitLocker for Windows

2. **Optional: MinIO SSE**
   - Environment variable configuration
   - Key management options

3. **MongoDB/PostgreSQL**
   - Enterprise encryption (MongoDB)
   - PostgreSQL TDE (Enterprise)
   - Recommendation: Use host-level instead

---

## Implementation Order

| Priority | Task | Effort |
|----------|------|--------|
| 1 | Phase 1: `--prod` flag + secret generation | 2-3 hours |
| 2 | Phase 2.1: MongoDB authentication | 1 hour |
| 3 | Phase 3.1: API key expiration | 1 hour |
| 4 | Phase 4: Let's Encrypt support | 1-2 hours |
| 5 | Phase 2.2: NATS authentication | 1 hour |
| 6 | Phase 5: Validation script + docs | 2 hours |
| 7 | Phase 6: Encryption at rest docs | 1 hour |

**Total estimated: 1-2 days**

---

## Verification Plan

1. **Test secret generation:**
   ```bash
   ./scripts/setup.sh --preset standard --hostname test.local --prod --generate-secrets
   ls -la data/secrets/
   cat data/secrets/credentials.txt
   ```

2. **Test production deployment:**
   ```bash
   ./scripts/setup.sh --preset full --hostname wip.example.com --prod -y
   ./scripts/security/production-check.sh
   ```

3. **Test API key expiration:**
   - Create key with 1-minute expiration
   - Verify access works
   - Wait, verify access fails

4. **Test Let's Encrypt (staging):**
   ```bash
   ./scripts/setup.sh --preset standard --hostname real-domain.com --prod --email admin@domain.com --acme-staging
   curl -I https://real-domain.com
   ```

---

## Critical Files Summary

| File | Purpose |
|------|---------|
| `scripts/setup.sh` | Main changes: --prod, --email, secret generation |
| `libs/wip-auth/src/wip_auth/models.py` | Add expires_at to APIKeyRecord |
| `libs/wip-auth/src/wip_auth/providers/api_key.py` | Add expiration validation |
| `docker-compose/base.yml` | MongoDB auth environment variables |
| `config/nats/nats.conf.template` | New: NATS token authentication |
| `scripts/security/generate-api-key.sh` | New: API key generator |
| `scripts/security/production-check.sh` | New: Production validator |
| `docs/security/production-deployment.md` | New: Deployment guide |
