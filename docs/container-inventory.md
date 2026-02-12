# WIP Container Inventory

This document provides a detailed inventory of all containers in the World In a Pie (WIP) system, including their configurations, exposed endpoints, and security mechanisms.

## Document Purpose

This inventory is intended for:
- Security analysis of interfaces and exposed endpoints
- Understanding which components are deployed in each profile
- Planning security hardening measures

---

## Modular Deployment System

WIP uses a modular deployment system with **presets** (sensible defaults) and **modules** (composable features).

### Presets

| Preset | Auth Method | Modules Included | Use Case |
|--------|-------------|------------------|----------|
| **core** | API Key only | none | Minimal footprint, single-user |
| **standard** | OIDC + API Key | oidc | Most users, multi-user |
| **analytics** | OIDC + API Key | oidc, reporting | SQL reporting, BI dashboards |
| **full** | OIDC + API Key | oidc, reporting, files, ingest | All features |

### Modules

| Module | Services Added | Purpose |
|--------|---------------|---------|
| `oidc` | Dex, Caddy | User authentication via OpenID Connect |
| `reporting` | PostgreSQL | SQL analytics, Reporting-Sync |
| `files` | MinIO | Binary file attachments |
| `ingest` | (Ingest Gateway service) | Streaming data ingestion via NATS |
| `dev-tools` | Mongo Express | Database inspection (dev variant only) |
| `bi` | Metabase | Self-service BI dashboards (optional, separate deployment) |

### Variants

| Variant | Description |
|---------|-------------|
| **dev** | Development mode, includes dev-tools module |
| **prod** | Production mode, no dev-tools, stricter settings (planned) |

### Platform Detection

| Platform | MongoDB Version | Detection |
|----------|-----------------|-----------|
| default | mongo:7 | Mac, Linux x86_64, Pi 5 |
| pi4 | mongo:4.4.18 | Raspberry Pi 4 (ARMv8.0) |

---

## Container Matrix by Module

| Container | Container Name | Base | oidc | reporting | files | dev-tools | bi |
|-----------|---------------|:----:|:----:|:---------:|:-----:|:---------:|:--:|
| **Infrastructure** |
| MongoDB | wip-mongodb | ✓ | | | | | |
| NATS | wip-nats | ✓ | | | | | |
| Dex | wip-dex | | ✓ | | | | |
| Caddy | wip-caddy | | ✓ | | | | |
| PostgreSQL | wip-postgres | | | ✓ | | | |
| MinIO | wip-minio | | | | ✓ | | |
| Mongo Express | wip-mongo-express | | | | | ✓ | |
| Metabase | wip-metabase | | | | | | ✓ |
| **Services** |
| Registry | wip-registry | ✓ | | | | | |
| Def-Store | wip-def-store | ✓ | | | | | |
| Template Store | wip-template-store | ✓ | | | | | |
| Document Store | wip-document-store | ✓ | | | | | |
| Reporting Sync | wip-reporting-sync | | | ✓ | | | |
| Ingest Gateway | wip-ingest-gateway | | | | | | |
| WIP Console | wip-console | ✓ | | | | | |

**Note:** Ingest Gateway is started when the `ingest` module is active, but doesn't require additional infrastructure.

### Container Counts by Preset

| Preset | Dev Variant | Prod Variant |
|--------|-------------|--------------|
| core | 8 (base + dev-tools) | 7 |
| standard | 10 (base + oidc + dev-tools) | 9 |
| analytics | 12 (base + oidc + reporting + dev-tools) | 11 |
| full | 14 (all modules) | 13 |

---

## Detailed Container Specifications

### Infrastructure Containers

#### 1. MongoDB (wip-mongodb)

| Attribute | Value |
|-----------|-------|
| **Image** | `mongo:7` (Mac/dev-minimal) / `mongo:4.4.18` (Pi) |
| **Purpose** | Primary document store for all services |
| **Port** | 27017 |
| **Network** | wip-network |
| **Volumes** | `${WIP_DATA_DIR}/mongodb:/data/db` |
| **Profiles** | All |

**Exposed Endpoint:**
- `localhost:27017` - MongoDB wire protocol

**Security:**
- No authentication configured (development mode)
- Accessible only from containers on wip-network
- **Risk:** Host port exposure allows local network access

