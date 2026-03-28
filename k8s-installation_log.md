# WIP on Kubernetes — Installation Log

This log captures all learnings, successes, and failures on the quest to install World In a Pie on a MicroK8s cluster. It serves as the basis for a future installation guide.

---

## Cluster Overview

**Date:** 2026-03-28

3-node MicroK8s v1.32 cluster running on Raspberry Pi 5s (aarch64, Debian Bookworm).

| Node | RAM | Disk (NVMe) | Current Workloads |
|------|-----|-------------|-------------------|
| kubi5-1 | 8GB (2.6GB available) | 99GB (34GB free) | Container registry, Home Assistant, Gitea, Pi-hole |
| kubi5-2 | 8GB (assumed) | unknown | Mosquitto, Python app, Teslamate PG |
| kubi5-3 | 16GB (8.8GB available) | 99GB (68GB free) | Lightest load — preferred WIP target |

### Enabled Add-ons

- **dns** — CoreDNS
- **ha-cluster** — High availability
- **helm** / **helm3** — Package management
- **hostpath-storage** — Local path provisioner
- **ingress** — NGINX Ingress controller (DaemonSet on all nodes, hostPort 80/443)
- **metallb** — LoadBalancer (IP pool: 192.168.1.10–39)
- **rook-ceph** — Distributed block storage
- **storage** — Default storage class

### Storage Classes

| Name | Provisioner | Default |
|------|------------|---------|
| `rook-ceph-block` | rook-ceph.rbd.csi.ceph.com | Yes |
| `microk8s-hostpath` | microk8s.io/hostpath-storage | No |

### Networking

- **CNI:** Calico
- **LoadBalancer:** MetalLB (pool: 192.168.1.10–39, used: .10–.18)
- **Ingress:** NGINX Ingress DaemonSet on all 3 nodes, hostPort 80/443 (no LoadBalancer needed)
- **Ingress classes:** Both `nginx` and `public` exist, same controller
- WIP accessible at `https://<any-node-ip>` once Ingress rule is created

### MetalLB IP Usage

| IP | Service |
|----|---------|
| 192.168.1.10 | Pi-hole DNS |
| 192.168.1.11 | Pi-hole 1 Web |
| 192.168.1.12 | Pi-hole 2 Web |
| 192.168.1.13 | Teslamate |
| 192.168.1.14 | Grafana |
| 192.168.1.15 | UniFi |
| 192.168.1.16 | WireGuard |
| 192.168.1.17 | Gitea |
| 192.168.1.18 | Rook-Ceph Dashboard |
| 192.168.1.19+ | **Available** |

### Existing Namespaces

container-registry, default, ingress, kube-node-lease, kube-public, kube-system, metallb-system, mongodb, pihole, rook-ceph, teslamate, unifi, velero, wireguard

### Container Registry

Local registry on kubi5-1, accessible at `kubi5-1:32000` (NodePort).

### Access

```bash
ssh peter@kubi5-1
/snap/bin/microk8s kubectl <command>
```

No `kubectl` alias is set. All kubectl commands must use the full path `/snap/bin/microk8s kubectl`.

---

## Deployment Plan

### Phase 0: Prerequisites (no cluster changes)
1. Verify cluster readiness — nodes, storage, DNS, registry
2. Review and customise manifests — secrets, hostname, storage class, CORS
3. Build aarch64 images on the Mac, push to `kubi5-1:32000`

### Phase 1: Namespace + Secrets + ConfigMaps (foundation)
4. Create `wip` namespace
5. Deploy secrets (generated, not `CHANGE_ME` placeholders)
6. Deploy configmaps (with correct hostname and service URLs)
7. **Verify:** `kubectl get all -n wip` shows namespace, secrets, configmaps

### Phase 2: Infrastructure (data stores, one at a time)
8. MongoDB StatefulSet + verify with `mongosh` ping
9. PostgreSQL StatefulSet + verify with `pg_isready`
10. NATS StatefulSet + verify with health endpoint
11. MinIO StatefulSet + verify with health endpoint
12. Dex Deployment + verify OIDC discovery endpoint
13. **Verify:** All 5 infra pods Running, PVCs Bound on `rook-ceph-block`

### Phase 3: Services (one at a time, dependency order)
14. Registry (no service dependencies) → verify `/health`
15. Def-Store (needs MongoDB, NATS, Registry) → verify `/health`
16. Template-Store (needs MongoDB, NATS, Registry, Def-Store) → verify `/health`
17. Document-Store (needs MongoDB, NATS, MinIO, Registry, Def-Store, Template-Store) → verify `/health`
18. Reporting-Sync (needs PostgreSQL, NATS, all stores) → verify `/health`
19. Ingest-Gateway (needs NATS, all stores) → verify `/health`
20. **Verify:** All 6 service pods Running, all `/health` returning OK

### Phase 4: Frontend + Ingress
21. Console Deployment → verify nginx serving SPA
22. TLS secret (self-signed for now)
23. Ingress → verify routing from browser
24. **Verify:** Console loads, login works, API calls route correctly

### Phase 5: Smoke Test
25. Seed data via MCP or API — create a terminology, template, document
26. Verify Console — browse created entities
27. Verify Reporting — SQL query returns synced data

### Phase 6: Network Policies (lockdown)
28. Apply NetworkPolicies — one at a time, verify nothing breaks after each
29. Negative test — confirm blocked paths are actually blocked

---

## Deployment Decisions

### Node Affinity Strategy

Use **soft node affinity** (`preferredDuringSchedulingIgnoredDuringExecution`) to prefer kubi5-3 (16GB RAM, most headroom) without hard-pinning. This preserves cluster resilience — if kubi5-3 goes down, pods can reschedule to other nodes.

```yaml
affinity:
  nodeAffinity:
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        preference:
          matchExpressions:
            - key: kubernetes.io/hostname
              operator: In
              values:
                - kubi5-3
```

### Storage Strategy

- PVCs with no explicit storageClassName → uses cluster default (`rook-ceph-block`)
- This gives Ceph-backed distributed storage for MongoDB, PostgreSQL, NATS, MinIO, Dex
- Total PVC request: 10Gi + 5Gi + 2Gi + 20Gi + 1Gi = **38Gi**

### Image Strategy

- Build aarch64 images on Mac (Apple Silicon = native arm64)
- Push to local registry at `kubi5-1:32000`
- `build-images.sh --registry kubi5-1.local:32000 --push --builder podman`
- Image names: `kubi5-1.local:32000/wip-<service>:latest`
- **Note:** Manifests use `wip/<service>:latest` — must be updated to match registry prefix

### Hostname & Ingress Strategy

**Decision:** Use MetalLB to assign a dedicated IP to the NGINX Ingress controller, then Ingress does path-based routing behind that single IP. This follows the same pattern as other services on the cluster (MetalLB IP → DNS entry).

