# WIP Kubernetes Deployment

Deploy World In a Pie (WIP) on any Kubernetes cluster. This replaces the podman-compose setup with native K8s resources, using NGINX Ingress instead of Caddy for TLS termination and routing.

## Prerequisites

- Kubernetes cluster (v1.25+) — minikube, k3s, EKS, GKE, etc.
- `kubectl` configured for your cluster
- [NGINX Ingress Controller](https://kubernetes.github.io/ingress-nginx/deploy/) installed
- Docker or Podman for building images

## Quick Start

```bash
# 1. Build images
./build-images.sh

# 2. Configure
#    Edit secrets.yaml — replace all CHANGE_ME values
#    Edit configmaps.yaml — replace WIP_HOSTNAME with your hostname
#    Edit ingress/ingress.yaml — replace WIP_HOSTNAME with your hostname

# 3. Deploy
kubectl apply -k .

# 4. Initialize namespaces (one-time, after registry pod is ready)
kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=registry -n wip --timeout=120s
kubectl exec -n wip deploy/wip-registry -- \
  curl -s -X POST http://localhost:8001/api/registry/namespaces/initialize-wip \
    -H "X-API-Key: YOUR_API_KEY"
```

## Step-by-Step Guide

### 1. Build Container Images

The podman-compose setup mounts `libs/wip-auth` as a volume at runtime. For K8s, images must be self-contained. The build script handles this by creating a temporary build context that includes wip-auth.

```bash
cd k8s/

# Build all images locally
./build-images.sh

# Build and push to a registry
./build-images.sh --registry ghcr.io/myorg --push

# Use podman instead of docker
./build-images.sh --builder podman

# Build a single service
./build-images.sh --service registry
```

This produces images:

| Image | Source |
|-------|--------|
| `wip/registry:latest` | components/registry + wip-auth |
| `wip/def-store:latest` | components/def-store + wip-auth |
| `wip/template-store:latest` | components/template-store + wip-auth |
| `wip/document-store:latest` | components/document-store + wip-auth |
| `wip/reporting-sync:latest` | components/reporting-sync + wip-auth |
| `wip/ingest-gateway:latest` | components/ingest-gateway |
| `wip/console:latest` | ui/wip-console (nginx + built Vue app) |

If using a remote registry, update the image references in each service manifest or use a Kustomize image override:

```bash
# Example: override images via kustomize command line
kubectl kustomize . | sed 's|wip/|ghcr.io/myorg/wip-|g' | kubectl apply -f -
```

### 2. Configure Hostname

Replace `WIP_HOSTNAME` in these files with your actual hostname (e.g., `wip.example.com`):

| File | What to change |
|------|---------------|
| `configmaps.yaml` | `WIP_AUTH_JWT_ISSUER_URL` |
| `configmaps.yaml` | Dex `issuer` and `redirectURIs` in `wip-dex-config` |
| `ingress/ingress.yaml` | `host` and TLS `hosts` |

**Critical:** The Dex issuer URL, `WIP_AUTH_JWT_ISSUER_URL`, and the Ingress hostname must all agree. A mismatch causes `401 Invalid token issuer` errors. See the main project's `docs/network-configuration.md`.

### 3. Configure Secrets

Edit `secrets.yaml` and replace all `CHANGE_ME` placeholder values:

```yaml
stringData:
  api-key: "your-secure-api-key"
  dex-client-secret: "your-dex-client-secret"
  mongo-uri: "mongodb://wip-mongodb:27017/"    # or with auth
  postgres-password: "your-pg-password"
  minio-root-user: "your-minio-user"
  minio-root-password: "your-minio-password"
```

For production, consider using an external secret manager (e.g., Sealed Secrets, External Secrets Operator, or Vault) instead of plain `secrets.yaml`.

### 4. TLS Certificate

The Ingress expects a TLS secret named `wip-tls`. Options:

**cert-manager (recommended):**
```yaml
# Add to ingress/ingress.yaml annotations:
cert-manager.io/cluster-issuer: letsencrypt-prod
```

**Self-signed (development):**
```bash
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout tls.key -out tls.crt \
  -subj "/CN=WIP_HOSTNAME"

kubectl create secret tls wip-tls \
  --cert=tls.crt --key=tls.key -n wip
```

### 5. Deploy

```bash
# Apply everything at once via Kustomize
kubectl apply -k .

# Watch pods come up
kubectl get pods -n wip -w
```

### 6. Verify

```bash
# All pods should be Running
kubectl get pods -n wip

# Check a service health endpoint
kubectl exec -n wip deploy/wip-registry -- curl -s http://localhost:8001/health

# Check via Ingress (from outside the cluster)
curl -k https://WIP_HOSTNAME/api/registry/health

# View logs for a service
kubectl logs -n wip deploy/wip-registry -f
```

### 7. Initialize Namespaces

Run once after first deployment:

```bash
kubectl exec -n wip deploy/wip-registry -- \
  curl -s -X POST http://localhost:8001/api/registry/namespaces/initialize-wip \
    -H "X-API-Key: YOUR_API_KEY"
```

## Architecture

```
                    ┌──────────────────────────────────┐
                    │      NGINX Ingress Controller     │
                    │      (TLS termination + routing)  │
                    └────────────┬─────────────────────┘
                                 │
         ┌──────────┬────────────┼────────────┬──────────────┐
         │          │            │            │              │
    /dex/*    /api/registry  /api/def-store  /api/...    /* (default)
         │          │            │            │              │
    ┌────┴──┐ ┌─────┴───┐ ┌─────┴───┐  ┌────┴──┐    ┌──────┴───┐
    │  Dex  │ │Registry │ │Def-Store│  │ ...   │    │ Console  │
    │ :5556 │ │  :8001  │ │  :8002  │  │       │    │   :80    │
    └───────┘ └────┬────┘ └────┬────┘  └───┬───┘    └──────────┘
                   │           │           │
              ┌────┴───────────┴───────────┴────┐
              │         Internal Services        │
              ├─────────────┬───────────────────┤
              │  MongoDB    │  NATS   PostgreSQL │
              │  MinIO      │                    │
              └─────────────┴───────────────────┘
```

## Resource Summary

| Component | Kind | Replicas | Storage |
|-----------|------|----------|---------|
| MongoDB | StatefulSet | 1 | 10Gi PVC |
| NATS | StatefulSet | 1 | 2Gi PVC |
| PostgreSQL | StatefulSet | 1 | 5Gi PVC |
| MinIO | StatefulSet | 1 | 20Gi PVC |
| Dex | Deployment | 1 | 1Gi PVC |
| Registry | Deployment | 1 | — |
| Def-Store | Deployment | 1 | — |
| Template-Store | Deployment | 1 | — |
| Document-Store | Deployment | 1 | — |
| Reporting-Sync | Deployment | 1 | — |
| Ingest-Gateway | Deployment | 1 | — |
| Console | Deployment | 1 | — |

## Differences from podman-compose

| Aspect | podman-compose | Kubernetes |
|--------|---------------|------------|
| TLS / Routing | Caddy reverse proxy | NGINX Ingress Controller |
| Service discovery | Docker network DNS | K8s Service DNS (`svc.cluster.local`) |
| Secrets | `.env` file | K8s Secret object |
| wip-auth | Volume mount + runtime pip install | Baked into images at build time |
| Console nginx | Proxies API + serves SPA | Serves SPA only (Ingress routes APIs) |
| Scaling | Manual | `kubectl scale` or HPA |

## Test Users (Dex OIDC)

| Email | Password | Group |
|-------|----------|-------|
| admin@wip.local | admin123 | wip-admins |
| editor@wip.local | editor123 | wip-editors |
| viewer@wip.local | viewer123 | wip-viewers |

## Troubleshooting

**Pods stuck in CrashLoopBackOff:**
```bash
kubectl logs -n wip deploy/wip-<service> --previous
```

**401 Invalid token issuer:**
The Dex issuer URL must match exactly across three places. Check:
```bash
# 1. Dex config (ConfigMap)
kubectl get configmap wip-dex-config -n wip -o yaml | grep issuer

# 2. Service config (ConfigMap)
kubectl get configmap wip-config -n wip -o yaml | grep JWT_ISSUER

# 3. What the browser sees (Ingress hostname)
curl -sk https://WIP_HOSTNAME/dex/.well-known/openid-configuration | jq .issuer
```

All three must be identical.

**Service can't connect to MongoDB/NATS:**
```bash
# Check DNS resolution from a service pod
kubectl exec -n wip deploy/wip-registry -- nslookup wip-mongodb

# Check if infra pods are ready
kubectl get pods -n wip -l app.kubernetes.io/name=mongodb
```

**Images not found (ErrImagePull):**
If using a remote registry, ensure image names match. Either push images with `--registry` flag or override in manifests.

## Cleanup

```bash
# Delete all WIP resources (PVCs are retained by default)
kubectl delete -k .

# Also delete persistent data
kubectl delete pvc -n wip --all
kubectl delete namespace wip
```
