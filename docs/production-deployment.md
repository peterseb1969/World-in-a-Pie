# Production Deployment Guide

This document covers production deployment considerations for World In a Pie (WIP).

## Current State Assessment

WIP's deployment infrastructure is **excellent for development and small-scale deployments** (Raspberry Pi, home labs, single-server setups) but requires additional work for full production readiness.

### What's Implemented

| Feature | Status | Notes |
|---------|--------|-------|
| Docker Compose orchestration | ✅ | 5 profiles, automated setup |
| TLS termination | ✅ | Caddy with self-signed certs |
| OIDC authentication | ✅ | Dex with static users |
| API key authentication | ✅ | Named keys with groups |
| Health checks | ✅ | Per-service endpoints |
| Basic metrics | ✅ | Reporting-sync only |
| Configurable storage | ✅ | External mounts supported |
| Multi-platform | ✅ | Mac, Pi 4, Pi 5 |
| Production compose file | ✅ | `docker-compose.infra.prod.yml` |

### What's Missing

| Feature | Priority | Status |
|---------|----------|--------|
| Production TLS (Let's Encrypt) | High | ❌ Not implemented |
| Enterprise OIDC (Authentik/Keycloak) | High | ❌ Not implemented |
| Centralized monitoring (Prometheus/Grafana) | High | ❌ Not implemented |
| Centralized logging (Loki/ELK) | Medium | ❌ Not implemented |
| Backup/restore procedures | Critical | ❌ Not documented |
| High availability (replica sets) | Medium | ❌ Not implemented |
| Kubernetes/Helm charts | Low | ❌ Not implemented |

---

## Quick Start: Production Deployment

### Prerequisites

1. Linux server with Docker/Podman installed
2. Domain name pointing to your server
3. Firewall allowing ports 80 and 443

### Step 1: Clone and Configure

```bash
# Clone repository
git clone https://github.com/your-org/World-In-A-Pie.git
cd World-In-A-Pie

# Create data directory
sudo mkdir -p /opt/wip/data
sudo chown $(whoami):$(whoami) /opt/wip/data

# Copy and edit production environment
cp .env.prod.example .env.prod
nano .env.prod  # Edit with your values
```

### Step 2: Generate Secrets

```bash
# Create secrets directory
mkdir -p /opt/wip/data/secrets
chmod 700 /opt/wip/data/secrets

# Generate strong random passwords
openssl rand -hex 32 > /opt/wip/data/secrets/mongo_password
openssl rand -hex 32 > /opt/wip/data/secrets/postgres_password
openssl rand -hex 32 > /opt/wip/data/secrets/minio_password
openssl rand -hex 32 > /opt/wip/data/secrets/api_key

# Secure the files
chmod 600 /opt/wip/data/secrets/*
```

### Step 3: Configure Dex (OIDC)

Edit `config/dex/config.yaml` for production:

```yaml
issuer: https://your-domain.com/dex

storage:
  type: sqlite3
  config:
    file: /data/dex.db

web:
  http: 0.0.0.0:5556

staticClients:
  - id: wip-console
    name: WIP Console
    secret: YOUR_CLIENT_SECRET_HERE  # Generate with: openssl rand -hex 32
    redirectURIs:
      - https://your-domain.com/auth/callback

# For production, use LDAP, OIDC upstream, or SAML instead of static passwords
connectors:
  - type: ldap
    id: ldap
    name: Company LDAP
    config:
      host: ldap.company.com:636
      # ... LDAP configuration
```

### Step 4: Configure Caddy for Let's Encrypt

Create `config/caddy/Caddyfile.prod`:

```caddyfile
{
    email admin@your-domain.com
    acme_ca https://acme-v02.api.letsencrypt.org/directory
}

your-domain.com {
    # OIDC Provider
    handle /dex/* {
        reverse_proxy wip-dex:5556
    }

    # API Services
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
    handle /api/reporting-sync/* {
        reverse_proxy wip-reporting-sync:8005
    }

    # Console UI (catch-all)
    handle {
        reverse_proxy wip-console:3000
    }

    # Security headers
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "SAMEORIGIN"
        Referrer-Policy "strict-origin-when-cross-origin"
    }

    # Health check endpoint
    handle /health {
        respond "OK" 200
    }
}
```

### Step 5: Start Infrastructure

```bash
# Start infrastructure with production compose
podman-compose --env-file .env.prod -f docker-compose.infra.prod.yml up -d

# Wait for health checks
sleep 30

# Verify all services are healthy
podman-compose --env-file .env.prod -f docker-compose.infra.prod.yml ps
```

### Step 6: Start Application Services

```bash
# Start each service in order
cd components/registry && podman-compose --env-file ../../.env.prod -f docker-compose.yml up -d
cd ../def-store && podman-compose --env-file ../../.env.prod -f docker-compose.yml up -d
cd ../template-store && podman-compose --env-file ../../.env.prod -f docker-compose.yml up -d
cd ../document-store && podman-compose --env-file ../../.env.prod -f docker-compose.yml up -d
cd ../reporting-sync && podman-compose --env-file ../../.env.prod -f docker-compose.yml up -d
cd ../../ui/wip-console && podman-compose --env-file ../../.env.prod -f docker-compose.yml up -d
```

---

## Production Gaps: Detailed Analysis

### 1. Secret Management

**Current State:** Environment variables and file-based secrets.

**Production Recommendation:**

For small deployments, file-based secrets (implemented in `docker-compose.infra.prod.yml`) are sufficient. For enterprise deployments, consider:

| Solution | Complexity | Best For |
|----------|------------|----------|
| File-based (current) | Low | Small teams, single-server |
| HashiCorp Vault | High | Enterprise, multi-team |
| AWS Secrets Manager | Medium | AWS deployments |
| Azure Key Vault | Medium | Azure deployments |

**Implementation Notes:**

```bash
# Current approach (file-based)
echo "$(openssl rand -hex 32)" > /opt/wip/data/secrets/mongo_password

# Vault approach (future)
vault kv put secret/wip/mongodb password="$(openssl rand -hex 32)"
```

### 2. TLS Certificate Management

**Current State:** Self-signed certificates via Caddy.

**Production Options:**

| Option | Pros | Cons |
|--------|------|------|
| Let's Encrypt (Caddy) | Free, automatic renewal | Requires public DNS |
| Organizational CA | Works internal-only | Manual renewal |
| Commercial cert | Maximum compatibility | Cost, manual renewal |

**Let's Encrypt Setup:**

```caddyfile
# In Caddyfile
your-domain.com {
    # Caddy automatically handles Let's Encrypt
    tls admin@your-domain.com
    # ... rest of config
}
```

**Internal CA Setup:**

```bash
# Generate CA (once)
openssl genrsa -out ca.key 4096
openssl req -new -x509 -days 3650 -key ca.key -out ca.crt

# Generate server cert
openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out server.crt -days 365
```

### 3. Authentication: Enterprise OIDC

**Current State:** Dex with static users (YAML configuration).

**Production Options:**

| Provider | RAM | Features | Best For |
|----------|-----|----------|----------|
| Dex (current) | ~30MB | Static users, upstream IdP | Development, small teams |
| Authentik | ~1.2GB | Full user management, SCIM | Enterprise, user self-service |
| Keycloak | ~500MB | Full IAM, federation | Enterprise, complex policies |
| Existing IdP | 0 | Use your org's IdP | Integration scenarios |

**Authentik Integration (Future):**

```yaml
# docker-compose.authentik.yml
services:
  authentik-server:
    image: ghcr.io/goauthentik/server:2024.2
    environment:
      AUTHENTIK_SECRET_KEY: ${AUTHENTIK_SECRET_KEY}
      AUTHENTIK_POSTGRESQL__HOST: wip-postgres
      AUTHENTIK_POSTGRESQL__USER: authentik
      AUTHENTIK_POSTGRESQL__PASSWORD: ${AUTHENTIK_DB_PASSWORD}
    networks:
      - wip-internal

  authentik-worker:
    image: ghcr.io/goauthentik/server:2024.2
    command: worker
    # ... same environment
```

### 4. Monitoring Stack

**Current State:** Per-service health endpoints, reporting-sync metrics.

**Production Stack:**

```
┌─────────────────────────────────────────────────────────────┐
│                     Monitoring Architecture                  │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Services → Prometheus → Grafana → Dashboards + Alerts      │
│     │                                                        │
│     └──────→ Loki ──────→ Log Queries                       │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**Implementation (Future):**

```yaml
# docker-compose.monitoring.yml
services:
  prometheus:
    image: prom/prometheus:v2.49.0
    volumes:
      - ./config/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml
      - ${WIP_DATA_DIR}/prometheus:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
      - '--storage.tsdb.retention.time=30d'
    networks:
      - wip-internal

  grafana:
    image: grafana/grafana:10.3.0
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD}
      GF_INSTALL_PLUGINS: grafana-clock-panel
    volumes:
      - ${WIP_DATA_DIR}/grafana:/var/lib/grafana
      - ./config/grafana/dashboards:/etc/grafana/provisioning/dashboards
      - ./config/grafana/datasources:/etc/grafana/provisioning/datasources
    networks:
      - wip-internal

  loki:
    image: grafana/loki:2.9.0
    command: -config.file=/etc/loki/loki-config.yaml
    volumes:
      - ./config/loki/loki-config.yaml:/etc/loki/loki-config.yaml
      - ${WIP_DATA_DIR}/loki:/loki
    networks:
      - wip-internal

  promtail:
    image: grafana/promtail:2.9.0
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./config/promtail/promtail-config.yaml:/etc/promtail/promtail-config.yaml
    command: -config.file=/etc/promtail/promtail-config.yaml
    networks:
      - wip-internal