- **Hostname:** `wip-kubi.local`
- **MetalLB IP:** `192.168.1.19` (assigned to `wip-ingress-lb` LoadBalancer Service in `ingress` namespace)
- **DNS:** `wip-kubi.local → 192.168.1.19` (added to local DNS)
- The Ingress controller already runs as a DaemonSet with hostPort 80/443, but the LoadBalancer Service gives it a stable dedicated IP

### Console Build Args

`VITE_OIDC_AUTHORITY` is baked in at build time as `/dex` (relative path). This works with Ingress because the browser resolves it relative to the current host. **The Dex client secret must match between the build arg and the K8s Secret.**

---

## Progress Log

### 2026-03-28 — Initial Cluster Assessment

- SSH'd into kubi5-1 and kubi5-3, surveyed cluster state
- All 3 nodes healthy and Ready
- Rook-Ceph operational with block storage
- MetalLB pool is 192.168.1.10–39 (30 IPs), 9 in use (.10–.18)
- NGINX Ingress running as DaemonSet with hostPort 80/443 (accessible on any node IP)
- Both `nginx` and `public` ingress classes exist
- kubi5-3 identified as preferred deployment target (16GB RAM, 68GB free disk)
- No changes made — read-only exploration

### 2026-03-28 — Phase 0: Manifest Review

**Existing manifests reviewed:** 20 files in `k8s/` — complete coverage of all services.

**Issues found that need fixing before deployment:**

1. **Image names mismatch:** Service manifests use `wip/<service>:latest` but `build-images.sh --registry` produces `kubi5-1.local:32000/wip-<service>:latest`. All service manifests need image name updates.

2. **`CHANGE_ME` secrets:** All secrets are placeholders — need real generated values.

3. **`WIP_HOSTNAME` placeholders:** In configmaps.yaml (issuer URL), dex config (issuer + redirect URI), and ingress.yaml (host + TLS). Need actual hostname.

4. **CORS origins:** Hardcoded to `https://localhost:8443` — must match Ingress hostname.

5. **Ingress class:** Manifest uses `nginx` — confirmed this class exists on the cluster. OK.

6. **StorageClass:** PVCs don't specify storageClassName — uses cluster default `rook-ceph-block`. OK.

7. **Node affinity:** Not yet in any manifest — needs adding to prefer kubi5-3.

8. **Console client secret:** `build-images.sh` hardcodes `VITE_OIDC_CLIENT_SECRET=wip-console-secret` — must match Dex static client secret.

9. **aarch64 compatibility:** All infrastructure images (mongo:7, postgres:16, nats:2.10, minio, dex) are multi-arch and support arm64. OK.

### 2026-03-28 — Phase 1: Namespace + Secrets + ConfigMaps

**Commands executed** (from Mac, piping manifests to the cluster via SSH):

```bash
# Step 1: Create Ingress LoadBalancer Service (MetalLB assigns an IP)
cat k8s/ingress/ingress-lb.yaml | ssh peter@kubi5-1 "/snap/bin/microk8s kubectl apply -f -"
# → service/wip-ingress-lb created

# Step 2: Create wip namespace
cat k8s/namespace.yaml | ssh peter@kubi5-1 "/snap/bin/microk8s kubectl apply -f -"
# → namespace/wip created

# Step 3: Deploy secrets (gitignored file with real credentials)
cat k8s/secrets.local.yaml | ssh peter@kubi5-1 "/snap/bin/microk8s kubectl apply -f -"
# → secret/wip-secrets created

# Step 4: Deploy configmaps (3 ConfigMaps in one file)
cat k8s/configmaps.yaml | ssh peter@kubi5-1 "/snap/bin/microk8s kubectl apply -f -"
# → configmap/wip-config created
# → configmap/wip-dex-config created
# → configmap/wip-console-nginx created
```

**Verification:**

```bash
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl get ns wip"
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl get secret,configmap -n wip"
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl get svc -n ingress wip-ingress-lb"
```

**Results:**
- Namespace `wip`: Active
- Secret `wip-secrets`: 8 data keys
- ConfigMap `wip-config`: 20 entries (service URLs, auth, CORS, file storage, PostgreSQL)
- ConfigMap `wip-dex-config`: Dex OIDC config with issuer `https://wip-kubi.local/dex`
- ConfigMap `wip-console-nginx`: nginx SPA serving config
- LoadBalancer `wip-ingress-lb`: External IP **192.168.1.19**

**Files created/modified locally:**
- `k8s/secrets.local.yaml` — Real credentials (gitignored)
- `k8s/ingress/ingress-lb.yaml` — New LoadBalancer Service for Ingress
- `k8s/configmaps.yaml` — Hostname placeholders replaced with `wip-kubi.local`
- `k8s/ingress/ingress.yaml` — Hostname placeholders replaced with `wip-kubi.local`
- `.gitignore` — Added `k8s/secrets.local.yaml`

**Action required:** Add `192.168.1.19 wip-kubi.local` to local DNS.

**Next:** Phase 2 — Infrastructure (MongoDB, PostgreSQL, NATS, MinIO, Dex), one at a time.

### 2026-03-28 — Phase 2, Step 1: MongoDB

**Attempt 1 — Wrong storage class:**

```bash
cat k8s/infrastructure/mongodb.yaml | ssh peter@kubi5-1 "/snap/bin/microk8s kubectl apply -f -"
```

PVC bound to `microk8s-hostpath` instead of `rook-ceph-block`. **Root cause:** Cluster has two default storage classes. PVCs without explicit `storageClassName` pick whichever default wins (non-deterministic).

**Fix:** Added `storageClassName: rook-ceph-block` to all 5 infrastructure manifests (mongodb, postgres, nats, minio, dex). Deleted StatefulSet + PVC and retried.

```bash
# Cleanup
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl delete statefulset wip-mongodb -n wip"
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl delete pvc data-wip-mongodb-0 -n wip"
```

**Attempt 2 — Ceph CSI provisioner failure:**

PVC stuck Pending with error: `error creating a temporary keyfile: open /tmp/csi/keys/keyfile-*: no such file or directory`.

**Root cause:** Provisioner pod `csi-rbdplugin-provisioner-5f7d95b6fb-xkjt9` (5 restarts) lost its `/tmp/csi/keys/` directory. This is a known Rook-Ceph CSI issue — the directory is an `emptyDir` volume that gets wiped on pod restart and sometimes isn't recreated by the entrypoint.

**Fix:** Restarted the failing provisioner pod:

```bash
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl delete pod csi-rbdplugin-provisioner-5f7d95b6fb-xkjt9 -n rook-ceph"
```

New pod `fhw2z` started with `/tmp/csi/keys/` intact. However, the PVC was still stuck referencing the old pod. Deleted StatefulSet + PVC and retried again.

**Known cluster issue:** This is a workaround, not a permanent fix. If a Ceph CSI provisioner pod restarts and loses `/tmp/csi/keys/`, new PVC provisioning will fail. The fix is to restart the affected provisioner pod. Existing Bound PVCs are unaffected. A proper fix would be upgrading Rook-Ceph or adding an initContainer to ensure the directory exists.

**Attempt 3 — Liveness probe timeout:**

