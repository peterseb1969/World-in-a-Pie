# Deployment Guide

This document covers deployment configurations for World In a Pie (WIP) across different environments.

---

## Deployment Profiles

| Profile | Target | Document Store | Reporting | Auth | RAM |
|---------|--------|----------------|-----------|------|-----|
| **Minimal** | Pi Zero/3 | SQLite | SQLite | Authelia | ~512MB |
| **Standard** | Pi 4/5 | MongoDB | PostgreSQL | Authentik | ~2GB |
| **Production** | Cloud/Server | MongoDB | PostgreSQL | Authentik | ~4GB+ |

---

## Docker Compose (Development / Raspberry Pi)

### Directory Structure

```
wip/
├── docker-compose.yml
├── docker-compose.override.yml      # Local development overrides
├── docker-compose.pi.yml            # Pi-specific configuration
├── .env                             # Environment variables
├── config/
│   ├── api/
│   │   └── config.yaml
│   ├── authentik/
│   │   └── ...
│   └── traefik/
│       └── traefik.yml
└── data/                            # Persistent volumes
    ├── mongodb/
    ├── postgres/
    └── authentik/
```

### Base Configuration

```yaml
# docker-compose.yml
version: "3.8"

services:
  # ===================
  # REVERSE PROXY
  # ===================
  traefik:
    image: traefik:v3.0
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./config/traefik:/etc/traefik
      - traefik-certs:/letsencrypt
    labels:
      - "traefik.enable=true"
    restart: unless-stopped

  # ===================
  # FRONTEND
  # ===================
  ui:
    image: wip/ui:latest
    build:
      context: ./frontend
      dockerfile: Dockerfile
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.ui.rule=PathPrefix(`/`)"
      - "traefik.http.services.ui.loadbalancer.server.port=80"
    depends_on:
      - api
    restart: unless-stopped

  # ===================
  # BACKEND API
  # ===================
  api:
    image: wip/api:latest
    build:
      context: ./backend
      dockerfile: Dockerfile
    environment:
      - WIP_CONFIG=/app/config/config.yaml
      - WIP_MONGODB_URI=mongodb://mongodb:27017/wip
      - WIP_POSTGRES_URI=postgresql://wip:${POSTGRES_PASSWORD}@postgres:5432/wip_reporting
      - WIP_NATS_URI=nats://nats:4222
      - WIP_AUTH_PROVIDER=authentik
      - WIP_AUTH_URL=http://authentik:9000
    volumes:
      - ./config/api:/app/config
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.api.rule=PathPrefix(`/api`)"
      - "traefik.http.services.api.loadbalancer.server.port=8000"
    depends_on:
      - mongodb
      - postgres
      - nats
    restart: unless-stopped

  # ===================
  # REGISTRY (Optional standalone)
  # ===================
  registry:
    image: wip/registry:latest
    build:
      context: ./backend
      dockerfile: Dockerfile.registry
    environment:
      - WIP_REGISTRY_MONGODB_URI=mongodb://mongodb:27017/wip_registry
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.registry.rule=PathPrefix(`/registry`)"
      - "traefik.http.services.registry.loadbalancer.server.port=8001"
    depends_on:
      - mongodb
    restart: unless-stopped

  # ===================
  # AUTHENTICATION
  # ===================
  authentik:
    image: ghcr.io/goauthentik/server:2024.2
    command: server
    environment:
      - AUTHENTIK_SECRET_KEY=${AUTHENTIK_SECRET_KEY}
      - AUTHENTIK_REDIS__HOST=redis
      - AUTHENTIK_POSTGRESQL__HOST=postgres
      - AUTHENTIK_POSTGRESQL__USER=authentik
      - AUTHENTIK_POSTGRESQL__PASSWORD=${AUTHENTIK_POSTGRES_PASSWORD}
      - AUTHENTIK_POSTGRESQL__NAME=authentik
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.authentik.rule=PathPrefix(`/auth`)"
      - "traefik.http.services.authentik.loadbalancer.server.port=9000"
    depends_on:
      - postgres
      - redis
    restart: unless-stopped

  authentik-worker:
    image: ghcr.io/goauthentik/server:2024.2
    command: worker
    environment:
      - AUTHENTIK_SECRET_KEY=${AUTHENTIK_SECRET_KEY}
      - AUTHENTIK_REDIS__HOST=redis
      - AUTHENTIK_POSTGRESQL__HOST=postgres
      - AUTHENTIK_POSTGRESQL__USER=authentik
      - AUTHENTIK_POSTGRESQL__PASSWORD=${AUTHENTIK_POSTGRES_PASSWORD}
      - AUTHENTIK_POSTGRESQL__NAME=authentik
    depends_on:
      - postgres
      - redis
    restart: unless-stopped

  # ===================
  # DATA STORES
  # ===================
  mongodb:
    image: mongo:7
    volumes:
      - mongodb-data:/data/db
    restart: unless-stopped

  postgres:
    image: postgres:16
    environment:
      - POSTGRES_USER=wip
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
      - POSTGRES_DB=wip_reporting
    volumes:
      - postgres-data:/var/lib/postgresql/data
      - ./config/postgres/init.sql:/docker-entrypoint-initdb.d/init.sql
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    volumes:
      - redis-data:/data
    restart: unless-stopped

  # ===================
  # MESSAGE QUEUE
  # ===================
  nats:
    image: nats:2.10
    command: ["--jetstream", "--store_dir=/data"]
    volumes:
      - nats-data:/data
    restart: unless-stopped

volumes:
  traefik-certs:
  mongodb-data:
  postgres-data:
  redis-data:
  nats-data:
```