```

**Prometheus Configuration:**

```yaml
# config/prometheus/prometheus.yml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'registry'
    static_configs:
      - targets: ['wip-registry:8001']
    metrics_path: '/metrics'

  - job_name: 'def-store'
    static_configs:
      - targets: ['wip-def-store:8002']

  - job_name: 'template-store'
    static_configs:
      - targets: ['wip-template-store:8003']

  - job_name: 'document-store'
    static_configs:
      - targets: ['wip-document-store:8004']

  - job_name: 'reporting-sync'
    static_configs:
      - targets: ['wip-reporting-sync:8005']
```

**Service Changes Required:**

Each FastAPI service needs Prometheus instrumentation:

```python
# Add to each service's main.py
from prometheus_fastapi_instrumentator import Instrumentator

app = FastAPI()
Instrumentator().instrument(app).expose(app, endpoint="/metrics")
```

### 5. Backup and Disaster Recovery

**Current State:** No documented backup procedures.

**Critical Data to Backup:**

| Component | Data Location | Backup Method |
|-----------|---------------|---------------|
| MongoDB | `${WIP_DATA_DIR}/mongodb` | mongodump |
| PostgreSQL | `${WIP_DATA_DIR}/postgres` | pg_dump with PITR |
| MinIO | `${WIP_DATA_DIR}/minio` | mc mirror or rsync |
| NATS | `${WIP_DATA_DIR}/nats` | File copy (when stopped) |
| Dex | `${WIP_DATA_DIR}/dex` | File copy |
| Secrets | `${WIP_DATA_DIR}/secrets` | Secure file copy |

**Backup Scripts (Future):**

```bash
#!/bin/bash
# scripts/backup/backup-all.sh

