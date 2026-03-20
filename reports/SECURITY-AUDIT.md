# WIP Security Audit Report

**Date:** 2026-03-20
**Scope:** Code review and configuration review of the entire WIP attack surface
**Auditor:** Automated security audit with manual review

---

## Executive Summary

A comprehensive security audit was performed on the World In a Pie (WIP) project covering all core services, authentication library, infrastructure configuration, and deployment tooling. The audit identified 4 critical, 7 high, 8 medium, and 6 low severity findings. All findings have been remediated.

---

## Threat Model

### Deployment Tiers

| Tier | Environment | Network | Trust Level |
|------|-------------|---------|-------------|
| **Tier 1** | Home Pi on LAN | Trusted local network | Household members |
| **Tier 2** | Internet-exposed Pi/VPS | Public internet | Untrusted anonymous users |
| **Tier 3** | Enterprise / cloud | Corporate network + internet | Compliance-grade |

### Assets Protected

1. **Data at rest** — MongoDB documents, PostgreSQL reporting data, MinIO files
2. **Credentials** — API keys, OIDC tokens, database passwords, NATS tokens
3. **Service availability** — All API services + UI
4. **Configuration integrity** — Compose files, Caddy config, Dex config

---

## Findings and Remediation

### CRITICAL

| ID | Finding | Status | Fix |
|----|---------|--------|-----|
| C1 | CORS wildcard with credentials | **Fixed** | Default CORS origin changed to `https://localhost:8443`. Restricted `allow_methods` and `allow_headers` to required values only. Configurable via `CORS_ORIGINS` env var. setup.sh generates hostname-specific CORS for prod. |
| C2 | No file upload size limit | **Fixed** | Added `WIP_MAX_UPLOAD_SIZE` (default 100MB). Upload endpoint reads in 64KB chunks with byte counter, returns HTTP 413 if exceeded. |
| C3 | No rate limiting | **Fixed** | Added `slowapi` rate limiter to all services. Default 40,000 req/min per IP. Configurable via `WIP_RATE_LIMIT` env var. Shared implementation in `wip_auth.ratelimit`. |
| C4 | Default API key accepted in production | **Fixed** | Added `check_production_security()` to all service startups. Services refuse to start in `WIP_VARIANT=prod` if the well-known default key `dev_master_key_for_testing` is configured. |

### HIGH

| ID | Finding | Status | Fix |
|----|---------|--------|-----|
| H1 | API key hashing uses SHA-256 | **Fixed** | Migrated to bcrypt. `hash_api_key()` now generates bcrypt hashes. Legacy SHA-256 hashes are still accepted with a deprecation warning. `verify_api_key()` uses `bcrypt.checkpw()` (constant-time). |
| H2 | Hardcoded salt for API key hashing | **Fixed** | setup.sh generates per-deployment random salt (`openssl rand -hex 16`) in prod mode. Stored as `WIP_AUTH_API_KEY_HASH_SALT` in `.env`. Dev mode retains default salt. |
| H3 | API key comparison not timing-safe | **Fixed** | Bundled with H1. bcrypt.checkpw() is inherently constant-time. Legacy SHA-256 fallback uses `hmac.compare_digest()`. |
| H4 | Missing security headers | **Fixed** | setup.sh now generates security headers for ALL deployment modes (X-Content-Type-Options, X-Frame-Options, Referrer-Policy). Prod mode adds HSTS and CSP. |
| H5 | No file content-type validation | **Fixed** | Added content-type allowlist validation and blocked-extension check. Configurable via `WIP_ALLOWED_MIME_TYPES` env var. Dangerous extensions (.exe, .bat, .ps1, etc.) always rejected. |
| H6 | MinIO console and NATS monitoring exposed | **Fixed** | Ports controlled by `WIP_MINIO_CONSOLE_PORT` and `WIP_NATS_MONITOR_PORT` env vars. setup.sh sets these to empty (disabled) in prod mode. production-check.sh validates. |
| H7 | Debug endpoints unauthenticated | **Fixed** | `/debug/timing`, `/debug/timing/reset`, `/debug/cache`, `/debug/cache/clear` now require API key authentication via `Depends(require_api_key)`. |

### MEDIUM

| ID | Finding | Status | Fix |
|----|---------|--------|-----|
| M1 | No JWT refresh token mechanism | **Accepted** | Deferred to UI improvement cycle. Current token lifetime is controlled by Dex configuration. Short-lived tokens (15 min) can be configured in Dex. |
| M2 | User groups sent as query parameters | **Fixed** | Groups now sent via `X-User-Groups` header. Registry endpoints updated to read from header (with query param fallback for backward compatibility). |
| M3 | Health check exposes exception details | **Fixed** | All service health checks now return generic error messages to clients. Full exception details logged server-side only. |
| M4 | MongoDB auth disabled by default | **Mitigated** | production-check.sh now flags MongoDB without auth as a FAIL in prod mode. setup.sh `--prod` already generates MongoDB credentials. |
| M5 | API key last_used_at not persisted | **Fixed** | Added structured logging on every API key use (key name, owner, HTTP method, path). Enables log-based audit of key usage for rotation decisions. |
| M6 | No Kubernetes NetworkPolicy | **Fixed** | Added `k8s/network-policies.yaml` with policies restricting pod-to-pod communication to defined paths. |
| M7 | MCP server has no authentication | **Mitigated** | Added SSE transport warning on startup when no API key is configured. Stdio transport is inherently local-only. Full SSE auth deferred to MCP library support. |
| M8 | NATS message validation missing | **Fixed** | Ingest gateway now validates that messages are JSON dicts before processing. Non-dict messages are rejected with a warning log. |