---

#### 2. Mongo Express (wip-mongo-express)

| Attribute | Value |
|-----------|-------|
| **Image** | `mongo-express` |
| **Purpose** | MongoDB web admin UI |
| **Port** | 8081 |
| **Network** | wip-network |
| **Profiles** | mac, pi-large, dev-minimal |

**Exposed Endpoint:**
- `http://localhost:8081` - Web UI

**Security:**
- Basic Auth: `admin` / `admin`
- **Risk:** Weak default credentials, allows database manipulation

---

#### 3. PostgreSQL (wip-postgres)

| Attribute | Value |
|-----------|-------|
| **Image** | `postgres:16` |
| **Purpose** | Reporting data store (SQL analytics) |
| **Port** | 5432 |
| **Network** | wip-network |
| **Volumes** | `${WIP_DATA_DIR}/postgres:/var/lib/postgresql/data` |
| **Profiles** | All |

**Exposed Endpoint:**
- `localhost:5432` - PostgreSQL wire protocol

**Security:**
- Credentials: `wip` / `wip_dev_password`
- Database: `wip_reporting`
- **Risk:** Weak default credentials, host port exposure

---

#### 4. NATS (wip-nats)

| Attribute | Value |
|-----------|-------|
| **Image** | `nats:2.10` |
| **Purpose** | Message queue for event-driven sync |
| **Ports** | 4222 (client), 8222 (monitoring) |
| **Network** | wip-network |
| **Volumes** | `${WIP_DATA_DIR}/nats:/data` |
| **Profiles** | All |

**Exposed Endpoints:**
- `localhost:4222` - NATS client connections
- `http://localhost:8222` - HTTP monitoring API

**Security:**
- No authentication configured
- JetStream enabled for message persistence
- **Risk:** Unauthenticated access to message queue

---

#### 5. MinIO (wip-minio)

| Attribute | Value |
|-----------|-------|
| **Image** | `quay.io/minio/minio:latest` |
| **Purpose** | S3-compatible file storage |
| **Ports** | 9000 (S3 API), 9001 (Console) |
| **Network** | wip-network |
| **Volumes** | `${WIP_DATA_DIR}/minio:/data` |
| **Profiles** | mac, pi-standard, pi-large |

**Exposed Endpoints:**
- `localhost:9000` - S3 API
- `http://localhost:9001` - Web Console

**Security:**
- Credentials: `wip-minio-root` / `wip-minio-password`
- Bucket: `wip-attachments`
- **Risk:** Weak default credentials, file access without service-level auth

---

#### 6. Dex (wip-dex)

| Attribute | Value |
|-----------|-------|
| **Image** | `ghcr.io/dexidp/dex:v2.38.0` |
| **Purpose** | OIDC identity provider |
| **Port** | 5556 (internal, via Caddy) |
| **Network** | wip-network |
| **Volumes** | `./config/dex:/etc/dex:ro`, `${WIP_DATA_DIR}/dex:/data` |
| **Profiles** | mac, pi-standard, pi-large |

**Exposed Endpoints:**
- Internal: `http://wip-dex:5556/dex/` (via Caddy proxy)
- External: `https://localhost:8443/dex/` (via Caddy)

**Security:**
- Static users configured in YAML:
  - `admin@wip.local` / `admin123` (wip-admins)
  - `editor@wip.local` / `editor123` (wip-editors)
  - `viewer@wip.local` / `viewer123` (wip-viewers)
- Password grant enabled (development convenience)
- **Risk:** Weak default passwords, password grant not for production

---

#### 7. Caddy (wip-caddy)

| Attribute | Value |
|-----------|-------|
| **Image** | `caddy:2-alpine` |
| **Purpose** | Reverse proxy, TLS termination |
| **Ports** | 8080 (HTTP), 8443 (HTTPS) |
| **Network** | wip-network |
| **Volumes** | `./config/caddy/Caddyfile:ro`, `${WIP_DATA_DIR}/caddy/` |
| **Profiles** | mac, pi-standard, pi-large |

**Exposed Endpoints:**
- `http://localhost:8080` - HTTP (redirects to HTTPS)
- `https://localhost:8443` - HTTPS (self-signed cert)