set -euo pipefail

DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="${WIP_BACKUP_DIR:-/backups}/$DATE"
mkdir -p "$BACKUP_DIR"

echo "Starting backup to $BACKUP_DIR"

# MongoDB
echo "Backing up MongoDB..."
podman exec wip-mongodb mongodump \
  --username "$MONGO_ROOT_USER" \
  --password "$(cat ${WIP_DATA_DIR}/secrets/mongo_password)" \
  --authenticationDatabase admin \
  --out /tmp/mongodump
podman cp wip-mongodb:/tmp/mongodump "$BACKUP_DIR/mongodb"
podman exec wip-mongodb rm -rf /tmp/mongodump

# PostgreSQL
echo "Backing up PostgreSQL..."
podman exec wip-postgres pg_dumpall \
  -U "$POSTGRES_USER" \
  > "$BACKUP_DIR/postgres/wip_reporting.sql"

# MinIO
echo "Backing up MinIO..."
podman exec wip-minio mc mirror local/wip-attachments /tmp/minio-backup
podman cp wip-minio:/tmp/minio-backup "$BACKUP_DIR/minio"
podman exec wip-minio rm -rf /tmp/minio-backup

# Secrets (encrypted)
echo "Backing up secrets..."
tar -czf - -C "${WIP_DATA_DIR}/secrets" . | \
  openssl enc -aes-256-cbc -salt -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY \
  > "$BACKUP_DIR/secrets.tar.gz.enc"