### Raspberry Pi Override

```yaml
# docker-compose.pi.yml
version: "3.8"

services:
  # Use ARM-optimized images where available
  mongodb:
    image: mongo:7
    # MongoDB 7 has official ARM64 support
    deploy:
      resources:
        limits:
          memory: 512M

  postgres:
    image: postgres:16
    deploy:
      resources:
        limits:
          memory: 256M

  api:
    deploy:
      resources:
        limits:
          memory: 256M

  authentik:
    deploy:
      resources:
        limits:
          memory: 512M

  # Reduce replica counts
  ui:
    deploy:
      replicas: 1
```

### Minimal Pi Configuration (SQLite)

```yaml
# docker-compose.minimal.yml
version: "3.8"

services:
  traefik:
    image: traefik:v3.0
    ports:
      - "80:80"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./config/traefik:/etc/traefik
    restart: unless-stopped

  ui:
    image: wip/ui:latest
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.ui.rule=PathPrefix(`/`)"
    restart: unless-stopped

  api:
    image: wip/api:latest
    environment:
      - WIP_DOCUMENT_STORE=sqlite
      - WIP_DOCUMENT_STORE_PATH=/data/wip.db
      - WIP_REPORTING_STORE=sqlite
      - WIP_REPORTING_STORE_PATH=/data/wip_reporting.db
      - WIP_AUTH_PROVIDER=authelia
    volumes:
      - wip-data:/data
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.api.rule=PathPrefix(`/api`)"
    restart: unless-stopped

  authelia:
    image: authelia/authelia:latest
    volumes:
      - ./config/authelia:/config
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.authelia.rule=PathPrefix(`/auth`)"
    restart: unless-stopped

volumes:
  wip-data:
```

### Environment File

```bash
# .env

# General
WIP_ENVIRONMENT=production
WIP_LOG_LEVEL=info

# Secrets (generate with: openssl rand -hex 32)
POSTGRES_PASSWORD=change_me_in_production
AUTHENTIK_SECRET_KEY=change_me_in_production
AUTHENTIK_POSTGRES_PASSWORD=change_me_in_production

# Optional: External access
WIP_DOMAIN=wip.example.com
LETSENCRYPT_EMAIL=admin@example.com
```

---

## MicroK8s Deployment

### Prerequisites

```bash
# Install MicroK8s
sudo snap install microk8s --classic

# Enable required add-ons
microk8s enable dns ingress storage registry

# Alias kubectl
alias kubectl='microk8s kubectl'
```

### Namespace and ConfigMap

```yaml
# k8s/namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: wip
---
# k8s/configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: wip-config
  namespace: wip
data:
  config.yaml: |
    storage:
      document_store:
        type: mongodb
        uri: mongodb://mongodb:27017/wip
      reporting_store:
        type: postgresql
        uri: postgresql://wip:$(POSTGRES_PASSWORD)@postgres:5432/wip_reporting

    messaging:
      type: nats
      uri: nats://nats:4222

    auth:
      provider: authentik
      url: http://authentik:9000
```

### Secrets

```yaml
# k8s/secrets.yaml
apiVersion: v1
kind: Secret
metadata:
  name: wip-secrets
  namespace: wip
type: Opaque
stringData:
  postgres-password: "change_me"
  authentik-secret-key: "change_me"
  api-secret-key: "change_me"
```

### MongoDB Deployment