PVC bound successfully on `rook-ceph-block` (10Gi), but MongoDB pod kept restarting. Liveness probe (`mongosh ping`) timed out after 1s — too aggressive for Pi + Ceph.

**Fix:** Updated `k8s/infrastructure/mongodb.yaml` probe settings:

```yaml
# Before (too aggressive for Pi + Ceph):
readinessProbe:
  initialDelaySeconds: 5
  periodSeconds: 10
  # timeoutSeconds: 1 (default)
livenessProbe:
  initialDelaySeconds: 15
  periodSeconds: 30
  # timeoutSeconds: 1 (default)

# After:
readinessProbe:
  initialDelaySeconds: 30
  periodSeconds: 10
  timeoutSeconds: 5
livenessProbe:
  initialDelaySeconds: 60
  periodSeconds: 30
  timeoutSeconds: 5
```

**Learning:** StatefulSet probe changes don't take effect on running pods — must delete the pod to pick up new config:

```bash
cat k8s/infrastructure/mongodb.yaml | ssh peter@kubi5-1 "/snap/bin/microk8s kubectl apply -f -"
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl delete pod wip-mongodb-0 -n wip"
```

**Final result:**

```bash
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl get pod -n wip -o wide"
# NAME            READY   STATUS    RESTARTS   AGE   NODE
# wip-mongodb-0   1/1     Running   0          35s   kubi5-1

ssh peter@kubi5-1 "/snap/bin/microk8s kubectl exec -n wip wip-mongodb-0 -- mongosh --quiet --eval 'db.runCommand({ping:1}).ok'"
# 1
```

| Check | Result |
|-------|--------|
| PVC | Bound, 10Gi, `rook-ceph-block` |
| Pod | 1/1 Running, 0 restarts, kubi5-1 |
| `mongosh ping` | OK |

**Lessons learned:**
1. Always specify `storageClassName` explicitly when a cluster has multiple defaults
2. Rook-Ceph CSI provisioner pods can lose `/tmp/csi/keys/` on restart — restart the pod to fix
3. Default probe `timeoutSeconds: 1` is too short for `mongosh` on Pi + Ceph — use 5s
4. Increase `initialDelaySeconds` for liveness probes on slow storage (60s for MongoDB)
5. StatefulSet probe updates require pod deletion to take effect
6. PostgreSQL on Ceph RBD requires `PGDATA` subdirectory to avoid `lost+found` conflict
7. Dex runs as UID 1001 — Ceph volumes need `fsGroup: 1001` in the pod security context
8. **RWO volume rule:** Ceph RBD volumes are ReadWriteOnce — only one pod can mount them at a time. When redeploying, **delete the old pod/deployment first**, then create the new one. Rolling updates will hang with the new pod stuck in `ContainerCreating`. For Deployments with RWO PVCs, use `strategy: { type: Recreate }` instead of the default `RollingUpdate`.

### 2026-03-28 — Phase 2, Step 2: PostgreSQL

**Attempt 1 — Ceph `lost+found` conflict:**

```bash
cat k8s/infrastructure/postgres.yaml | ssh peter@kubi5-1 "/snap/bin/microk8s kubectl apply -f -"
# → service/wip-postgres created
# → statefulset.apps/wip-postgres created
```

PVC bound immediately (`rook-ceph-block`, 5Gi) but pod went to CrashLoopBackOff:

```
initdb: error: directory "/var/lib/postgresql/data" exists but is not empty
initdb: detail: It contains a lost+found directory, perhaps due to it being a mount point.
initdb: hint: Using a mount point directly as the data directory is not recommended.
```

**Root cause:** Ceph RBD (ext4) volumes create a `lost+found` directory at the mount root. PostgreSQL's `initdb` refuses to initialize in a non-empty directory.

**Fix:** Set `PGDATA` env var to use a subdirectory:

```yaml
env:
  - name: PGDATA
    value: /var/lib/postgresql/data/pgdata
```

Also pre-emptively bumped probe timeouts (same lesson as MongoDB):

```yaml
readinessProbe:
  initialDelaySeconds: 15   # was 5
  timeoutSeconds: 5         # was 1 (default)
livenessProbe:
  initialDelaySeconds: 30   # was 15
  timeoutSeconds: 5         # was 1 (default)
```

Deleted StatefulSet + PVC (corrupted init state) and redeployed:

```bash
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl delete statefulset wip-postgres -n wip"
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl delete pvc data-wip-postgres-0 -n wip"
cat k8s/infrastructure/postgres.yaml | ssh peter@kubi5-1 "/snap/bin/microk8s kubectl apply -f -"
# → service/wip-postgres unchanged
# → statefulset.apps/wip-postgres created
```

**Verification:**

```bash
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl get pod -n wip -o wide"
# NAME             READY   STATUS    RESTARTS   AGE   NODE
# wip-postgres-0   1/1     Running   0          35s   kubi5-1

ssh peter@kubi5-1 "/snap/bin/microk8s kubectl exec -n wip wip-postgres-0 -- pg_isready -U wip -d wip_reporting"
# /var/run/postgresql:5432 - accepting connections
```

| Check | Result |
|-------|--------|
| PVC | Bound, 5Gi, `rook-ceph-block` |
| Pod | 1/1 Running, 0 restarts, kubi5-1 |
| `pg_isready` | Accepting connections |

**Lesson learned:**
6. PostgreSQL on Ceph RBD requires `PGDATA` set to a subdirectory (e.g., `/var/lib/postgresql/data/pgdata`) to avoid the `lost+found` conflict. This is a well-known issue with any ext4/xfs-formatted block storage.

### 2026-03-28 — Phase 2, Step 3: NATS

Pre-emptively bumped probe timeouts (`initialDelaySeconds: 10/20`, `timeoutSeconds: 5`) before deploying.

```bash
cat k8s/infrastructure/nats.yaml | ssh peter@kubi5-1 "/snap/bin/microk8s kubectl apply -f -"
# → service/wip-nats created
# → statefulset.apps/wip-nats created
```

**Clean deploy — no issues.** PVC provisioned immediately, pod 1/1 Running in ~30s.

**Verification:**

```bash
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl get pod,pvc -n wip -l app.kubernetes.io/name=nats"
# NAME             READY   STATUS    RESTARTS   AGE
# pod/wip-nats-0   1/1     Running   0          32s
# PVC: Bound, 2Gi, rook-ceph-block

# NATS container has no wget/curl — use a temporary busybox pod:
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl run nats-check --rm -i --restart=Never --image=busybox -n wip -- wget -qO- http://wip-nats:8222/healthz"
# {"status":"ok"}
```

| Check | Result |
|-------|--------|
| PVC | Bound, 2Gi, `rook-ceph-block` |
| Pod | 1/1 Running, 0 restarts |
| `/healthz` | `{"status":"ok"}` |

**Note:** NATS minimal image has no shell utilities (wget, curl). Use a temporary busybox pod for health checks, or rely on the K8s readiness probe status.

### 2026-03-28 — Phase 2, Step 4: MinIO

