# WIP — Developer Guide

Essential context for working on the World In a Pie (WIP) codebase.

---

## What Is WIP

WIP is a universal template-driven document storage system. It runs on anything from a Raspberry Pi 5 (8GB) to cloud infrastructure. Users define terminologies and templates, then store validated documents against those templates. A reporting pipeline syncs data to PostgreSQL for analytics.

---

## Architecture

### Services

| Service | Port | Purpose |
|---------|------|---------|
| **Registry** | 8001 | ID generation, namespace management, synonyms |
| **Def-Store** | 8002 | Terminologies, terms, aliases, ontology relationships |
| **Template-Store** | 8003 | Document schemas, field definitions, inheritance, draft mode |
| **Document-Store** | 8004 | Document CRUD, versioning, term validation, file storage (MinIO), CSV/XLSX import, event replay |
| **Reporting-Sync** | 8005 | MongoDB → PostgreSQL sync via NATS events |
| **Ingest Gateway** | 8006 | Async bulk ingestion via NATS JetStream |
| **MCP Server** | stdio/SSE | 68 tools, 4 resources for AI-assisted development |
| **WIP Console** | 8443 | Vue 3 + PrimeVue UI (served via Caddy reverse proxy) |

### Infrastructure

- **MongoDB** — primary data store for all services
- **PostgreSQL** — reporting/analytics (synced from MongoDB)
- **NATS JetStream** — event bus (document events → reporting-sync, ingest-gateway)
- **MinIO** — binary file storage (S3-compatible)
- **Caddy** — reverse proxy, TLS termination, security headers
- **Dex** — OIDC identity provider (optional, for SSO)

### Shared Libraries

- **wip-auth** (`libs/wip-auth/`) — Python auth library used by all services. API key (bcrypt) + JWT/OIDC dual mode, rate limiting (slowapi), RBAC permissions.
- **@wip/client** (`libs/wip-client/`) — TypeScript client for app developers. 6 service classes, typed error hierarchy, bulk abstraction.
- **@wip/react** (`libs/wip-react/`) — React hooks wrapping @wip/client with TanStack Query.

---

## Running WIP

### Automated Setup

```bash
# Development (localhost, self-signed TLS)
./scripts/setup.sh --preset standard --localhost

# Network deployment (hostname, self-signed TLS)
./scripts/setup.sh --preset standard --hostname wip-pi.local

# Production (random secrets, auth hardened)
./scripts/setup.sh --preset standard --hostname wip-pi.local --prod -y
```

Presets: `core` (minimal), `standard` (+ reporting + ingest), `full` (all services), `headless` (no console), `analytics` (BI focus). See `config/presets/`.

### Access Points

| Service | URL |
|---------|-----|
| WIP Console | https://localhost:8443 |
| API Docs | http://localhost:{port}/docs |
| Mongo Express | http://localhost:8081 |
| MinIO Console | http://localhost:9001 |

### Test Users (Dex OIDC — dev mode only)

| Email | Password | Group |
|-------|----------|-------|
| admin@wip.local | admin123 | wip-admins |
| editor@wip.local | editor123 | wip-editors |
| viewer@wip.local | viewer123 | wip-viewers |

In production mode (`--prod`), random passwords are generated and saved to `data/secrets/credentials.txt`.

**Dev API Key:** `dev_master_key_for_testing`

---

## Development

### Running Tests

```bash
# Activate venv first
source .venv/bin/activate

# Run a component's tests locally
cd components/registry && PYTHONPATH=src pytest tests/ -v

# Run inside container
podman exec -it wip-registry pytest /app/tests -v
```

CI runs all component tests via `.gitea/workflows/test.yaml`.

### Quality Audit

```bash
# Quick check (no services needed): ruff, shellcheck, vulture, radon, mypy, eslint
./scripts/quality-audit.sh --quick

# Full check (services running): adds pytest coverage, API consistency
./scripts/quality-audit.sh

# CI mode (fails if issues exceed baseline)
./scripts/quality-audit.sh --quick --ci
```

### Security Checks

```bash
# Validate production hardening
./scripts/security/production-check.sh

# Generate a new API key
./scripts/security/generate-api-key.sh
```

### Seed Data

```bash
source .venv/bin/activate
pip install faker requests
python scripts/seed_comprehensive.py --profile standard
```

Profiles: `minimal` (50 docs), `standard` (500), `full` (2000), `performance` (100k).

---

## Key Conventions

### Bulk-First API

**Every write endpoint is bulk.** Single operations are `[item]`. No single-entity write endpoints exist.

- Request: `List[ItemRequest]` via `Body(...)`
- Response: `BulkResponse { results, total, succeeded, failed }`
- **Always HTTP 200** — errors are per-item in `results[i].status == "error"`
- Never check HTTP status for duplicates; check `result.status` and `result.error`
- Updates: `PUT /endpoint` with ID in body (not URL)
- Deletes: `DELETE /endpoint` with `[{"id": "..."}]` body
- Pagination: default 50, max 100; all list responses include `pages` field

See `docs/api-conventions.md` for full details and examples.

### Synonym Resolution

All service APIs accept **human-readable synonyms** wherever a canonical ID is expected. For example, `template_id="PATIENT"` resolves to the canonical UUID transparently at the API boundary via `wip-auth`'s `resolve_entity_id()`. UUIDs pass through without any Registry call. Resolution is best-effort — if it fails, the raw value passes through and downstream validation handles it. Auto-synonyms are registered at entity creation. Use `wip-toolkit backfill-synonyms` to register synonyms for pre-existing entities. See `docs/design/universal-synonym-resolution.md`.