```yaml
# k8s/mongodb.yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: mongodb
  namespace: wip
spec:
  serviceName: mongodb
  replicas: 1
  selector:
    matchLabels:
      app: mongodb
  template:
    metadata:
      labels:
        app: mongodb
    spec:
      containers:
        - name: mongodb
          image: mongo:7
          ports:
            - containerPort: 27017
          volumeMounts:
            - name: data
              mountPath: /data/db
          resources:
            requests:
              memory: "256Mi"
              cpu: "100m"
            limits:
              memory: "512Mi"
              cpu: "500m"
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes: ["ReadWriteOnce"]
        resources:
          requests:
            storage: 10Gi
---
apiVersion: v1
kind: Service
metadata:
  name: mongodb
  namespace: wip
spec:
  ports:
    - port: 27017
  selector:
    app: mongodb
```

### PostgreSQL Deployment

```yaml
# k8s/postgres.yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgres
  namespace: wip
spec:
  serviceName: postgres
  replicas: 1
  selector:
    matchLabels:
      app: postgres
  template:
    metadata:
      labels:
        app: postgres
    spec:
      containers:
        - name: postgres
          image: postgres:16
          ports:
            - containerPort: 5432
          env:
            - name: POSTGRES_USER
              value: wip
            - name: POSTGRES_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: wip-secrets
                  key: postgres-password
            - name: POSTGRES_DB
              value: wip_reporting
          volumeMounts:
            - name: data
              mountPath: /var/lib/postgresql/data
          resources:
            requests:
              memory: "128Mi"
              cpu: "100m"
            limits:
              memory: "256Mi"
              cpu: "500m"
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes: ["ReadWriteOnce"]
        resources:
          requests:
            storage: 10Gi
---
apiVersion: v1
kind: Service
metadata:
  name: postgres
  namespace: wip
spec:
  ports:
    - port: 5432
  selector:
    app: postgres
```

### API Deployment

```yaml
# k8s/api.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: wip-api
  namespace: wip
spec:
  replicas: 2
  selector:
    matchLabels:
      app: wip-api
  template:
    metadata:
      labels:
        app: wip-api
    spec:
      containers:
        - name: api
          image: localhost:32000/wip/api:latest
          ports:
            - containerPort: 8000
          env:
            - name: WIP_CONFIG
              value: /app/config/config.yaml
            - name: POSTGRES_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: wip-secrets
                  key: postgres-password
          volumeMounts:
            - name: config
              mountPath: /app/config
          resources:
            requests:
              memory: "128Mi"
              cpu: "100m"
            limits:
              memory: "256Mi"
              cpu: "500m"
          livenessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 10
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /ready
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 10
      volumes:
        - name: config
          configMap:
            name: wip-config
---
apiVersion: v1
kind: Service
metadata:
  name: wip-api
  namespace: wip
spec:
  ports:
    - port: 8000
  selector:
    app: wip-api
```

### UI Deployment

```yaml
# k8s/ui.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: wip-ui
  namespace: wip
spec:
  replicas: 2
  selector:
    matchLabels:
      app: wip-ui
  template:
    metadata:
      labels:
        app: wip-ui
    spec:
      containers:
        - name: ui
          image: localhost:32000/wip/ui:latest
          ports:
            - containerPort: 80
          resources:
            requests:
              memory: "64Mi"
              cpu: "50m"
            limits:
              memory: "128Mi"
              cpu: "200m"
---
apiVersion: v1
kind: Service
metadata:
  name: wip-ui
  namespace: wip
spec:
  ports:
    - port: 80
  selector:
    app: wip-ui
```

### Ingress

```yaml
# k8s/ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: wip-ingress
  namespace: wip
  annotations:
    nginx.ingress.kubernetes.io/rewrite-target: /$2
spec:
  rules:
    - host: wip.local
      http:
        paths:
          - path: /api(/|$)(.*)
            pathType: Prefix
            backend:
              service:
                name: wip-api
                port:
                  number: 8000
          - path: /auth(/|$)(.*)
            pathType: Prefix
            backend:
              service:
                name: authentik
                port:
                  number: 9000
          - path: /
            pathType: Prefix
            backend:
              service:
                name: wip-ui
                port:
                  number: 80
```

### Deploy Script

```bash
#!/bin/bash
# deploy.sh

set -e

echo "Deploying WIP to MicroK8s..."

# Build and push images
docker build -t localhost:32000/wip/api:latest ./backend
docker build -t localhost:32000/wip/ui:latest ./frontend
docker push localhost:32000/wip/api:latest
docker push localhost:32000/wip/ui:latest

# Apply configurations
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secrets.yaml
kubectl apply -f k8s/configmap.yaml

# Deploy data stores
kubectl apply -f k8s/mongodb.yaml
kubectl apply -f k8s/postgres.yaml
kubectl apply -f k8s/nats.yaml

# Wait for data stores
kubectl wait --for=condition=ready pod -l app=mongodb -n wip --timeout=120s
kubectl wait --for=condition=ready pod -l app=postgres -n wip --timeout=120s

# Deploy application
kubectl apply -f k8s/api.yaml
kubectl apply -f k8s/ui.yaml
kubectl apply -f k8s/authentik.yaml

# Deploy ingress
kubectl apply -f k8s/ingress.yaml

echo "Deployment complete!"
kubectl get pods -n wip
```