Pre-emptively bumped probe timeouts (`initialDelaySeconds: 10/20`, `timeoutSeconds: 5`).

```bash
cat k8s/infrastructure/minio.yaml | ssh peter@kubi5-1 "/snap/bin/microk8s kubectl apply -f -"
# → service/wip-minio created
# → statefulset.apps/wip-minio created
```

**Clean deploy — no issues.** PVC provisioned immediately (20Gi), pod 1/1 Running in ~30s.

**Verification:**

```bash
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl get pod,pvc -n wip -l app.kubernetes.io/name=minio"
# NAME              READY   STATUS    RESTARTS   AGE
# pod/wip-minio-0   1/1     Running   0          35s
# PVC: Bound, 20Gi, rook-ceph-block

ssh peter@kubi5-1 "/snap/bin/microk8s kubectl run minio-check --rm -i --restart=Never --image=busybox -n wip -- wget -S -O /dev/null http://wip-minio:9000/minio/health/live 2>&1 | head -2"
# HTTP/1.1 200 OK
```

| Check | Result |
|-------|--------|
| PVC | Bound, 20Gi, `rook-ceph-block` |
| Pod | 1/1 Running, 0 restarts |
| `/minio/health/live` | HTTP 200 OK (empty body — MinIO convention) |

### 2026-03-28 — Phase 2, Step 5: Dex

**Attempt 1 — SQLite permission error:**

```bash
cat k8s/infrastructure/dex.yaml | ssh peter@kubi5-1 "/snap/bin/microk8s kubectl apply -f -"
# → persistentvolumeclaim/wip-dex-data created
# → service/wip-dex created
# → deployment.apps/wip-dex created
```

PVC bound, but pod crashed with:

```
failed to initialize storage: failed to perform migrations: creating migration table:
unable to open database file: no such file or directory
```

**Root cause:** Dex runs as UID 1001 inside the container. The Ceph RBD volume is mounted with root ownership. Dex can't create the SQLite database file.

**Fix:** Add `fsGroup: 1001` to the pod security context:

```yaml
spec:
  securityContext:
    fsGroup: 1001
```

This tells Kubernetes to chown the volume to GID 1001, making it writable by the Dex process.

**Attempt 2 — RWO volume contention:**

Applied the fix but the new pod was stuck in `ContainerCreating` while the old crashing pod still held the RWO Ceph volume. Deployment rolling update can't proceed because RWO allows only one mount.

**Fix:** Delete the entire deployment first, then re-apply:

```bash
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl delete deployment wip-dex -n wip"
cat k8s/infrastructure/dex.yaml | ssh peter@kubi5-1 "/snap/bin/microk8s kubectl apply -f -"
# → deployment.apps/wip-dex created
```

**Verification:**

```bash
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl get pod -n wip -l app.kubernetes.io/name=dex"
# NAME                      READY   STATUS    RESTARTS   AGE
# wip-dex-c98fd9f6b-52kvg   1/1     Running   0          41s

ssh peter@kubi5-1 "/snap/bin/microk8s kubectl run dex-check --rm -i --restart=Never --image=busybox -n wip -- wget -qO- http://wip-dex:5556/dex/.well-known/openid-configuration 2>/dev/null"
# {"issuer":"https://wip-kubi.local/dex", ...}
```

| Check | Result |
|-------|--------|
| PVC | Bound, 1Gi, `rook-ceph-block` |
| Pod | 1/1 Running, 0 restarts, kubi5-3 |
| OIDC discovery | Issuer = `https://wip-kubi.local/dex` |

**Lessons learned:**
7. Dex runs as UID 1001 — Ceph volumes need `fsGroup: 1001` in the pod security context
8. Deployments with RWO volumes can't do rolling updates (old pod holds the volume). Must delete the deployment before re-creating, or set `strategy.type: Recreate`
9. Consider adding `strategy: { type: Recreate }` to all Deployments with RWO PVCs to avoid this in future

### 2026-03-28 — Phase 2 Complete: Infrastructure Summary

All 5 infrastructure components deployed and verified:

```bash
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl get pod,pvc -n wip -o wide"
```

| Pod | Status | Node | PVC | Storage |
|-----|--------|------|-----|---------|
| wip-mongodb-0 | 1/1 Running | kubi5-1 | 10Gi | rook-ceph-block |
| wip-postgres-0 | 1/1 Running | kubi5-1 | 5Gi | rook-ceph-block |
| wip-nats-0 | 1/1 Running | kubi5-1 | 2Gi | rook-ceph-block |
| wip-minio-0 | 1/1 Running | kubi5-1 | 20Gi | rook-ceph-block |
| wip-dex-* | 1/1 Running | kubi5-3 | 1Gi | rook-ceph-block |

Total Ceph storage allocated: **38Gi**

**Next:** Phase 3 — Build aarch64 images and deploy WIP services.

### 2026-03-28 — Phase 3, Step 0: Build & Push Images

Built all 7 WIP images on Mac (Apple Silicon = native arm64) using podman and pushed to the cluster registry:

```bash
cd /Users/peter/Development/WorldInPie
bash k8s/build-images.sh --registry kubi5-1.local:32000 --push --builder podman
# ✓ All images built successfully.
```

Verified all images present in registry:

```bash
curl -s http://kubi5-1.local:32000/v2/_catalog
# wip-registry, wip-def-store, wip-template-store, wip-document-store,
# wip-reporting-sync, wip-ingest-gateway, wip-console
```

Updated all 7 service manifests in `k8s/services/` with registry image references.

**Image reference issue:** Initially used `kubi5-1.local:32000/wip-*` in manifests, but this failed:
1. MicroK8s containerd only has `localhost:32000` configured as insecure HTTP registry
2. `kubi5-1.local` DNS didn't resolve from all nodes
3. Containerd tried HTTPS for non-localhost registries

**Fix:** Changed all manifests to use `localhost:32000/wip-*:latest`. The MicroK8s registry addon runs as a NodePort (32000) accessible via localhost on every node. The registry stores images by repo name (e.g., `wip-registry`), not hostname — so images pushed via `kubi5-1.local:32000` are pullable via `localhost:32000`.