# Dex database
echo "Backing up Dex..."
cp "${WIP_DATA_DIR}/dex/dex.db" "$BACKUP_DIR/dex.db"

# Create manifest
echo "Creating backup manifest..."
cat > "$BACKUP_DIR/manifest.json" << EOF
{
  "timestamp": "$DATE",
  "hostname": "$(hostname)",
  "wip_version": "$(cat VERSION 2>/dev/null || echo 'unknown')",
  "components": ["mongodb", "postgres", "minio", "secrets", "dex"]
}
EOF

echo "Backup completed: $BACKUP_DIR"

# Optional: Upload to S3
if [ -n "${S3_BACKUP_BUCKET:-}" ]; then
  echo "Uploading to S3..."
  aws s3 sync "$BACKUP_DIR" "s3://$S3_BACKUP_BUCKET/$DATE/"
fi
```

**Restore Script:**

```bash
#!/bin/bash
# scripts/restore/restore-all.sh

set -euo pipefail

BACKUP_DIR="${1:?Usage: restore-all.sh /path/to/backup}"

echo "Restoring from $BACKUP_DIR"

# Stop services first
echo "Stopping services..."
./scripts/stop-all.sh

# MongoDB
echo "Restoring MongoDB..."
podman cp "$BACKUP_DIR/mongodb" wip-mongodb:/tmp/mongorestore
podman exec wip-mongodb mongorestore \
  --username "$MONGO_ROOT_USER" \
  --password "$(cat ${WIP_DATA_DIR}/secrets/mongo_password)" \
  --authenticationDatabase admin \
  --drop \
  /tmp/mongorestore
podman exec wip-mongodb rm -rf /tmp/mongorestore

# PostgreSQL
echo "Restoring PostgreSQL..."
podman exec -i wip-postgres psql -U "$POSTGRES_USER" -d postgres \
  < "$BACKUP_DIR/postgres/wip_reporting.sql"

# MinIO
echo "Restoring MinIO..."
podman cp "$BACKUP_DIR/minio" wip-minio:/tmp/minio-restore
podman exec wip-minio mc mirror /tmp/minio-restore local/wip-attachments
podman exec wip-minio rm -rf /tmp/minio-restore

# Dex
echo "Restoring Dex..."
cp "$BACKUP_DIR/dex.db" "${WIP_DATA_DIR}/dex/dex.db"

# Start services
echo "Starting services..."
./scripts/start-all.sh

echo "Restore completed"
```

**Backup Schedule (cron):**

```cron
# /etc/cron.d/wip-backup
# Daily full backup at 2 AM
0 2 * * * root /opt/wip/scripts/backup/backup-all.sh >> /var/log/wip-backup.log 2>&1

# Weekly backup verification
0 4 * * 0 root /opt/wip/scripts/backup/verify-backup.sh >> /var/log/wip-backup.log 2>&1
```

### 6. High Availability

**Current State:** Single instance of all services.

**MongoDB Replica Set (Future):**

```yaml
# docker-compose.mongodb-ha.yml
services:
  mongodb-primary:
    image: mongo:7
    command: ["--replSet", "rs0", "--bind_ip_all", "--keyFile", "/etc/mongo-keyfile"]
    volumes:
      - ${WIP_DATA_DIR}/mongodb-primary:/data/db
      - ${WIP_DATA_DIR}/secrets/mongo-keyfile:/etc/mongo-keyfile:ro

  mongodb-secondary-1:
    image: mongo:7
    command: ["--replSet", "rs0", "--bind_ip_all", "--keyFile", "/etc/mongo-keyfile"]
    volumes:
      - ${WIP_DATA_DIR}/mongodb-secondary-1:/data/db
      - ${WIP_DATA_DIR}/secrets/mongo-keyfile:/etc/mongo-keyfile:ro

  mongodb-secondary-2:
    image: mongo:7
    command: ["--replSet", "rs0", "--bind_ip_all", "--keyFile", "/etc/mongo-keyfile"]
    volumes:
      - ${WIP_DATA_DIR}/mongodb-secondary-2:/data/db
      - ${WIP_DATA_DIR}/secrets/mongo-keyfile:/etc/mongo-keyfile:ro
