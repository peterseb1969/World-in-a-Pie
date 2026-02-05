# WIP Container Inventory

This document provides a detailed inventory of all containers in the World In a Pie (WIP) system, including their configurations, exposed endpoints, and security mechanisms.

## Document Purpose

This inventory is intended for:
- Security analysis of interfaces and exposed endpoints
- Understanding which components are deployed in each profile
- Planning security hardening measures

---

## Profile Overview

WIP supports multiple deployment profiles optimized for different hardware and use cases:

| Profile | Target Hardware | Auth Method | RAM Usage | Description |
|---------|-----------------|-------------|-----------|-------------|
| **mac** | Mac development | OIDC + API Key | ~2GB | Full stack with Mongo Express |
| **pi-minimal** | Pi 4 (1-2GB) | API Key only | ~800MB | Minimal stack, no OIDC |
| **pi-standard** | Pi 4 (2-4GB) | OIDC + API Key | ~1GB | Full stack, no Mongo Express |
| **pi-large** | Pi 5 (8GB+) | OIDC + API Key | ~1.2GB | Full stack with Mongo Express |
| **dev-minimal** | Any | API Key only | ~1GB | Quick dev, no OIDC |

---

## Container Matrix by Profile

| Container | Container Name | mac | pi-minimal | pi-standard | pi-large | dev-minimal |
|-----------|---------------|:---:|:----------:|:-----------:|:--------:|:-----------:|
| **Infrastructure** |
| MongoDB | wip-mongodb | ✓ | ✓ | ✓ | ✓ | ✓ |
| Mongo Express | wip-mongo-express | ✓ | - | - | ✓ | ✓ |
| PostgreSQL | wip-postgres | ✓ | ✓ | ✓ | ✓ | ✓ |
| NATS | wip-nats | ✓ | ✓ | ✓ | ✓ | ✓ |
| MinIO | wip-minio | ✓ | - | ✓ | ✓ | - |
| Dex | wip-dex | ✓ | - | ✓ | ✓ | - |
| Caddy | wip-caddy | ✓ | - | ✓ | ✓ | - |
| **Services** |
| Registry | wip-registry-dev | ✓ | ✓ | ✓ | ✓ | ✓ |
| Def-Store | wip-def-store-dev | ✓ | ✓ | ✓ | ✓ | ✓ |
| Template Store | wip-template-store-dev | ✓ | ✓ | ✓ | ✓ | ✓ |
| Document Store | wip-document-store-dev | ✓ | ✓ | ✓ | ✓ | ✓ |
| Reporting Sync | wip-reporting-sync-dev | ✓ | ✓ | ✓ | ✓ | ✓ |
| WIP Console | wip-console-dev | ✓ | ✓ | ✓ | ✓ | ✓ |

**Total containers by profile:**
- mac: 13
- pi-minimal: 9
- pi-standard: 12
- pi-large: 13
- dev-minimal: 10

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

### Service Containers

#### 8. Registry (wip-registry-dev)

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

#### 9. Def-Store (wip-def-store-dev)

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

#### 10. Template Store (wip-template-store-dev)

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

#### 11. Document Store (wip-document-store-dev)

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

#### 12. Reporting Sync (wip-reporting-sync-dev)

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

#### 13. WIP Console (wip-console-dev)

| Attribute | Value |
|-----------|-------|
| **Image** | Built from `ui/wip-console/Dockerfile.dev` |
| **Purpose** | Web UI for all WIP operations |
| **Port** | 3000 (internal, via Caddy) |
| **Network** | wip-network |
| **Profiles** | All |