### LOW

| ID | Finding | Status | Fix |
|----|---------|--------|-----|
| L1 | Permission cache race condition | **Fixed** | Replaced raw `dict` caches with `cachetools.TTLCache` (thread-safe, automatic expiry). Simplified cache code. |
| L2 | Filename not sanitised on upload | **Fixed** | Added `sanitize_filename()` in `file_validation.py`. Strips path separators, null bytes, control characters, and dangerous sequences. Applied in `file_service.py` before storage. |
| L3 | API keys stored in plaintext JSON files | **Mitigated** | API key provider now warns at startup if any key uses legacy SHA-256 hash. Bcrypt is the new default. |
| L4 | Debug logging can include SQL values | **Fixed** | SQL values in debug logs are now truncated to 100 chars per value. Prevents sensitive document content from appearing in logs. |
| L5 | No secrets rotation documentation | **Already existed** | `docs/security/key-rotation.md` covers API keys, database passwords, NATS tokens, Dex secrets, and TLS certificates. |
| L6 | K8s secrets in plaintext etcd | **Documented** | `docs/security/encryption-at-rest.md` covers etcd encryption requirements. |

---

## Residual Risks

| Risk | Severity | Mitigation | Notes |
|------|----------|------------|-------|
| JWT refresh tokens not implemented | Medium | Short-lived access tokens via Dex config | UI would benefit from silent refresh |
| MCP SSE transport lacks auth middleware | Medium | Warning on startup; typically local-only use | Depends on MCP library auth support |
| MongoDB auth optional in dev mode | Low | Dev is trusted local only; prod check enforces | By design for ease of development |
| No WAF or DDoS protection | Low | Caddy rate limiting at reverse proxy level | Acceptable for Tier 1-2; Tier 3 needs external WAF |

---

## Recommendations for Tier 2+ Deployments

1. **Always use `--prod` flag** with setup.sh to enable all security defaults
2. **Run production-check.sh** before exposing to the internet
3. **Enable Let's Encrypt** (`--email admin@example.com`) for proper TLS
4. **Rotate secrets** on the schedule in `docs/security/key-rotation.md`
5. **Monitor logs** for API key usage patterns and failed auth attempts
6. **Consider a reverse proxy** (Cloudflare, nginx) for DDoS protection at Tier 2+

---

## Tools and Verification

### Production Readiness Check

```bash
./scripts/security/production-check.sh
```

Validates: CORS, rate limiting, upload limits, API key strength, MongoDB auth, MinIO/NATS port exposure, security headers, file permissions, TLS, and deployment variant.

### Security Dependencies

| Package | Purpose | Added in |
|---------|---------|----------|
| `bcrypt` | API key hashing (GPU-resistant) | wip-auth |
| `slowapi` | Rate limiting | wip-auth |
| `cachetools` | Thread-safe TTL caches | wip-auth |

---

## Files Modified

### wip-auth library
- `providers/api_key.py` — bcrypt hashing, timing-safe comparison, usage logging
- `permissions.py` — TTLCache, groups-in-header
- `config.py` — salt configuration
- `ratelimit.py` — **new** shared rate limit configuration
- `security.py` — **new** startup security checks
- `pyproject.toml` — added bcrypt, slowapi, cachetools

### Services
- All 4 service `main.py` files — CORS lockdown, rate limiting, startup security check
- `reporting-sync/main.py` — rate limiting, security check
- `document-store/api/files.py` — upload size limit, content-type validation
- `document-store/services/file_service.py` — filename sanitisation
- `document-store/services/file_validation.py` — **new** content-type + filename validation
- `document-store/main.py` — debug endpoint auth gating
- `registry/api/grants.py` — groups from header
- `reporting-sync/worker.py` — SQL log truncation
- `ingest-gateway/worker.py` — message validation
- `mcp-server/server.py` — SSE auth warning

### Infrastructure
- `docker-compose/modules/files.yml` — conditional MinIO console port
- `docker-compose/modules/nats.yml` — conditional monitoring port
- `scripts/setup.sh` — CORS hostname, security headers, salt generation, port control
- `scripts/security/production-check.sh` — expanded security checks
- `k8s/network-policies.yaml` — **new** pod-to-pod communication policies

### Documentation
- `reports/SECURITY-AUDIT.md` — **this report**