---

## Configuration Reference

### API Configuration

```yaml
# config.yaml

# Application settings
app:
  name: "World In a Pie"
  environment: production  # development, staging, production
  debug: false
  log_level: info  # debug, info, warning, error

# Storage configuration
storage:
  document_store:
    type: mongodb  # mongodb, postgresql, sqlite
    uri: mongodb://localhost:27017/wip
    # For SQLite:
    # type: sqlite
    # path: /data/wip.db

  reporting_store:
    type: postgresql  # postgresql, mysql, sqlite, none
    uri: postgresql://user:pass@localhost:5432/wip_reporting
    sync:
      mode: event  # batch, event, queue
      batch:
        schedule: "0 */6 * * *"
      event:
        debounce_ms: 1000
      queue:
        subject: "wip.documents.>"

# Messaging
messaging:
  type: nats  # nats, redis, none
  uri: nats://localhost:4222
  jetstream:
    enabled: true
    stream_name: WIP_EVENTS

# Authentication
auth:
  provider: authentik  # authentik, authelia, none
  authentik:
    url: http://localhost:9000
    client_id: wip-api
    client_secret: ${AUTHENTIK_CLIENT_SECRET}
  api_keys:
    enabled: true
    header: X-API-Key

# Versioning
versioning:
  policy: deactivate  # deactivate (never delete)
  archive:
    enabled: false
    policies:
      - type: age
        max_age_days: 730  # 2 years
      - type: volume
        max_bytes: 107374182400  # 100GB
      - type: template
        template_id: audit-log
        max_age_days: 365

# Registry (if running as standalone)
registry:
  enabled: true
  id_generator: uuid4  # uuid4, uuid7, nanoid, custom
```

---

## Backup and Recovery

### MongoDB Backup

```bash
#!/bin/bash
# backup-mongodb.sh

BACKUP_DIR=/backups/mongodb
DATE=$(date +%Y%m%d_%H%M%S)

# Dump database
mongodump --uri="mongodb://localhost:27017/wip" --out="$BACKUP_DIR/$DATE"

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
# backup-postgres.sh

BACKUP_DIR=/backups/postgres
DATE=$(date +%Y%m%d_%H%M%S)

# Dump database
pg_dump -h localhost -U wip wip_reporting | gzip > "$BACKUP_DIR/wip_reporting_$DATE.sql.gz"

# Retain last 30 days
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +30 -delete

echo "Backup complete: $BACKUP_DIR/wip_reporting_$DATE.sql.gz"
```

### Recovery

```bash
# MongoDB restore
tar -xzf wip_20240115_120000.tar.gz
mongorestore --uri="mongodb://localhost:27017" 20240115_120000/wip

# PostgreSQL restore
gunzip -c wip_reporting_20240115_120000.sql.gz | psql -h localhost -U wip wip_reporting
```

---

## Monitoring

### Health Endpoints

```
GET /health    - Basic liveness check
GET /ready     - Readiness check (includes dependencies)
GET /metrics   - Prometheus metrics
```

### Prometheus Configuration

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'wip-api'
    static_configs:
      - targets: ['wip-api:8000']
    metrics_path: /metrics
```

### Key Metrics

| Metric | Description |
|--------|-------------|
| `wip_documents_total` | Total documents by template |
| `wip_validations_total` | Validation attempts (success/failure) |
| `wip_request_duration_seconds` | API request latency |
| `wip_sync_lag_seconds` | Reporting sync delay |

---

## Troubleshooting

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| API won't start | MongoDB not ready | Check MongoDB logs; wait for startup |
| Auth failures | Authentik misconfigured | Verify client_id/secret; check Authentik logs |
| Slow queries | Missing indexes | Run index creation script |
| Sync lag | Queue backlog | Check NATS; scale sync workers |
| Out of memory | Resource limits too low | Increase container memory limits |

### Debug Mode

```yaml
# config.yaml
app:
  debug: true
  log_level: debug
```

### Log Access

```bash
# Docker Compose
docker-compose logs -f api

# Kubernetes
kubectl logs -f deployment/wip-api -n wip
```