**Security:**
- Auto-generated self-signed TLS certificate
- Proxies all services on single endpoint
- No additional authentication layer
- **Risk:** Self-signed cert requires browser exception

---

#### 8. Metabase (wip-metabase)

| Attribute | Value |
|-----------|-------|
| **Image** | `docker.io/metabase/metabase:latest` |
| **Purpose** | Self-service BI dashboards |
| **Port** | 3030 |
| **Network** | wip-network |
| **Volumes** | `./data:/metabase-data` |
| **Deployment** | Optional, separate (`deploy/optional/metabase/`) |

**Exposed Endpoints:**
- `http://localhost:3030` - Metabase web UI

**Database Connection:**
- Connects to WIP PostgreSQL (`wip-postgres:5432`)
- Database: `wip_reporting`
- SSL must be disabled (WIP PostgreSQL doesn't use SSL)

**Security:**
- Web-based authentication (email/password, configured in Metabase)
- No default credentials (setup wizard on first run)
- Stores own data in H2 embedded database (or external PostgreSQL for production)
- **Risk:** Direct read access to all WIP reporting data
- **Risk:** No integration with WIP OIDC - separate user management

**Resource Usage:**
- ~500MB-1GB RAM (Java-based)
- Configurable via `JAVA_OPTS` environment variable

---

### Service Containers

#### 9. Registry (wip-registry)

| Attribute | Value |
|-----------|-------|
| **Image** | Built from `components/registry/Dockerfile` |
| **Purpose** | ID registry and namespace management |
| **Port** | 8001 |
| **Network** | wip-network |
| **Profiles** | All |

**Exposed Endpoints:**
- `http://localhost:8001/api/registry/` - REST API
- `http://localhost:8001/docs` - Swagger UI

**API Endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| POST | `/entries/register` | Register composite keys (bulk) |
| POST | `/entries/lookup/by-id` | Lookup by ID (bulk) |
| POST | `/entries/lookup/by-key` | Lookup by composite key (bulk) |
| PUT | `/entries` | Update entries (bulk) |
| DELETE | `/entries` | Soft-delete entries (bulk) |
| POST | `/synonyms/add` | Add synonyms |
| GET | `/namespaces` | List namespaces |
| POST | `/namespaces` | Create namespace |
| GET | `/search` | Cross-namespace search |

**Security:**
- `X-API-Key` header required
- Environment: `MASTER_API_KEY=dev_master_key_for_testing`
- **Risk:** Single shared API key for all operations

---

#### 10. Def-Store (wip-def-store)

| Attribute | Value |
|-----------|-------|
| **Image** | Built from `components/def-store/Dockerfile` |
| **Purpose** | Terminology and term management |
| **Port** | 8002 |
| **Network** | wip-network |
| **Profiles** | All |

**Exposed Endpoints:**
- `http://localhost:8002/api/def-store/` - REST API
- `http://localhost:8002/docs` - Swagger UI

**API Endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/terminologies` | List/Create terminologies |
| GET/PUT/DELETE | `/terminologies/{id}` | CRUD terminology |
| GET/POST | `/terminologies/{id}/terms` | List/Create terms |
| POST | `/terminologies/{id}/terms/bulk` | Bulk create terms |
| GET/PUT/DELETE | `/terms/{id}` | CRUD term |
| GET | `/terms/{id}/audit` | Term audit log |
| POST | `/validate` | Validate term value |
| POST | `/validate/bulk` | Bulk validate |
| POST | `/import-export/import` | Import terminology |
| GET | `/import-export/export/{id}` | Export terminology |

**Security:**
- Dual auth mode: `X-API-Key` OR `Authorization: Bearer <JWT>`
- wip-auth library with named API keys
- **Auth Config:** `WIP_AUTH_MODE=dual`

---

#### 11. Template Store (wip-template-store)

| Attribute | Value |
|-----------|-------|
| **Image** | Built from `components/template-store/Dockerfile` |
| **Purpose** | Document template/schema management |
| **Port** | 8003 |
| **Network** | wip-network |
| **Profiles** | All |

**Exposed Endpoints:**
- `http://localhost:8003/api/template-store/` - REST API
- `http://localhost:8003/docs` - Swagger UI

**API Endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/templates` | List/Create templates |
| GET/PUT/DELETE | `/templates/{id}` | CRUD template |
| GET | `/templates/by-code/{code}` | Get by code |
| GET | `/templates/by-code/{code}/versions` | List versions |
| POST | `/templates/{id}/validate` | Validate template |
| POST | `/templates/bulk` | Bulk operations |
| GET | `/{id}/dependencies` | Check dependencies |

**Security:**
- Dual auth mode: `X-API-Key` OR `Authorization: Bearer <JWT>`
- Publishes events to NATS on create/update

---

#### 12. Document Store (wip-document-store)

| Attribute | Value |
|-----------|-------|
| **Image** | Built from `components/document-store/Dockerfile` |
| **Purpose** | Document storage, validation, versioning |
| **Port** | 8004 |
| **Network** | wip-network |
| **Profiles** | All |

**Exposed Endpoints:**
- `http://localhost:8004/api/document-store/` - REST API
- `http://localhost:8004/docs` - Swagger UI

**API Endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/documents` | List/Create documents |
| GET/PUT/DELETE | `/documents/{id}` | CRUD document |
| GET | `/documents/{id}/versions` | Version history |
| GET | `/documents/{id}/latest` | Get latest version |
| POST | `/documents/bulk` | Bulk operations |
| GET | `/table/{template_id}` | Table view |
| GET | `/table/{template_id}/csv` | CSV export |
| POST | `/files` | Upload file |
| GET | `/files/{id}` | Get file metadata |
| GET | `/files/{id}/download` | Download file |

**Security:**
- Dual auth mode: `X-API-Key` OR `Authorization: Bearer <JWT>`
- Publishes events to NATS on document changes
- Validates against templates and terminologies

---

#### 13. Reporting Sync (wip-reporting-sync)

| Attribute | Value |
|-----------|-------|
| **Image** | Built from `components/reporting-sync/Dockerfile` |
| **Purpose** | MongoDB → PostgreSQL sync worker |
| **Port** | 8005 |
| **Network** | wip-network |
| **Profiles** | All |

**Exposed Endpoints:**
- `http://localhost:8005/` - REST API
- `http://localhost:8005/docs` - Swagger UI

**API Endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/status` | Sync worker status |
| GET | `/metrics` | Sync metrics |
| GET | `/metrics/consumer` | NATS consumer info |
| GET | `/alerts` | Active alerts |
| PUT | `/alerts/config` | Configure alerts |
| GET | `/schema/{template_code}` | View generated schema |
| POST | `/sync/batch/{template_code}` | Trigger batch sync |
| GET | `/sync/batch/jobs` | List sync jobs |
| GET | `/health/integrity` | Data integrity check |

**Security:**
- Dual auth mode: `X-API-Key` OR `Authorization: Bearer <JWT>`
- Consumes NATS events (no authentication on NATS side)

---

#### 14. Ingest Gateway (wip-ingest-gateway)

| Attribute | Value |
|-----------|-------|
| **Image** | Built from `components/ingest-gateway/Dockerfile` |
| **Purpose** | NATS-based streaming ingestion gateway |
| **Port** | 8006 |
| **Network** | wip-network |
| **Profiles** | All |

**Exposed Endpoints:**
- `http://localhost:8006/` - REST API
- `http://localhost:8006/docs` - Swagger UI

**API Endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check (NATS + worker status) |
| GET | `/status` | Worker status and statistics |
| GET | `/metrics` | Detailed processing metrics |
| GET | `/` | Service info |

**NATS Subjects (Inbound - WIP_INGEST stream):**
| Subject | Action | Description |
|---------|--------|-------------|
| `wip.ingest.terminologies.create` | Create terminology | Forward to Def-Store |
| `wip.ingest.terms.bulk` | Bulk create terms | Forward to Def-Store |
| `wip.ingest.templates.create` | Create template | Forward to Template Store |
| `wip.ingest.templates.bulk` | Bulk create templates | Forward to Template Store |
| `wip.ingest.documents.create` | Create document | Forward to Document Store |
| `wip.ingest.documents.bulk` | Bulk create documents | Forward to Document Store |

**NATS Subjects (Outbound - WIP_INGEST_RESULTS stream):**
| Subject | Description |
|---------|-------------|
| `wip.ingest.results.>` | Processing results with correlation_id |

**Message Format (Inbound):**
```json
{
  "correlation_id": "unique-tracking-id",
  "payload": { ... }  // REST API request body
}
```

**Result Format (Outbound):**
```json
{
  "correlation_id": "unique-tracking-id",
  "action": "documents.create",
  "status": "success|partial|failed",
  "http_status_code": 201,
  "response": { ... },
  "error": null,
  "processed_at": "2024-01-30T10:00:00Z",
  "duration_ms": 45.2
}
```

**Security:**
- Uses API key for forwarding requests to backend services
- NATS streams have no authentication (relies on network isolation)
- **Risk:** Any client with NATS access can publish ingest messages

---

#### 15. WIP Console (wip-console)

| Attribute | Value |
|-----------|-------|
| **Image** | Built from `ui/wip-console/Dockerfile` |
| **Purpose** | Web UI for all WIP operations |
| **Port** | 80 (internal, via Caddy) |
| **Network** | wip-network |
| **Profiles** | All |

**Exposed Endpoints:**
- Internal: `http://wip-console:80` (via Caddy)
- External: `https://localhost:8443/` (via Caddy)

**Security:**
- Authenticates via OIDC (Dex) or API key
- Stores auth state in browser localStorage
- All API calls go through backend services

---

## Network Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              EXTERNAL ACCESS                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   Browser ──────► https://localhost:8443 ──────► Caddy                      │
│                                                   │                          │
│   External ─────► NATS :4222 ──► Ingest Gateway ─┼──────────────┐           │
│   Systems          (WIP_INGEST)     :8006        │              │           │
│                                                   │              │           │
│                   ┌───────────────────────────────┼──────────────┼─┐        │
│                   │            wip-network        │              │ │        │
│                   │                               ▼              │ │        │
│                   │   ┌─────────┐    ┌─────────────────────────┐ │ │        │
│                   │   │   Dex   │◄───│     WIP Console         │ │ │        │
│                   │   └─────────┘    └───────────┬─────────────┘ │ │        │
│                   │                              │               │ │        │
│                   │   ┌──────────────────────────┼───────────────┼─┼──┐     │
│                   │   │   WIP SERVICES           ▼               │ │  │     │
│                   │   │  ┌──────────┐  ┌──────────────────┐  ◄───┘ │  │     │
│                   │   │  │ Registry │  │ Def-Store        │        │  │     │
│                   │   │  │  :8001   │  │  :8002           │  ◄─────┘  │     │
│                   │   │  └────┬─────┘  └────────┬─────────┘           │     │
│                   │   │       │                 │                     │     │
│                   │   │  ┌────┴─────┐  ┌────────┴─────────┐           │     │
│                   │   │  │ Template │  │ Document Store   │           │     │
│                   │   │  │  :8003   │  │  :8004           │           │     │
│                   │   │  └────┬─────┘  └────────┬─────────┘           │     │
│                   │   │       │                 │                     │     │
│                   │   │       └────────┬────────┘                     │     │
│                   │   │                │                              │     │
│                   │   │  ┌─────────────▼─────────────────┐            │     │
│                   │   │  │      Reporting Sync :8005     │            │     │
│                   │   │  └───────────────────────────────┘            │     │
│                   │   └───────────────────────────────────────────────┘     │
│                   │                                                         │
│                   │   ┌─────────────────────────────────────────────────┐   │
│                   │   │          DATA STORES                            │   │
│                   │   │  ┌─────────┐  ┌─────────┐  ┌─────────────────┐  │   │
│                   │   │  │ MongoDB │  │Postgres │  │ NATS            │  │   │
│                   │   │  │ :27017  │  │ :5432   │  │ :4222           │  │   │
│                   │   │  └─────────┘  └─────────┘  │ WIP_INGEST      │  │   │
│                   │   │  ┌─────────┐               │ WIP_INGEST_RESULTS│ │   │
│                   │   │  │ MinIO   │               │ WIP_DOCUMENTS   │  │   │
│                   │   │  │ :9000   │               └─────────────────┘  │   │
│                   │   │  └─────────┘                                    │   │
│                   │   └─────────────────────────────────────────────────┘   │
│                   └─────────────────────────────────────────────────────────┘
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Port Summary

| Port | Container | Protocol | Purpose |
|------|-----------|----------|---------|
| 8443 | Caddy | HTTPS | Main entry point (reverse proxy) |
| 8080 | Caddy | HTTP | Redirects to HTTPS |
| 8001 | Registry | HTTP | Registry API |
| 8002 | Def-Store | HTTP | Terminology API |
| 8003 | Template Store | HTTP | Template API |
| 8004 | Document Store | HTTP | Document API |
| 8005 | Reporting Sync | HTTP | Sync API |
| 8006 | Ingest Gateway | HTTP | Streaming ingest API |
| 27017 | MongoDB | TCP | MongoDB wire protocol |
| 5432 | PostgreSQL | TCP | PostgreSQL wire protocol |
| 4222 | NATS | TCP | NATS client connections |
| 8222 | NATS | HTTP | NATS monitoring |
| 9000 | MinIO | HTTP | S3 API |
| 9001 | MinIO | HTTP | MinIO Console |
| 8081 | Mongo Express | HTTP | MongoDB admin UI |
| 5556 | Dex | HTTP | OIDC provider (internal) |
| 80 | WIP Console | HTTP | Web UI (internal) |
| 3030 | Metabase | HTTP | BI dashboards (optional) |

---

## Security Summary

### Authentication Mechanisms

| Mechanism | Description | Used By |
|-----------|-------------|---------|
| API Key | `X-API-Key` header | All services |
| JWT (OIDC) | `Authorization: Bearer` | All services (via wip-auth) |
| Basic Auth | Username/Password | Mongo Express only |
| None | Unauthenticated | MongoDB, PostgreSQL, NATS, MinIO (internal) |

### Current Security Concerns

1. **Weak Default Credentials**
   - All services: `dev_master_key_for_testing`
   - Dex users: `admin123`, `editor123`, `viewer123`
   - Mongo Express: `admin` / `admin`
   - PostgreSQL: `wip` / `wip_dev_password`
   - MinIO: `wip-minio-root` / `wip-minio-password`

2. **Unauthenticated Data Stores**
   - MongoDB: No auth, port 27017 exposed
   - PostgreSQL: Weak password, port 5432 exposed
   - NATS: No auth, ports 4222/8222 exposed
   - MinIO: Weak password, ports 9000/9001 exposed

3. **Self-Signed TLS**
   - Caddy generates self-signed certificates
   - Requires browser exception
   - Not suitable for production

4. **Network Exposure**
   - All infrastructure ports bound to host
   - Accessible from local network (not just localhost)

5. **Unauthenticated NATS Ingestion**
   - Ingest Gateway consumes from NATS without authentication
   - Any client with NATS access can publish to `wip.ingest.>` subjects
   - Can bypass REST API authentication entirely
   - **Risk:** Network-level access to NATS allows unrestricted data injection

6. **Metabase Separate Authentication** (if deployed)
   - Metabase has its own user management, not integrated with WIP OIDC
   - Direct read access to all PostgreSQL reporting data
   - **Risk:** Users must be managed in two places (WIP + Metabase)

---

## Raspberry Pi Deployment

### Prerequisites

```bash
# Install Podman on Raspberry Pi OS
sudo apt update
sudo apt install -y podman podman-compose jq

# Clone repository
git clone <your-repo-url>
cd WorldInPie
```

### Quick Setup

```bash
# Auto-detect Pi model and deploy
./scripts/setup.sh --hostname your-pi.local

# Or specify preset explicitly
./scripts/setup.sh --preset standard --hostname your-pi.local
./scripts/setup.sh --preset core  # API keys only, minimal footprint
./scripts/setup.sh --preset full --hostname your-pi.local

# With external storage
./scripts/setup.sh --data-dir /mnt/usb-ssd --preset standard --hostname your-pi.local
```

### Why Caddy for OIDC?

The OIDC library (oidc-client-ts) uses PKCE which requires `Crypto.subtle`, available only in secure contexts (HTTPS or localhost). Caddy provides:

- Auto-generated self-signed TLS certificate
- Reverse proxy for all services on single port (8443)
- OIDC login works over network without SSH tunnels
- ~25MB RAM overhead

For API-key-only deployments, use `--preset core` to skip Caddy.

### Pi-Specific Notes

| Consideration | Details |
|---------------|---------|
| **MongoDB version** | Pi 4 requires MongoDB 4.4.x (ARMv8.0), Pi 5 can use 7.x |
| **Health check timing** | Pi profiles use longer intervals (10s vs 5s) |
| **Mongo Express** | Excluded from prod variant to save ~200MB RAM |
| **SD Card wear** | Consider external USB SSD for data directory |
| **Linger** | Rootless Podman requires `sudo loginctl enable-linger $USER` (setup.sh enables this automatically) |

---

## Backup and Recovery

### MongoDB Backup

```bash
#!/bin/bash
BACKUP_DIR=/backups/mongodb
DATE=$(date +%Y%m%d_%H%M%S)

# Dump database
podman exec wip-mongodb mongodump --out="/data/backup_$DATE"

# Copy from container
podman cp wip-mongodb:/data/backup_$DATE "$BACKUP_DIR/$DATE"

# Compress
tar -czf "$BACKUP_DIR/wip_$DATE.tar.gz" -C "$BACKUP_DIR" "$DATE"
rm -rf "$BACKUP_DIR/$DATE"

# Retain last 30 days
find "$BACKUP_DIR" -name "*.tar.gz" -mtime +30 -delete

echo "Backup complete: $BACKUP_DIR/wip_$DATE.tar.gz"
```

### PostgreSQL Backup

```bash
#!/bin/bash
BACKUP_DIR=/backups/postgres
DATE=$(date +%Y%m%d_%H%M%S)

# Dump database
podman exec wip-postgres pg_dump -U wip wip_reporting | gzip > "$BACKUP_DIR/wip_reporting_$DATE.sql.gz"

# Retain last 30 days
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +30 -delete

echo "Backup complete: $BACKUP_DIR/wip_reporting_$DATE.sql.gz"
```

### Recovery

```bash
# MongoDB restore
tar -xzf wip_20240115_120000.tar.gz
podman cp 20240115_120000 wip-mongodb:/data/
podman exec wip-mongodb mongorestore /data/20240115_120000

# PostgreSQL restore
gunzip -c wip_reporting_20240115_120000.sql.gz | \
  podman exec -i wip-postgres psql -U wip wip_reporting
```

---

## Recommended Security Hardening

For production deployments:

1. **Change all default credentials**
2. **Enable MongoDB authentication**
3. **Use strong PostgreSQL passwords**
4. **Configure NATS authentication** (critical for Ingest Gateway security)
5. **Use proper TLS certificates (Let's Encrypt or CA-signed)**
6. **Bind infrastructure ports to 127.0.0.1 only**
7. **Remove Mongo Express in production**
8. **Use network-level firewall rules**
9. **Consider service mesh for mTLS between services**
10. **Restrict NATS ingest subjects** to authorized publishers only

---

## Appendix: Compose Files

### Modular Infrastructure (used by setup.sh)

| File | Purpose |
|------|---------|
| `docker-compose/base.yml` | Core infrastructure (MongoDB, NATS) |
| `docker-compose/modules/oidc.yml` | OIDC module (Dex, Caddy) |
| `docker-compose/modules/reporting.yml` | Reporting module (PostgreSQL) |
| `docker-compose/modules/files.yml` | Files module (MinIO) |
| `docker-compose/modules/dev-tools.yml` | Dev tools module (Mongo Express) |
| `docker-compose/platforms/default.yml` | Default platform (MongoDB 7) |
| `docker-compose/platforms/pi4.yml` | Pi 4 platform (MongoDB 4.4.18) |

### Service Compose Files

| File | Description |
|------|-------------|
| `components/*/docker-compose.yml` | Individual service configuration |
| `components/*/docker-compose.override.yml` | Dev overrides (auto-generated) |
| `ui/wip-console/docker-compose.yml` | Web UI configuration |

### Preset Configuration

| File | Description |
|------|-------------|
| `config/presets/core.conf` | Core preset (API keys only) |
| `config/presets/standard.conf` | Standard preset (OIDC) |
| `config/presets/analytics.conf` | Analytics preset (OIDC + reporting) |
| `config/presets/full.conf` | Full preset (all modules) |