**Exposed Endpoints:**
- Internal: `http://wip-console-dev:3000` (via Caddy)
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
│                   ┌───────────────────────────────┼───────────────┐         │
│                   │            wip-network        │               │         │
│                   │                               ▼               │         │
│                   │   ┌─────────┐    ┌─────────────────────────┐ │         │
│                   │   │   Dex   │◄───│     WIP Console         │ │         │
│                   │   └─────────┘    └───────────┬─────────────┘ │         │
│                   │                              │               │         │
│                   │   ┌──────────────────────────┼───────────┐   │         │
│                   │   │                          ▼           │   │         │
│                   │   │  ┌──────────┐  ┌──────────────────┐  │   │         │
│                   │   │  │ Registry │  │ Def-Store        │  │   │         │
│                   │   │  │  :8001   │  │  :8002           │  │   │         │
│                   │   │  └────┬─────┘  └────────┬─────────┘  │   │         │
│                   │   │       │                 │            │   │         │
│                   │   │  ┌────┴─────┐  ┌────────┴─────────┐  │   │         │
│                   │   │  │ Template │  │ Document Store   │  │   │         │
│                   │   │  │  :8003   │  │  :8004           │  │   │         │
│                   │   │  └────┬─────┘  └────────┬─────────┘  │   │         │
│                   │   │       │                 │            │   │         │
│                   │   │       └────────┬────────┘            │   │         │
│                   │   │                │                     │   │         │
│                   │   │  ┌─────────────▼─────────────────┐   │   │         │
│                   │   │  │      Reporting Sync :8005     │   │   │         │
│                   │   │  └───────────────────────────────┘   │   │         │
│                   │   └──────────────────────────────────────┘   │         │
│                   │                                              │         │
│                   │   ┌──────────────────────────────────────┐   │         │
│                   │   │          DATA STORES                 │   │         │
│                   │   │  ┌─────────┐  ┌─────────┐  ┌──────┐  │   │         │
│                   │   │  │ MongoDB │  │Postgres │  │ NATS │  │   │         │
│                   │   │  │ :27017  │  │ :5432   │  │:4222 │  │   │         │
│                   │   │  └─────────┘  └─────────┘  └──────┘  │   │         │
│                   │   │  ┌─────────┐                         │   │         │
│                   │   │  │ MinIO   │                         │   │         │
│                   │   │  │ :9000   │                         │   │         │
│                   │   │  └─────────┘                         │   │         │
│                   │   └──────────────────────────────────────┘   │         │
│                   └──────────────────────────────────────────────┘         │
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
| 27017 | MongoDB | TCP | MongoDB wire protocol |
| 5432 | PostgreSQL | TCP | PostgreSQL wire protocol |
| 4222 | NATS | TCP | NATS client connections |
| 8222 | NATS | HTTP | NATS monitoring |
| 9000 | MinIO | HTTP | S3 API |
| 9001 | MinIO | HTTP | MinIO Console |
| 8081 | Mongo Express | HTTP | MongoDB admin UI |
| 5556 | Dex | HTTP | OIDC provider (internal) |
| 3000 | WIP Console | HTTP | Web UI (internal) |

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

---

## Recommended Security Hardening

For production deployments:

1. **Change all default credentials**
2. **Enable MongoDB authentication**
3. **Use strong PostgreSQL passwords**
4. **Configure NATS authentication**
5. **Use proper TLS certificates (Let's Encrypt or CA-signed)**
6. **Bind infrastructure ports to 127.0.0.1 only**
7. **Remove Mongo Express in production**
8. **Use network-level firewall rules**
9. **Consider service mesh for mTLS between services**

---

## Appendix: Compose Files

| File | Profile(s) | Description |
|------|------------|-------------|
| `docker-compose.infra.yml` | mac | Full infrastructure for Mac |
| `docker-compose.infra.pi.yml` | pi-standard, pi-large | Pi infrastructure with OIDC |
| `docker-compose.infra.pi.minimal.yml` | pi-minimal | Pi minimal (no OIDC) |
| `docker-compose.infra.minimal.yml` | dev-minimal | Minimal (no OIDC, has Mongo Express) |
| `components/*/docker-compose.dev.yml` | All | Individual service development |
| `ui/wip-console/docker-compose.dev.yml` | All | Web UI development |
