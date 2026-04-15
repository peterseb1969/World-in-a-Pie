# K8s Deployment Plan: wip-stable namespace

## Context

Replace the existing `wip` namespace (17 days old, v1.0-era) with `wip-stable` running the current v1.2-rc1 code including gateway auth. The cluster is a 3-node microk8s on kubi5-1/2/3 with nginx ingress and metallb.

## Architecture: Podman vs K8s

The images are identical. Only the "front door" differs:

| Podman | K8s equivalent |
|--------|---------------|
| Caddy (TLS + routing + forward_auth) | nginx ingress (TLS + routing + auth-url) |
| `docker-compose.production.yml` | k8s Deployment/StatefulSet yamls |
| `.env` file | K8s Secret + ConfigMap |
| Compose chunk labels | Ingress resources |
| `setup-wip.sh` generates Caddyfile | Ingress yamls define routes directly |

**The auth gateway is the same container.** nginx calls it via `auth-url` annotation instead of Caddy's `forward_auth`. Same `/auth/verify` endpoint, same headers, same behavior.

## Step 1: Delete old namespace

```bash
ssh kubi5-1 '/snap/bin/microk8s kubectl delete namespace wip'
```

This removes all pods, services, configmaps, secrets, ingresses, PVCs. Clean slate.

**Data loss:** MongoDB, PostgreSQL, MinIO volumes are deleted. Acceptable — this is a demo/test deployment. Data will be restored from backups.

## Step 2: Create wip-stable namespace

Update `k8s/namespace.yaml`:
```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: wip-stable
```

Update `k8s/kustomization.yaml` to reference `wip-stable` namespace.

## Step 3: Update Secrets

`k8s/secrets.yaml` needs:
- `API_KEY` — master service key
- `WIP_POSTGRES_PASSWORD`
- `WIP_FILE_STORAGE_SECRET_KEY` (MinIO)
- **NEW:** `WIP_GATEWAY_SECRET` — Dex client secret for auth gateway
- **NEW:** `WIP_GATEWAY_SESSION_SECRET` — cookie signing key
- Dex user password hashes (admin, editor, viewer)

Generate with: `head -c 32 /dev/urandom | base64 | tr -d '+/='`

## Step 4: Update ConfigMap