### Uniqueness & Identity

- **Registry** is the central ID authority. Services compute composite keys → Registry hashes and deduplicates.
- **Versioned entities** (templates, documents): `entity_id` stays the same across versions; `(entity_id, version)` is the true unique key.
- **Document identity**: Template defines `identity_fields` → Document-Store sends values to Registry → SHA-256 hash → same hash = same document_id, new version.
- **Synonyms**: One entry can have multiple composite keys. Enables cross-namespace linking and external ID mapping.

See `docs/uniqueness-and-identity.md` for the full rules.

### OIDC Configuration (Critical)

**These THREE values MUST be identical — mismatch causes 401 errors:**

| Config File | Variable |
|-------------|----------|
| `config/dex/config.yaml` | `issuer` |
| `.env` | `WIP_AUTH_JWT_ISSUER_URL` |
| `.env` | `VITE_OIDC_AUTHORITY` |

**After changing `.env`, recreate containers** (`podman-compose down && up -d`), not just restart.

See `docs/network-configuration.md` for all deployment scenarios.

---

## Security Hardening

The following protections are in place (implemented during the security audit, March 2026):

- **CORS lockdown** — origins restricted (not wildcard), configurable via `WIP_CORS_ORIGINS`
- **Rate limiting** — slowapi on all services, configurable limits
- **API key hashing** — bcrypt with per-deployment salt, timing-safe comparison
- **File upload limits** — configurable max size (`WIP_MAX_UPLOAD_SIZE`, default 100MB)
- **Content-type validation** — magic-byte checking, configurable MIME allowlist
- **Security headers** — HSTS, CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy (via Caddy)
- **Debug endpoints gated** — require API key in production
- **Default key rejection** — services refuse to start with `dev_master_key_for_testing` in prod mode

See `docs/production-deployment.md` and `docs/security/` for details.

---

## Building Apps on WIP

To create a new app project that builds on WIP (not modifying WIP itself):

```bash
./scripts/create-app-project.sh /path/to/my-app --name "My App"
```

This generates the full project scaffold with MCP config, slash commands, reference docs, client libraries, and a starter CLAUDE.md. See `docs/WIP_AppSetup_Guide.md` for the full guide.

---

## File Structure

```
WorldInPie/
├── CLAUDE.md                 # This file
├── docs/                     # Documentation (architecture, APIs, security, design specs)
│   ├── design/               # Feature design documents
│   ├── security/             # Security docs (key rotation, encryption at rest)
│   └── slash-commands/       # Slash commands for app-building AI instances
├── scripts/                  # Setup, security, quality audit, seed data
├── config/                   # Caddy, Dex, presets, API key configs
├── libs/
│   ├── wip-auth/             # Shared Python auth library
│   ├── wip-client/           # @wip/client TypeScript library
│   └── wip-react/            # @wip/react hooks library
├── components/
│   ├── registry/             # ID & namespace management
│   ├── def-store/            # Terminologies & terms
│   ├── template-store/       # Document schemas
│   ├── document-store/       # Document storage, files, import, replay
│   ├── reporting-sync/       # PostgreSQL sync
│   ├── ingest-gateway/       # Async ingestion via NATS
│   ├── mcp-server/           # MCP server (68 tools, 4 resources)
│   └── seed_data/            # Test data generation
├── docker-compose/           # Modular compose: base.yml + modules/
├── k8s/                      # Kubernetes manifests
├── deploy/optional/          # Optional services (Metabase)
├── ui/wip-console/           # Vue 3 + PrimeVue UI
├── WIP-Toolkit/              # CLI toolkit
├── data/                     # Runtime data (volumes, secrets)
└── testdata/                 # Test fixtures
```

---

## Documentation Index

| Document | What it covers |
|----------|---------------|
| `docs/architecture.md` | System architecture, service interactions |
| `docs/api-conventions.md` | Bulk-first convention, BulkResponse contract |
| `docs/uniqueness-and-identity.md` | ID generation, Registry synonyms, identity hashing |
| `docs/data-models.md` | Document, template, term data models |
| `docs/authentication.md` | Auth modes, API keys, JWT/OIDC configuration |
| `docs/network-configuration.md` | 4 deployment scenarios, OIDC setup |
| `docs/production-deployment.md` | Production hardening guide |
| `docs/mcp-server.md` | MCP tools, resources, AI development workflow |
| `docs/reporting-layer.md` | MongoDB → PostgreSQL sync architecture |
| `docs/semantic-types.md` | 7 semantic field types with validation rules |
| `docs/bulk-import-tuning.md` | Tuning batch sizes for 100k+ imports |
| `docs/WIP_AppSetup_Guide.md` | Setting up app projects that build on WIP |
| `docs/roadmap.md` | Future plans, pending features, design docs |
| `docs/security/` | Key rotation, encryption at rest |
| `docs/design/` | Feature design documents (ontology, replay, draft mode, etc.) |
| `docs/release-checklist.md` | Pre-release verification checklist (code, tests, security, docs, deploy) |

---

## Caddy Proxy Gotcha

Use `handle` NOT `handle_path` for API routes. Services expect the full path:
```
handle /api/def-store/*    # CORRECT — preserves /api/def-store/
handle_path /api/def-store/*  # WRONG — strips prefix
```