```

**PostgreSQL Replication (Future):**

Consider using Patroni for PostgreSQL HA:

```yaml
services:
  postgres-primary:
    image: docker.io/bitnami/postgresql-repmgr:16
    environment:
      POSTGRESQL_POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRESQL_USERNAME: wip
      POSTGRESQL_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRESQL_DATABASE: wip_reporting
      REPMGR_PASSWORD: ${REPMGR_PASSWORD}
      REPMGR_PRIMARY_HOST: postgres-primary
      REPMGR_NODE_NAME: postgres-primary
      REPMGR_NODE_NETWORK_NAME: postgres-primary
```

### 7. Kubernetes Deployment

**Current State:** Not implemented.

**Future Helm Chart Structure:**

```
deploy/kubernetes/helm/wip/
├── Chart.yaml
├── values.yaml
├── values-production.yaml
├── templates/
│   ├── _helpers.tpl
│   ├── configmap.yaml
│   ├── secrets.yaml
│   ├── mongodb-statefulset.yaml
│   ├── postgres-statefulset.yaml
│   ├── nats-statefulset.yaml
│   ├── minio-statefulset.yaml
│   ├── services-deployment.yaml
│   ├── console-deployment.yaml
│   ├── ingress.yaml
│   └── servicemonitor.yaml
```

**Kustomize Alternative:**

```
deploy/kubernetes/kustomize/
├── base/
│   ├── kustomization.yaml
│   ├── mongodb/
│   ├── postgres/
│   ├── nats/
│   ├── services/
│   └── console/
└── overlays/
    ├── development/
    │   └── kustomization.yaml
    └── production/
        ├── kustomization.yaml
        ├── replicas-patch.yaml
        └── resources-patch.yaml
```

---

## Security Checklist

Before going to production, verify:

- [ ] All default passwords changed
- [ ] Secrets stored in files, not environment variables
- [ ] Secret files have restrictive permissions (600)
- [ ] TLS certificates configured (Let's Encrypt or organizational CA)
- [ ] Only Caddy exposes external ports (80, 443)
- [ ] Firewall configured to block direct service access
- [ ] API keys are unique per service/user
- [ ] Dex configured with real identity provider (not static passwords)
- [ ] Backup procedures tested
- [ ] Restore procedures tested
- [ ] Monitoring alerts configured
- [ ] Log aggregation configured
- [ ] Rate limiting enabled on Caddy
- [ ] Security headers configured

---

## Recommended Implementation Order

| Priority | Task | Effort | Impact |
|----------|------|--------|--------|
| 1 | Configure production secrets | 1 hour | Critical |
| 2 | Set up Let's Encrypt TLS | 1 hour | Critical |
| 3 | Implement backup scripts | 1 day | Critical |
| 4 | Test restore procedures | 1 day | Critical |
| 5 | Add Prometheus metrics to services | 1-2 days | High |
| 6 | Deploy Prometheus/Grafana | 1 day | High |
| 7 | Configure alerting | 1 day | High |
| 8 | Set up log aggregation (Loki) | 1 day | Medium |
| 9 | Replace Dex with Authentik/Keycloak | 2-3 days | Medium |
| 10 | Implement MongoDB replica set | 2-3 days | Medium |
| 11 | Create Kubernetes Helm charts | 1-2 weeks | Low |

---

## Support

For production deployment assistance:
- File issues at: https://github.com/your-org/World-In-A-Pie/issues
- Tag with `deployment` or `production` labels