`k8s/configmaps.yaml` needs:
- `WIP_HOSTNAME: wip-kubi.local`
- `WIP_AUTH_MODE: dual`
- `WIP_AUTH_TRUST_PROXY_HEADERS: "true"` — enables TrustedHeaderProvider on all services
- `WIP_AUTH_JWT_ISSUER_URL: https://wip-kubi.local/dex`
- `WIP_AUTH_JWT_JWKS_URI: http://wip-dex:5556/dex/keys`
- All inter-service URLs (http://wip-registry:8001, etc.)

## Step 5: Infrastructure StatefulSets (unchanged architecture)

Same as current, just namespace `wip-stable`:
- `mongodb.yaml` — StatefulSet, PVC, Service
- `postgres.yaml` — StatefulSet, PVC, Service  
- `nats.yaml` — StatefulSet, PVC, Service
- `minio.yaml` — StatefulSet, PVC, Service
- `dex.yaml` — Deployment + ConfigMap + Service

**Dex config changes:**
- Add `wip-gateway` static client (for auth gateway)
- Keep `wip-console` client for backward compatibility
- Update issuer to `https://wip-kubi.local/dex`

## Step 6: WIP Service Deployments

Update image tags to GHCR v1.1.0 (core services):
- `registry.yaml` — `ghcr.io/peterseb1969/registry:v1.1.0`
- `def-store.yaml` — same tag
- `template-store.yaml`
- `document-store.yaml`
- `reporting-sync.yaml`
- `ingest-gateway.yaml`
- `mcp-server.yaml` — needs `MCP_PORT: "8007"`, `MCP_ALLOWED_HOST: wip-mcp-server`

**Remove:** `wip-console.yaml` (Vue Console — replaced by React Console app)

**Add:** `auth-gateway.yaml` — new Deployment + Service:
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: wip-auth-gateway
spec:
  replicas: 1
  selector:
    matchLabels:
      app: wip-auth-gateway
  template:
    spec:
      containers:
      - name: auth-gateway
        image: ghcr.io/peterseb1969/auth-gateway:v1.2-rc1
        ports:
        - containerPort: 4180
        env:
        - name: OIDC_ISSUER
          value: "https://wip-kubi.local/dex"
        - name: OIDC_INTERNAL_ISSUER
          value: "http://wip-dex:5556/dex"
        - name: OIDC_CLIENT_ID
          value: "wip-gateway"
        - name: OIDC_CLIENT_SECRET
          valueFrom:
            secretKeyRef:
              name: wip-secrets
              key: WIP_GATEWAY_SECRET
        - name: SESSION_SECRET
          valueFrom:
            secretKeyRef:
              name: wip-secrets
              key: WIP_GATEWAY_SESSION_SECRET
        - name: API_KEY
          valueFrom:
            secretKeyRef:
              name: wip-secrets
              key: API_KEY
        - name: WIP_HOSTNAME
          value: "wip-kubi.local"
        - name: CALLBACK_URL
          value: "https://wip-kubi.local/auth/callback"
---
apiVersion: v1
kind: Service
metadata:
  name: wip-auth-gateway
spec:
  selector:
    app: wip-auth-gateway
  ports:
  - port: 4180
    targetPort: 4180
```

## Step 7: App Deployments

Update image tags:
- `react-console.yaml` — `ghcr.io/peterseb1969/react-console:v1.2-rc1`
  - Remove OIDC env vars (gateway handles auth)
  - Keep WIP_BASE_URL, WIP_API_KEY, APP_BASE_PATH, NATS_URL, MONGO_URI, ANTHROPIC_API_KEY
- `dnd-compendium.yaml` — `ghcr.io/peterseb1969/dnd-compendium:v1.1.0`
  - Add ANTHROPIC_API_KEY, MCP_URL: http://wip-mcp-server:8007/sse
- `clintrial-explorer.yaml` — `ghcr.io/peterseb1969/clintrial-explorer:v1.1.0`

**Not included (added later):** receipt-scanner, statement-manager — these are apps that follow the same pattern. Adding them is: one Deployment yaml + one Service yaml + one Ingress resource. Same pattern as DnD/CT.

## Step 8: Ingress with Gateway Auth

This is the key difference from Podman. Instead of Caddy + forward_auth, we use nginx ingress + auth-url annotation.

**Auth gateway ingress** (handles /auth/* routes directly):
```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: auth-gateway-ingress
  annotations:
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
spec:
  ingressClassName: nginx
  tls:
  - hosts: [wip-kubi.local]
    secretName: wip-tls
  rules:
  - host: wip-kubi.local
    http:
      paths:
      - path: /auth
        pathType: Prefix
        backend:
          service:
            name: wip-auth-gateway
            port:
              number: 4180
```

**Dex ingress** (unchanged):
```yaml
- path: /dex
  pathType: Prefix
  backend:
    service:
      name: wip-dex
      port:
        number: 5556
```

**API ingress** (NO auth-url — API key auth only):
```yaml
- path: /api
  pathType: Prefix
  backend:
    service:
      name: wip-caddy-internal  # or direct to services
      port:
        number: 8080
```

Wait — the current k8s setup routes API calls directly to services via separate ingress paths (`/api/registry/*` → wip-registry:8001). No Caddy in the k8s path. This is actually cleaner.

**App ingresses** (WITH auth-url — gateway auth):
```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: react-console-ingress
  annotations:
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    nginx.ingress.kubernetes.io/auth-url: "http://wip-auth-gateway.wip-stable.svc.cluster.local:4180/auth/verify"
    nginx.ingress.kubernetes.io/auth-signin: "https://wip-kubi.local/auth/login?return_to=$escaped_request_uri"
    nginx.ingress.kubernetes.io/auth-response-headers: "X-WIP-User,X-WIP-Groups,X-API-Key"
spec:
  ingressClassName: nginx
  tls:
  - hosts: [wip-kubi.local]
    secretName: wip-tls
  rules:
  - host: wip-kubi.local
    http:
      paths:
      - path: /apps/rc
        pathType: Prefix
        backend:
          service:
            name: react-console
            port:
              number: 3011
```

Each app ingress gets the same three nginx annotations. API ingresses don't.

## Step 9: TLS Certificate

The `wip-tls` secret needs to be recreated in `wip-stable` namespace. Options:
- Copy from the old `wip` namespace before deleting it
- Generate a new self-signed cert: `openssl req -x509 -nodes -days 365 -newkey rsa:2048 -keyout tls.key -out tls.crt -subj "/CN=wip-kubi.local"`
- Use cert-manager if installed on the cluster

## Step 10: GHCR Pull Secret

The cluster needs to pull from GHCR. If packages are private, create a pull secret:
```bash
/snap/bin/microk8s kubectl -n wip-stable create secret docker-registry ghcr-pull \
  --docker-server=ghcr.io \
  --docker-username=peterseb1969 \
  --docker-password=YOUR_GITHUB_PAT
```

Then add `imagePullSecrets: [{name: ghcr-pull}]` to all Deployments/StatefulSets.

If packages are made public (planned for Thursday), this isn't needed.

## Step 11: Apply

```bash
ssh kubi5-1 '/snap/bin/microk8s kubectl delete namespace wip'
# Wait for termination
ssh kubi5-1 '/snap/bin/microk8s kubectl apply -k k8s/'
```

## Step 12: Verify

1. All pods running: `/snap/bin/microk8s kubectl -n wip-stable get pods`
2. Browse to `https://wip-kubi.local/apps/rc/` → Dex login via gateway
3. Login → RC Console with user identity
4. DnD at `/apps/dnd/` → also requires login
5. API calls with API key bypass gateway
6. Restore backups via RC Console

## Files to update

| File | Action |
|------|--------|
| `k8s/namespace.yaml` | Change `wip` → `wip-stable` |
| `k8s/kustomization.yaml` | Update namespace, add auth-gateway, remove wip-console |
| `k8s/secrets.yaml` | Add gateway secrets |
| `k8s/configmaps.yaml` | Add trust proxy headers, update hostnames |
| `k8s/services/auth-gateway.yaml` | **Create** — Deployment + Service |
| `k8s/services/wip-console.yaml` | **Delete** |
| `k8s/services/*.yaml` | Update image tags to GHCR v1.1.0 |
| `k8s/services/react-console.yaml` | Update to v1.2-rc1, remove OIDC env vars |
| `k8s/services/mcp-server.yaml` | Add MCP_PORT, MCP_ALLOWED_HOST |
| `k8s/ingress/ingress.yaml` | Add auth-url annotations for app routes, add /auth/* route |
| `k8s/infrastructure/dex.yaml` | Add wip-gateway client |

## Adding apps later (receipt-scanner, statement-manager)

Each app is 3 resources:
1. **Deployment** — image, env vars, health check
2. **Service** — ClusterIP, port mapping
3. **Ingress** — route + auth-url annotations (copy from any other app ingress)

Add the yaml, `kubectl apply -f`, done. No restart of anything else. This IS "apps as resources."

## Estimated effort

- Update manifests: ~2 hours (mostly mechanical — update namespaces, tags, env vars)
- TLS cert + pull secret: 10 minutes
- Deploy + debug: 30 minutes
- Total: ~3 hours