**For future builds:** Push with `--registry kubi5-1.local:32000` from the Mac (because localhost:32000 doesn't reach the Pi from the Mac), but reference as `localhost:32000` in K8s manifests.

**Lesson learned:**
9. MicroK8s local registry: push from external machines via `kubi5-1.local:32000`, but reference in manifests as `localhost:32000`. The registry is a single service — both hostnames reach the same image store.

### 2026-03-28 — Phase 3, Step 1: Registry Service

```bash
cat k8s/services/registry.yaml | ssh peter@kubi5-1 "/snap/bin/microk8s kubectl apply -f -"
# → service/wip-registry created
# → deployment.apps/wip-registry created
```

**First attempt failed** with `ErrImagePull` — used `kubi5-1.local:32000` image ref (see Step 0 for details). After fixing to `localhost:32000`, deleted old pod and redeployed:

```bash
cat k8s/services/registry.yaml | ssh peter@kubi5-1 "/snap/bin/microk8s kubectl apply -f -"
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl delete pod -n wip -l app.kubernetes.io/name=registry"
```

**Verification:**

```bash
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl get pod -n wip -l app.kubernetes.io/name=registry -o wide"
# NAME                            READY   STATUS    RESTARTS   AGE   NODE
# wip-registry-6f88877c79-d22gr   1/1     Running   0          32s   kubi5-3

ssh peter@kubi5-1 "/snap/bin/microk8s kubectl run reg-check --rm -i --restart=Never --image=busybox -n wip -- wget -qO- http://wip-registry:8001/health"
# {"status":"healthy","database":"connected","auth_enabled":true}
```

| Check | Result |
|-------|--------|
| Pod | 1/1 Running, 0 restarts, kubi5-3 |
| `/health` | `{"status":"healthy","database":"connected","auth_enabled":true}` |

### 2026-03-28 — Phase 3, Step 2: Def-Store

```bash
cat k8s/services/def-store.yaml | ssh peter@kubi5-1 "/snap/bin/microk8s kubectl apply -f -"
# → service/wip-def-store created
# → deployment.apps/wip-def-store created
```

**Clean deploy — no issues.**

```bash
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl get pod -n wip -l app.kubernetes.io/name=def-store -o wide"
# wip-def-store-68cdb959c8-g8h5j   1/1   Running   0   35s   kubi5-2

ssh peter@kubi5-1 "/snap/bin/microk8s kubectl run ds-check --rm -i --restart=Never --image=busybox -n wip -- wget -qO- http://wip-def-store:8002/health"
# {"status":"healthy","database":"connected","registry":"connected"}
```

| Check | Result |
|-------|--------|
| Pod | 1/1 Running, 0 restarts, kubi5-2 |
| `/health` | `{"status":"healthy","database":"connected","registry":"connected"}` |

### 2026-03-28 — Phase 3, Step 3: Template-Store

```bash
cat k8s/services/template-store.yaml | ssh peter@kubi5-1 "/snap/bin/microk8s kubectl apply -f -"
# → service/wip-template-store created
# → deployment.apps/wip-template-store created
```

**Clean deploy — no issues.**

```bash
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl get pod -n wip -l app.kubernetes.io/name=template-store -o wide"
# wip-template-store-5949b574db-wjzm5   1/1   Running   0   31s   kubi5-3

ssh peter@kubi5-1 "/snap/bin/microk8s kubectl run ts-check --rm -i --restart=Never --image=busybox -n wip -- wget -qO- http://wip-template-store:8003/health"
# {"status":"healthy","database":"connected","registry":"connected","def_store":"connected","nats":"connected"}
```

| Check | Result |
|-------|--------|
| Pod | 1/1 Running, 0 restarts, kubi5-3 |
| `/health` | healthy — database, registry, def_store, nats all connected |

### 2026-03-28 — Phase 3, Step 4: Document-Store

```bash
cat k8s/services/document-store.yaml | ssh peter@kubi5-1 "/snap/bin/microk8s kubectl apply -f -"
# → service/wip-document-store created
# → deployment.apps/wip-document-store created
```

**Clean deploy — no issues.**

```bash
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl get pod -n wip -l app.kubernetes.io/name=document-store -o wide"
# wip-document-store-7bb846d56b-pgp9v   1/1   Running   0   32s   kubi5-2

ssh peter@kubi5-1 "/snap/bin/microk8s kubectl run doc-check --rm -i --restart=Never --image=busybox -n wip -- wget -qO- http://wip-document-store:8004/health"
# {"status":"healthy","database":"connected","registry":"connected","template_store":"connected","def_store":"connected","nats":"connected","file_storage":"connected"}
```

| Check | Result |
|-------|--------|
| Pod | 1/1 Running, 0 restarts, kubi5-2 |
| `/health` | healthy — database, registry, template_store, def_store, nats, file_storage all connected |

### 2026-03-28 — Phase 3, Step 5: Reporting-Sync

```bash
cat k8s/services/reporting-sync.yaml | ssh peter@kubi5-1 "/snap/bin/microk8s kubectl apply -f -"
# → service/wip-reporting-sync created
# → deployment.apps/wip-reporting-sync created
```

**Clean deploy — no issues.**

```bash
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl get pod -n wip -l app.kubernetes.io/name=reporting-sync -o wide"
# wip-reporting-sync-78dd96b88d-4jsjf   1/1   Running   0   38s   kubi5-1

ssh peter@kubi5-1 "/snap/bin/microk8s kubectl run rs-check --rm -i --restart=Never --image=busybox -n wip -- wget -qO- http://wip-reporting-sync:8005/health"
# {"status":"healthy","service":"wip-reporting-sync","version":"0.1.0","nats_connected":true,"postgres_connected":true,"details":{"stream_name":"WIP_EVENTS"}}
```

| Check | Result |
|-------|--------|
| Pod | 1/1 Running, 0 restarts, kubi5-1 |
| `/health` | healthy — NATS connected, PostgreSQL connected, stream WIP_EVENTS |

### 2026-03-28 — Phase 3, Step 6: Ingest-Gateway

```bash
cat k8s/services/ingest-gateway.yaml | ssh peter@kubi5-1 "/snap/bin/microk8s kubectl apply -f -"
# → service/wip-ingest-gateway created
# → deployment.apps/wip-ingest-gateway created
```

**First attempt failed** — pod crashed with:

```
nats.js.errors.ServerError: code=500 err_code=10047 description='insufficient storage resources available'
```

**Root cause:** NATS JetStream had `max_storage: ~1.4GB` (from 2Gi PVC). Reporting-Sync already reserved 1GB for WIP_EVENTS stream. Ingest-Gateway needs 2 more streams (WIP_INGEST + WIP_INGEST_RESULTS) — not enough room.

**Fix:** Online PVC expansion (Ceph supports `allowVolumeExpansion: true`):

```bash
# Expand PVC from 2Gi to 5Gi (no downtime needed for the patch)
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl patch pvc data-wip-nats-0 -n wip -p '{\"spec\":{\"resources\":{\"requests\":{\"storage\":\"5Gi\"}}}}'"

# Restart NATS pod to pick up expanded filesystem
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl delete pod wip-nats-0 -n wip"

# Verify: max_storage increased from ~1.4GB to ~3.6GB
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl run nats-jsz --rm -i --restart=Never --image=busybox -n wip -- wget -qO- http://wip-nats:8222/jsz"
# "max_storage": 3894082560

# Restart Ingest-Gateway
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl delete pod -n wip -l app.kubernetes.io/name=ingest-gateway"
```

Updated `k8s/infrastructure/nats.yaml` to 5Gi for future deployments.

**Verification:**

```bash
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl get pod -n wip -l app.kubernetes.io/name=ingest-gateway -o wide"
# wip-ingest-gateway-6fd964d6bc-bjnp4   1/1   Running   0   32s   kubi5-3

ssh peter@kubi5-1 "/snap/bin/microk8s kubectl run ig-check --rm -i --restart=Never --image=busybox -n wip -- wget -qO- http://wip-ingest-gateway:8006/health"
# {"status":"healthy","nats_connected":true,"worker_running":true,"details":{"ingest_stream":"WIP_INGEST","results_stream":"WIP_INGEST_RESULTS"}}
```

| Check | Result |
|-------|--------|
| Pod | 1/1 Running, 0 restarts, kubi5-3 |
| `/health` | healthy — NATS connected, worker running, both streams created |

**Lesson learned:**
10. NATS JetStream PVC must be large enough for all streams. WIP needs ~2GB reserved across 3 streams (WIP_EVENTS, WIP_INGEST, WIP_INGEST_RESULTS). 2Gi PVC is insufficient — use 5Gi. Ceph supports online PVC expansion via `kubectl patch pvc`.

### 2026-03-28 — Phase 3 Complete: Services Summary

All 6 WIP services deployed and verified:

```bash
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl get pod -n wip -o wide"
```

| Pod | Status | Node | Health |
|-----|--------|------|--------|
| wip-registry-* | 1/1 Running | kubi5-3 | database + auth connected |
| wip-def-store-* | 1/1 Running | kubi5-2 | database + registry connected |
| wip-template-store-* | 1/1 Running | kubi5-3 | database + registry + def_store + nats |
| wip-document-store-* | 1/1 Running | kubi5-2 | database + registry + template + def + nats + file_storage |
| wip-reporting-sync-* | 1/1 Running | kubi5-1 | NATS + PostgreSQL connected |
| wip-ingest-gateway-* | 1/1 Running | kubi5-3 | NATS + worker running |

All services spread across all 3 nodes. All inter-service connections verified via `/health` endpoints.

**Next:** Phase 4 — Console + TLS + Ingress.

### 2026-03-28 — Phase 4, Step 1: Console

```bash
cat k8s/services/wip-console.yaml | ssh peter@kubi5-1 "/snap/bin/microk8s kubectl apply -f -"
# → service/wip-console created
# → deployment.apps/wip-console created
```

**Clean deploy — no issues.**

```bash
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl get pod -n wip -l app.kubernetes.io/name=console -o wide"
# wip-console-c54546f5c-2qnvs   1/1   Running   0   32s   kubi5-2

ssh peter@kubi5-1 "/snap/bin/microk8s kubectl run con-check --rm -i --restart=Never --image=busybox -n wip -- wget -qO- http://wip-console:80/ | head -5"
# <!DOCTYPE html>
# <html lang="en">
#   <head>
#     <meta charset="UTF-8" />
#     <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
```

| Check | Result |
|-------|--------|
| Pod | 1/1 Running, 0 restarts, kubi5-2 |
| HTML served | Vue SPA `index.html` returned |

### 2026-03-28 — Phase 4, Step 2: TLS Certificate

Generated a self-signed certificate for `wip-kubi.local` (valid 365 days) and deployed as a K8s TLS secret:

```bash
# Generate cert on Mac
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout /tmp/wip-kubi-tls.key \
  -out /tmp/wip-kubi-tls.crt \
  -subj "/CN=wip-kubi.local" \
  -addext "subjectAltName=DNS:wip-kubi.local"

# Copy to Pi and create secret
cat /tmp/wip-kubi-tls.crt | ssh peter@kubi5-1 "cat > /tmp/wip-tls.crt"
cat /tmp/wip-kubi-tls.key | ssh peter@kubi5-1 "cat > /tmp/wip-tls.key"
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl create secret tls wip-tls -n wip \
  --cert=/tmp/wip-tls.crt --key=/tmp/wip-tls.key && rm /tmp/wip-tls.crt /tmp/wip-tls.key"
# → secret/wip-tls created
```

```bash
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl get secret wip-tls -n wip"
# NAME      TYPE                DATA   AGE
# wip-tls   kubernetes.io/tls   2      4s
```

**Note:** Self-signed cert — browsers will show a security warning. Acceptable for home LAN (Tier 1).

### 2026-03-28 — Phase 4, Step 3: Ingress

Applied the NGINX Ingress rule for path-based routing to all WIP services:

```bash
cat k8s/ingress/ingress.yaml | ssh peter@kubi5-1 "/snap/bin/microk8s kubectl apply -f -"
```

**Ingress configuration:**
- Host: `wip-kubi.local`
- TLS: `wip-tls` secret (self-signed)
- SSL redirect: enabled
- Proxy body size: 100m (for file uploads)
- Proxy buffering: off (for streaming downloads)

**Routes:**

| Path | Backend | Port |
|------|---------|------|
| `/dex` | wip-dex | 5556 |
| `/api/registry` | wip-registry | 8001 |
| `/api/def-store` | wip-def-store | 8002 |
| `/api/template-store` | wip-template-store | 8003 |
| `/api/document-store` | wip-document-store | 8004 |
| `/api/reporting-sync` | wip-reporting-sync | 8005 |
| `/api/ingest-gateway` | wip-ingest-gateway | 8006 |
| `/` (catch-all) | wip-console | 80 |

```bash
cat k8s/ingress/ingress.yaml | ssh peter@kubi5-1 "/snap/bin/microk8s kubectl apply -f -"
# → ingress.networking.k8s.io/wip-ingress created
```

**Verification:**

```bash
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl describe ingress wip-ingress -n wip"
# All 8 backends resolved to pod IPs
# TLS: wip-tls terminates wip-kubi.local
# Events: Scheduled for sync on all 3 nodes

# External access tests:
curl -sk https://wip-kubi.local/                                    # → 200, Vue SPA HTML
curl -sk https://wip-kubi.local/dex/.well-known/openid-configuration # → issuer: https://wip-kubi.local/dex
curl -sk https://wip-kubi.local/api/registry/namespaces              # → {"detail":"Authentication required"}
curl -sk https://wip-kubi.local/api/def-store/terminologies          # → {"detail":"Authentication required"}
curl -sk http://wip-kubi.local/                                      # → 308 redirect to https://
```

| Check | Result |
|-------|--------|
| Console | 200, Vue SPA served |
| Dex OIDC discovery | Issuer = `https://wip-kubi.local/dex` |
| API routing | Reaches services, auth enforced |
| HTTP→HTTPS redirect | 308 redirect working |
| TLS termination | Self-signed cert served |

**Note:** Service `/health` endpoints are at the root path (e.g., `GET /health` on port 8001), not under `/api/<service>/health`. They are only accessible internally via K8s probes, not through the Ingress. This is by design — the Ingress routes business API traffic, K8s handles health monitoring directly.

### 2026-03-28 — Phase 4 Complete: External Access Summary

WIP is now accessible at `https://wip-kubi.local` from any device on the LAN.

| Component | External URL | Status |
|-----------|-------------|--------|
| Console | `https://wip-kubi.local/` | Serving |
| Dex OIDC | `https://wip-kubi.local/dex/` | Configured |
| Registry API | `https://wip-kubi.local/api/registry/` | Auth enforced |
| Def-Store API | `https://wip-kubi.local/api/def-store/` | Auth enforced |
| Template-Store API | `https://wip-kubi.local/api/template-store/` | Auth enforced |
| Document-Store API | `https://wip-kubi.local/api/document-store/` | Auth enforced |
| Reporting-Sync API | `https://wip-kubi.local/api/reporting-sync/` | Auth enforced |
| Ingest-Gateway API | `https://wip-kubi.local/api/ingest-gateway/` | Auth enforced |

### 2026-03-28 — Phase 5: Bootstrap & Smoke Test

#### Step 1: Initialize WIP Namespace

The "wip" namespace must be created before services can bootstrap. In docker-compose, `setup.sh` handles this. On K8s, it must be done manually after the Registry is healthy:

```bash
curl -sk -X POST https://wip-kubi.local/api/registry/namespaces/initialize-wip \
  -H "X-API-Key: <your-api-key>"
# → {"prefix":"wip","description":"Default World In a Pie namespace","isolation_mode":"open",...}
```

#### Step 2: Fix Dex Groups

The K8s Dex ConfigMap was missing `groups` on static passwords — JWTs had no group claims, causing "No Namespace Access" in the Console. Added groups to `k8s/configmaps.yaml`:

```yaml
staticPasswords:
  - email: admin@wip.local
    # ...
    groups:
      - wip-admins
  - email: editor@wip.local
    # ...
    groups:
      - wip-editors
  - email: viewer@wip.local
    # ...
    groups:
      - wip-viewers
```

Applied ConfigMap and restarted Dex (delete deployment first — RWO volume rule):

```bash
cat k8s/configmaps.yaml | ssh peter@kubi5-1 "/snap/bin/microk8s kubectl apply -f -"
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl delete deployment wip-dex -n wip"
cat k8s/infrastructure/dex.yaml | ssh peter@kubi5-1 "/snap/bin/microk8s kubectl apply -f -"
```

Dex pod pinned to kubi5-3 (image already cached there — ghcr.io throttles pulls to ~50 KB/s).

**Lesson learned:**
11. **Startup ordering matters.** In docker-compose, `setup.sh` orchestrates: (1) start services, (2) call `initialize-wip`, (3) Def-Store bootstraps terminologies. On K8s, all pods start simultaneously. The "wip" namespace must be initialized manually before Def-Store can create built-in terminologies. If Def-Store starts first, restart it after initializing the namespace.
12. **Dex static passwords need explicit `groups` field.** Without it, JWTs have no group claims and the namespace permission system denies access. The docker-compose Dex config has them; the K8s ConfigMap template was missing them.
13. **ghcr.io image pulls can be very slow (~50 KB/s).** Pin pods to nodes where the image is already cached using `nodeSelector` to avoid re-pulling.

#### Step 3: Restart Def-Store for Built-in Terminologies

Def-Store failed to create system terminologies at startup because the "wip" namespace didn't exist yet. Restarting after namespace initialization fixed it:

```bash
ssh peter@kubi5-1 "/snap/bin/microk8s kubectl delete pod -n wip -l app.kubernetes.io/name=def-store"
# After restart:
# Created system terminology '_TIME_UNITS' with ID 019d3568-933e-...
# Created system terminology '_ONTOLOGY_RELATIONSHIP_TYPES' with ID 019d3568-9396-...
# System terminologies: 2 created, 0 existed, 14 terms created, 0 terms existed
```

#### Step 4: Console Login Verified

- Login via Dex with `admin@wip.local` / `admin123` — works
- JWT includes `wip-admins` group — namespace access granted
- Built-in terminologies visible in Console

#### Step 5: Remote Seeding from Mac

Added `--port` flag to `scripts/seed_comprehensive.py` to support K8s Ingress (port 443) in addition to Caddy (port 8443). Also fixed health checks in proxy mode to use API endpoints instead of unreachable `/health` root paths.

```bash
source .venv/bin/activate
WIP_API_KEY=<key> python scripts/seed_comprehensive.py \
  --host wip-kubi.local --via-proxy --port 443 --profile minimal
```

**Result — complete success, zero errors:**

| Category | Count | Time |
|----------|-------|------|
| Terminologies | 15 (215 terms) | 6.1s |
| Templates | 25 + 2 version upgrades | 3.6s |
| Documents | 60 + 5 versioning tests | 1.4s |
| **Total** | | **11.4s** |

Document creation throughput: **44 docs/sec** over the network (Mac → K8s Ingress → services on Pi cluster).

**Lesson learned:**
14. The seed script works for remote seeding via `--host <hostname> --via-proxy --port 443`. The `--port` flag was added to support K8s Ingress (443) vs Caddy (8443). Push from Mac, reference as `localhost:32000` in manifests — same pattern as image builds.

#### Step 6: Console Verification

Logged into WIP Console at `https://wip-kubi.local` as `admin@wip.local`:
- Namespace switcher works (wip + seed namespaces visible)
- Built-in terminologies (`_TIME_UNITS`, `_ONTOLOGY_RELATIONSHIP_TYPES`) present
- Seed data (15 terminologies, 25 templates, 60 documents) all visible and browsable
- UI is fast and snappy — no noticeable latency despite 3-node Pi cluster with Ceph storage

### 2026-03-28 — Phase 5 Complete: Smoke Test Passed

WIP is fully operational on the K8s cluster:

| Component | Status |
|-----------|--------|
| Console | Serving, login works, data browsable |
| OIDC (Dex) | Login with dev credentials, group claims in JWT |
| Registry | Namespace initialization, synonym resolution |
| Def-Store | System terminologies bootstrapped, seed terminologies created |
| Template-Store | 25 templates + versioning |
| Document-Store | 60 documents, validation, identity dedup |
| Reporting-Sync | NATS connected, PostgreSQL sync |
| Ingest-Gateway | NATS streams created, worker running |
| Remote seeding | Works from Mac via Ingress |

### 2026-03-28 — MCP Server on K8s: Design

#### Goal

Deploy the WIP MCP server on the K8s cluster, accessible via HTTP streamable transport through the Ingress. Any Claude Code instance on the LAN can connect to `https://wip-kubi.local/mcp/` with an API key.

#### Transport Choice: HTTP Streamable (not SSE)

The MCP Python library (v1.26.0) supports three transports: `stdio`, `sse`, and `streamable-http`. SSE is deprecated in the MCP spec. HTTP streamable is the replacement:

| | SSE | HTTP Streamable |
|--|-----|-----------------|
| Endpoints | `/sse` (GET, long-lived) + `/messages` (POST) | `/mcp` (single, standard HTTP) |
| Connection | Long-lived server-sent events stream | Standard request/response |
| Ingress | Needs timeout tuning (3600s) for SSE stream | Standard timeouts work |
| Claude Code config | `"type": "sse"` | `"type": "http"` |
| MCP spec status | Deprecated | Current |

#### Architecture

```
Claude Code (Mac)
    │
    │  HTTPS + X-API-Key header
    │
    ▼
NGINX Ingress (wip-kubi.local)
    │
    │  /mcp/* → wip-mcp-server:8007
    │
    ▼
MCP Server Pod (stateless)
    │
    │  HTTP (internal K8s DNS)
    │
    ├──► wip-registry:8001
    ├──► wip-def-store:8002
    ├──► wip-template-store:8003
    ├──► wip-document-store:8004
    └──► wip-reporting-sync:8005
```

#### Port: 8007

- 8006 is already used by ingest-gateway
- FastMCP defaults to 8000, but we override to 8007 for consistency with WIP's 800x scheme
- Dockerfile EXPOSE and K8s Service both use 8007

#### Code Changes

**1. `components/mcp-server/src/wip_mcp/server.py`** — update `main()`:
- Accept `--http` flag (in addition to existing `--sse`)
- Use `mcp.streamable_http_app()` instead of `mcp.sse_app()`
- Override host to `0.0.0.0` and port to `8007` for container mode
- Keep API key middleware unchanged (same Starlette pattern)

**2. `components/mcp-server/Dockerfile`** — update:
- Change `EXPOSE 8006` → `EXPOSE 8007`
- Change CMD to `--http` flag

**3. `k8s/build-images.sh`** — add mcp-server to the build list

**4. New: `k8s/services/mcp-server.yaml`** — Deployment + Service:
- Image: `localhost:32000/wip-mcp-server:latest`
- Port: 8007
- Env: service URLs via `wip-config` ConfigMap, API key from `wip-secrets`
- Stateless — no PVC
- Health check: TBD (FastMCP may expose a health endpoint)

**5. `k8s/ingress/ingress.yaml`** — add path:
- `/mcp` → `wip-mcp-server:8007`
- No special annotations needed (standard HTTP)

**6. `.mcp.json`** — add `wip-kubi` entry:
```json
"wip-kubi": {
  "type": "http",
  "url": "https://wip-kubi.local/mcp/mcp",
  "headers": {
    "X-API-Key": "<kubi-api-key>"
  }
}
```

The double `/mcp` is: Ingress strips nothing (path-based routing preserves prefix), FastMCP serves at `/mcp` within the app. If this is awkward, we can configure FastMCP's `streamable_http_path` setting or use an Ingress rewrite.

#### Open Questions

1. **Path prefix**: Does FastMCP's `streamable_http_app()` support a configurable path, or is it always `/mcp`? If configurable, we could set it to `/` and let Ingress handle the `/mcp` prefix cleanly.
2. **Health endpoint**: Does the streamable HTTP app expose `/health` or similar? If not, we'll use a TCP check on 8007.
3. **Self-signed TLS**: Will Claude Code's HTTP MCP client accept self-signed certs? May need `NODE_TLS_REJECT_UNAUTHORIZED=0` or cert trust config.

#### Resolved: Open Questions

1. **Path prefix**: `streamable_http_path` IS configurable. Default `/mcp`. Ingress routes `/mcp` → backend, backend serves at `/mcp`. URL is simply `https://wip-kubi.local/mcp` — no double path.
2. **Health endpoint**: No built-in health endpoint. Using TCP socket probe on port 8007.
3. **Self-signed TLS**: TBD — needs testing with Claude Code.

#### Implementation

Built and deployed. Key issue encountered: MCP library's DNS rebinding protection rejects non-localhost Host headers (421 Misdirected Request). Fixed with `MCP_ALLOWED_HOST` env var.

```bash
# Build and push
bash k8s/build-images.sh --registry kubi5-1.local:32000 --push --builder podman --service mcp-server

# Deploy
cat k8s/services/mcp-server.yaml | ssh peter@kubi5-1 "/snap/bin/microk8s kubectl apply -f -"
cat k8s/ingress/ingress.yaml | ssh peter@kubi5-1 "/snap/bin/microk8s kubectl apply -f -"
```

**Verification:**

```bash
curl -sk -X POST https://wip-kubi.local/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "X-API-Key: <key>" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'
# → serverInfo: {name: "wip", version: "1.26.0"}, 69 tools, 4 resources
```

| Check | Result |
|-------|--------|
| Pod | 1/1 Running, kubi5-2 |
| Ingress | `/mcp` → `wip-mcp-server:8007` |
| MCP initialize | Full protocol response |
| Auth | API key validated |
| DNS rebinding | `wip-kubi.local` allowed |

**Claude Code `.mcp.json` entry:**
```json
"wip-kubi": {
  "type": "http",
  "url": "https://wip-kubi.local/mcp",
  "headers": {
    "X-API-Key": "<kubi-api-key>"
  }
}
```

**Lessons learned:**
15. MCP library v1.26.0 supports `streamable-http` transport via `mcp.streamable_http_app()` — drop-in replacement for `mcp.sse_app()`. Prefer HTTP over deprecated SSE.
16. MCP transport security validates Host headers (DNS rebinding protection). In K8s, set `MCP_ALLOWED_HOST` to the Ingress hostname. Without this, all requests get 421 Misdirected Request.
17. Port 8007 for MCP server (8006 = ingest-gateway, FastMCP default = 8000). Override via `MCP_PORT` env var.

#### Claude Code Connection: Issues & Fixes

**Issue 1: Self-signed TLS rejected by Node.js**

Node.js does NOT read the macOS system keychain. Even after `security add-trusted-cert`, Node.js still rejects self-signed certs.

**Fix:** Set `NODE_EXTRA_CA_CERTS` globally in `~/.bashrc` or `~/.zshrc`:
```bash
export NODE_EXTRA_CA_CERTS=/path/to/wip-kubi-tls.crt
```

The `env` field in `.mcp.json` does NOT work for HTTP transports (only stdio). For HTTP transports, the env var must be set on the Claude Code process itself.

**Issue 2: MCP server missing `WIP_API_KEY` for internal service calls**

The manifest had `API_KEY` (MCP server's own auth middleware) but not `WIP_API_KEY` (client calling WIP services). Result: 401 on every tool call.

**Fix:** Added `WIP_API_KEY` to `k8s/services/mcp-server.yaml`, sourced from the same `wip-secrets/api-key`.

**Issue 3: Session lost after pod restart**

Restarting the MCP pod invalidates the HTTP streamable session. Claude Code must reconnect via `/mcp` → Reconnect.

**Verification — full end-to-end:**

`list_namespaces` via `mcp__wip-kubi__list_namespaces` returned both `wip` and `seed` namespaces. All 69 tools operational over: Claude Code → HTTPS → Ingress → MCP pod → WIP services → MongoDB → back.

**Lessons learned:**
18. Node.js ignores the macOS keychain for TLS. Use `NODE_EXTRA_CA_CERTS` env var globally, not in `.mcp.json` `env` (only works for stdio transports).
19. MCP server needs TWO API key env vars: `API_KEY` (incoming auth) and `WIP_API_KEY` (outgoing calls to WIP services).

**Next:** Phase 6 (Network Policies) — optional hardening.
