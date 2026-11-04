# WIP on Kubernetes — what runs where, and how the two clusters talk

**Audience:** an agent (or person) who knows nothing about this deployment. Read sections 1–3 before touching either cluster.
**State described:** 2026-11-04, checked live with read-only `kubectl` over VPN (see §10 for what was and was not checked).
**Not this:** the local podman stack on a laptop (`wip-deploy install --target dev`, containers named `wip-*`, install dir `~/.wip-deploy/wip-local`) is a different thing. Nothing here applies to it, and it is not reachable from either cluster.

Each fact carries a tag: **[live]** read from the running cluster on 2026-11-04; **[export]** read from a manifest export dated 2026-10-30; **[notes]** recorded by an earlier session and not re-checked; **[inferred]** reasoned, not tested.

---

## 1. At a glance

WIP runs on **two separate Rancher-managed Kubernetes clusters** that share **no pod network**. The only link between them is public-ingress HTTPS.

| | On-prem cluster | AWS cluster |
|---|---|---|
| kubectl context | `stgemeaworkloads` | `caasawsqa` |
| Namespace | `pbs-rancher-dev` | `pbs-app-dev` |
| Role | the WIP backend: services and datastores | the apps, their login, and the auth gateway |
| Public host | `wip.stgcaas-gw.emea.roche.com` | `wip-apps.caasqarcp.roche.com` |
| Ingress | Traefik, plain HTTP routing, no TLS block | Traefik with a `forwardAuth` middleware |

```
Browser ──https──> wip-apps.caasqarcp.roche.com           [AWS Traefik, TLS ends here]
                     ├ /dex   ──> Dex (login)
                     ├ /auth  ──> auth-gateway
                     └ /apps/* ──forwardAuth(auth-gateway)──> app pod

App pod ──https + X-API-Key──> wip.stgcaas-gw.emea.roche.com  [on-prem Traefik]
                     ├ /api/<service> ──> wip-<service>:800x
                     └ /mcp           ──> wip-mcp-server:8007   (own Ingress + prefix-strip middleware)

on-prem pods ──cluster DNS──> wip-mongodb:27017, wip-postgres:5432, wip-nats:4222
```

Three facts to keep in your head:

1. **Apps live on AWS, data lives on-prem.** Every app request for data crosses from AWS to on-prem over the public ingress, carrying an API key.
2. **The on-prem cluster has no auth gateway, no Dex, no Caddy router.** Each backend service checks the API key itself.
3. **Login lives only on AWS.** Dex and the auth gateway exist only there. On-prem services never see a user token.

---

## 2. Before you touch anything

### 2.1 You must be on the VPN
Without the VPN, `kubectl` fails with `x509: certificate signed by unknown authority` on both contexts, which looks like a kubeconfig/CA problem and is not. Switch the VPN on and retry before debugging anything else. [live: failed off-VPN, worked on-VPN, same file]

### 2.2 Access
```bash
export KUBECONFIG=~/Development/Rancher/.secrets/kubeconfig-merged.yaml
```
- Tokens last about 30 days. They were issued 2026-10-29. When both clusters fail at once with `system:unauthenticated`, the tokens have expired: download fresh kubeconfigs from the Rancher UI (they land in `~/Downloads/<cluster> (1).yaml`, names contain spaces) and re-merge. [notes]
- Always pass an explicit `--context`. There is no sensible default.

### 2.3 Shell pitfalls on this machine (zsh, macOS)
- **zsh does not split variables into words.** `KC="kubectl --context x -n y"; $KC get pods` fails. Use shell functions:
  ```bash
  op() { perl -e 'alarm 40; exec @ARGV' kubectl --context stgemeaworkloads -n pbs-rancher-dev "$@"; }
  aw() { perl -e 'alarm 40; exec @ARGV' kubectl --context caasawsqa       -n pbs-app-dev      "$@"; }
  ```
- **There is no `timeout` command on macOS.** The `perl -e 'alarm N; exec @ARGV'` wrapper above bounds a call.
- **Quote `custom-columns=...[0]...` arguments**, or zsh treats `[0]` as a glob and fails with `no matches found`.
- **Do not use `kubectl exec` to download large files.** Output was silently truncated on a 102 MB archive with exit code 0. Use `kubectl port-forward` plus curl and compare against the job's reported archive size. [notes]

### 2.4 Secrets
- Never print a key, token, password or hash. Refer to Secrets by name and key only. Do not run `kubectl get secret -o yaml`.
- `docs/rancher-deployment.md` (untracked) contains an **API key in plain text**. Scrub it before it is ever committed.

### 2.5 Start read-only
`get`, `describe`, `logs --tail`, `rollout history`, `diff`. Nothing in this document requires a write to understand the system.

---

## 3. On-prem cluster (`stgemeaworkloads` / `pbs-rancher-dev`)

### 3.1 Workloads [live]
All WIP service images come from `ghcr.io/peterseb1969/<name>:20261026b`. Every workload is **one replica** [live]; the Deployments use the `Recreate` strategy [export — the strategy field was not re-read live].

| Workload | Kind | Image | Port | CPU limit | Mem limit | Notes |
|---|---|---|---|---|---|---|
| wip-registry | Deployment | registry | 8001 | 350m | 1Gi | identity authority |
| wip-def-store | Deployment | def-store | 8002 | 150m | 1Gi | terminologies, terms |
| wip-template-store | Deployment | template-store | 8003 | 350m | 1Gi | |
| wip-document-store | Deployment | document-store | 8004 | 1 | 4Gi | big because of backup/restore |
| wip-reporting-sync | Deployment | reporting-sync | 8005 | 400m | 1Gi | MongoDB → PostgreSQL; reporting SQL |
| wip-mcp-server | Deployment | mcp-server | 8007 | 100m | 1Gi | |
| wip-mongodb | StatefulSet | `mongo:7` | 27017 | 800m | 4Gi | PVC 8Gi |
| wip-postgres | StatefulSet | `postgres:16` | 5432 | 500m | 1Gi | PVC 6Gi |
| wip-nats | StatefulSet | `nats:2-alpine` | 4222 | 200m | 512Mi | JetStream, PVC 2Gi |

CPU/memory limits are from the repo's patch script [export] and match the live quota arithmetic [live]. CPU requests are 50m each, except mongodb 250m, postgres 250m, nats 50m. Storage class for the PVCs is `pxcsi-l1-xfs-db-sc` (Portworx). [export]

All Services are `ClusterIP`. Nothing is exposed except through Ingress. [live]

### 3.2 Not deployed [live / notes]
No Caddy router, no auth-gateway, no Dex, no ingest-gateway, no Garage/MinIO (so no file storage), and **no NetworkPolicies**. The router, a root ingress, the network policies and the Namespace object were rendered by `wip-deploy` but deliberately held back (§7.1).

### 3.3 Ingress [live]
Host `wip.stgcaas-gw.emea.roche.com`, class `traefik`, **no TLS block** — TLS ends upstream of the cluster.
- `wip-ingress` routes `/api/def-store`, `/api/document-store`, `/api/registry`, `/api/reporting-sync`, `/api/template-store` directly to the matching Service. The path prefix appears to be preserved (the services mount at `/api/<service>`) [inferred; no prefix-strip middleware exists for these routes].
- `wip-mcp-ingress` routes `/mcp` to `wip-mcp-server`, through the Traefik `Middleware` `strip-mcp-prefix`. `/mcp` is deliberately absent from `wip-ingress`.
- The ingress carries nginx-style annotations (body-size, timeouts). Traefik most likely ignores them, which fits the documented failure of large uploads through it. [inferred]

### 3.4 Quota — the constraint that shapes everything [live]
One project-level ResourceQuota (`default-q7q98`), no LimitRange:

| Resource | Used | Hard | Spare |
|---|---|---|---|
| limits.cpu | 3850m | 4 | 150m |
| limits.memory | 14848Mi (14.5Gi) | 16Gi | 1.5Gi |
| requests.cpu | 850m | 1 | 150m |
| requests.storage | 16Gi | 20Gi | 4Gi |

Consequences: **a second pod of any service cannot start** (hence `Recreate`), every pod must declare CPU and memory limits, and any increase for one workload must be paid for by shrinking another. Do the arithmetic before changing a limit.

### 3.5 Intra-cluster traffic [export / live]
- Service-to-service HTTP uses full cluster DNS, e.g. `http://wip-registry.pbs-rancher-dev.svc.cluster.local:8001`.
- **Mongo:** `mongodb://wip-mongodb.pbs-rancher-dev.svc.cluster.local:27017/`; authentication on, one user and password per service.
- **NATS:** `nats://wip-nats...:4222`; per-service users. Used by def-store, document-store, template-store and reporting-sync (events → reporting-sync).
- **PostgreSQL:** reporting-sync only, with separate owner, read and sync-owner roles.
- **Auth:** `WIP_AUTH_MODE=api_key_only` on registry, def-store, template-store, document-store and reporting-sync. mcp-server sets no mode and calls the five services over in-cluster HTTP with the master key. [live env]
- On-prem JWT/OIDC settings in the manifests point at a Dex that does not exist on this cluster. They are inert in `api_key_only` mode. [export]

### 3.6 Restart history [live]
registry, template-store and document-store each show 2 restarts, all on 2026-10-28 at about 13:15 (registry and template-store exit 137, document-store exit 0). Nothing has recurred in 7 days. The cause was not established; only termination state was read, not logs.

---

## 4. AWS cluster (`caasawsqa` / `pbs-app-dev`)

### 4.1 Workloads [live]
| Workload | Image | Port | Notes |
|---|---|---|---|
| wip-auth-gateway | `ghcr.io/peterseb1969/auth-gateway:20261026b` | 4180 | `AUTH_REDIRECT_MODE=redirect` (required for Traefik) |
| wip-dex | `ghcr.io/dexidp/dex:v2.45.0` | 5556 | static users in a ConfigMap; 1Gi PVC on Portworx |
| wip-react-console | `ghcr.io/peterseb1969/react-console:20261030a` | 3011 | admin UI |
| wip-brmri-explorer | `ghcr.io/peterseb1969/brmri-explorer:20261029b` | 3001 | |
| wip-pbs-clintrial | `ghcr.io/roche-sandbox/pbs-clintrial:20261102a` | 3014 | different registry org |

One replica each; all Services `ClusterIP`. Tags change when people deploy — re-read them, do not trust this table for tags.

### 4.2 Ingress [live]
Host `wip-apps.caasqarcp.roche.com`, class `traefik`.
- `wip-apps-ingress`: `/apps/rc` → react-console:3011, `/apps/brmri` → brmri-explorer:3001, `/apps/pbs-clintrial` → pbs-clintrial:3014, all behind the `wip-forward-auth` Middleware. [middleware attachment: export]
- `/apps/clintrial` → `wip-clintrial:3001` — **a dead route: that Service does not exist.**
- `wip-auth-ingress`: `/dex` → Dex, `/auth` → auth-gateway. Not behind forward-auth.

### 4.3 Configuration the apps carry [live]
| App | WIP URL variable | TLS verify | Other |
|---|---|---|---|
| react-console | `WIP_BASE_URL=https://wip.stgcaas-gw.emea.roche.com` | **off** (`NODE_TLS_REJECT_UNAUTHORIZED=0`) | |
| brmri-explorer | `WIP_API_URL=` same host | **off** | |
| pbs-clintrial | `WIP_BASE_URL=` same host | **off** | `WIP_NAMESPACE=pbs-clintrial`, `WIP_REF_NAMESPACE=pbs-clintrial-ref` |

All three read `WIP_API_KEY` from **one secret: `wip-secrets`, key `api-key`**. [live] That key was patched from the on-prem master key on 2026-10-28. [notes] So **the AWS apps hold the master key, not scoped per-app keys.** The per-app-key model exists in the deployer, but it has not been applied to this cluster.

---

## 5. How traffic actually flows

### 5.1 Between the clusters
**Only HTTPS from an AWS app pod to the on-prem public ingress.** No manifest declares any cross-cluster pod-to-pod link, mesh, VPN or private-link route for WIP traffic. [export/live] Nothing on-prem calls AWS at runtime. [export] The in-cluster name `wip-router.pbs-rancher-dev.svc.cluster.local` is **unreachable from AWS**; apps must use the external URL. [notes] Whether AWS pods can resolve and reach `wip.stgcaas-gw.emea.roche.com` is demonstrated by the apps working, but DNS/egress/firewall rules were not inspected. [inferred]

Because TLS verification is off on this hop, a wrong or intercepted certificate would not be noticed. That is a known weakness, not a feature.

### 5.2 Request paths, hop by hop

**(a) A browser opens an app** (`https://wip-apps.caasqarcp.roche.com/apps/pbs-clintrial/...`)
1. AWS Traefik terminates TLS.
2. The `wip-forward-auth` middleware calls the auth-gateway at `http://wip-auth-gateway.pbs-app-dev.svc:4180/auth/verify`.
3. No session → the gateway answers with a redirect (redirect mode) to Dex at `/dex`; the user logs in; Dex returns to `/auth/callback`.
4. With a session, the gateway returns identity headers (`X-WIP-User`, `X-WIP-Groups`, `X-API-Key`) and Traefik forwards the request to the app Service. **Note:** gateway images built after CASE-1050 no longer return `X-API-Key` (the install's master key). Any new hand-built setup must list only `X-WIP-User` and `X-WIP-Groups` in the middleware's `authResponseHeaders`, and must never give an app the gateway's key.

**(b) An app calls a WIP service** (e.g. document-store, reporting SQL)
1. The app sends `https://wip.stgcaas-gw.emea.roche.com/api/<service>/...` with `X-API-Key`.
2. On-prem Traefik routes the path to `wip-<service>:800x` (no gateway, no Caddy).
3. The service validates the API key itself.

**(c) MCP**
Client → `https://wip.stgcaas-gw.emea.roche.com/mcp` → `wip-mcp-ingress` (prefix stripped) → `wip-mcp-server:8007` → the five services over in-cluster HTTP with the master key. The documented client URL is `/mcp/mcp`; **not verified**.

**(d) OIDC login**
Dex runs only on AWS. The public issuer is `https://wip-apps.caasqarcp.roche.com/dex` (what the browser sees). The auth-gateway fetches discovery and signing keys from the in-cluster issuer `http://wip-dex.pbs-app-dev.svc.cluster.local:5556/dex`. The two issuer URLs are deliberately different. On-prem services do not validate tokens.

---

## 6. Identity and secrets model

| Thing | Where | Notes |
|---|---|---|
| Master API key (on-prem) | on-prem generated Secrets | regenerated on the 2026-10-28 rebuild |
| Same key, AWS copy | `pbs-app-dev` / `wip-secrets` / `api-key` | used by all three apps |
| Other keys in `wip-secrets` | `dex-client-wip-gateway`, `gateway-session-secret`, `anthropic-api-key`, `jira-token`, `JIRA_URL` | names only |
| Dex users | ConfigMap `wip-dex-config` (AWS) | a copy of its contents is the only record of who can log in; export before any change |
| Per-service Mongo/NATS/Postgres credentials | on-prem Secrets | one set per service |

When the on-prem master key changes, the AWS `wip-secrets/api-key` must be changed with it, and the three app pods restarted, otherwise their data calls should start failing with 401. [inferred from how the secret is wired; it matches what happened on 2026-10-28, when the AWS secret was patched from the new on-prem key]

---

## 7. Operating this safely

### 7.1 Changing the on-prem cluster
Do **not** apply a raw `wip-deploy render` output. The k8s renderer cannot yet express what this cluster needs, so a patch script sits in between:

- `~/.wip-deploy/onprem-fresh/` (mode 700): `render/` (untouched output with the generated secrets), `patch_render.py`, `patched/` (what is applied), `held-back/` (rendered but deliberately not applied), plus `*.pre-optionB` copies from before the last CPU change.
- The script adds: CPU/memory requests and limits (the quota requires them); smaller PVCs (8/6/2 Gi; the render asks for 20/20/5 and the quota is 20); `Recreate` strategy; numeric `securityContext` values (the cluster enforces `runAsNonRoot` and cannot verify named users — WIP images run as 1000, Mongo and Postgres as 999, with `fsGroup` on data volumes); no TLS block on the ingress; and `/mcp` removed from `wip-ingress` because `wip-mcp-ingress` serves it.
- Flow: re-render → `python3 patch_render.py` → review `patched/` → `kubectl apply -R -f patched` (after checking the quota arithmetic in §3.4).
- Rollback material from the 2026-10-28 rebuild: `~/Documents/wip-onprem-backup-20261028/` (old manifests, old `wip-secrets` of both clusters, a verified BRMRI backup).

### 7.2 Restarting and deploying
- The quota cannot hold two pods of one service. The recorded pattern is **scale to 0, wait for termination, scale to 1**, and never `kubectl rollout restart` (it tries to run two pods and gets stuck). [notes] The on-prem Deployments now use `Recreate`, which should avoid the problem, but that has not been tested here; use scale-down/up until it has.
- A StatefulSet pod stuck in `CreateContainerConfigError` is **not** replaced when you fix the template. Delete the pod. [notes]
- After any restart of a single-replica service, that service is down until the new pod is ready. There is no redundancy anywhere.

### 7.3 Changing the AWS cluster
- A cleaned export of the running namespace is in `~/.wip-deploy/aws-apps-live-20261030/` (Deployments, Services, Ingress, middleware, Dex PVC; the Dex ConfigMap is in `sensitive/`, mode 600; Secrets are listed by name only in `required-secrets.txt`). Use `kubectl --context caasawsqa diff -R -f <dir>/<sub>` to see drift. It was empty on 2026-10-30; it is **no longer** (pbs-clintrial and react-console tags have since moved).
- **Never apply older manifests over this namespace.** The last-applied annotations date from June–October; an apply would revert the image tags.
- Fix an image tag on the **Deployment**, not on a running pod. A tag fixed on a pod reappears broken at the next rollout. Check with `kubectl get deploy <n> -o jsonpath='{.spec.template.spec.containers[0].image}'`. `kubectl rollout history deployment/<n> --revision=N` shows which apply reverted a hand fix. [notes]
- A Rancher "Redeploy" only adds a `cattle.io/timestamp` annotation (a new revision with no real change). [notes]

### 7.4 Large data and restores
- Archives over about 1 GB cannot be uploaded through the React Console via Traefik (timeout/body size). Copy the archive into the document-store pod with `kubectl cp` and call `localhost:8004` from inside the pod. [notes]
- Data restored from before 2026-10-12 has stale identity hashes. Run `POST /api/document-store/maintenance/rehash-identities` (body `{namespace, dry_run}`; **dry run is the default**), then rebuild the affected reporting tables. [notes]
- PostgreSQL JIT is turned off at database level (`ALTER DATABASE wip_reporting SET jit = off`). It lives in the database catalog, survives a pod restart, but **a fresh install, a rebuild or a restore into a new database does not carry it**. Re-apply it. [notes]

---

## 8. Known issues and weak spots

1. **Every workload is a single replica, including all three datastores.** Any restart is an outage for that service.
2. **Quota headroom is 150m CPU and 1.5Gi memory.** Nothing can grow without something else shrinking.
3. **AWS apps hold the master key.** A compromise of any app pod's environment is a compromise of the whole backend.
4. **TLS verification is off** on the AWS → on-prem hop for all three apps.
5. **No NetworkPolicies** on-prem.
6. **`/apps/clintrial` is a dead route** on the AWS ingress.
7. **The on-prem ingress has nginx-style annotations on a Traefik controller** — probably ignored [inferred].
8. **Portworx volumes:** the AWS Dex pod was briefly unschedulable on 2026-10-29 ("No node found with storage driver") during a node rotation and recovered by itself; cause not established. [notes]
9. **`docs/rancher-deployment.md` is stale and contains a plaintext key.** Its image tags (`v2.1.0`), the Caddy router on-prem and `brmri` at `20260702a` no longer match the cluster. Prefer this document.
10. **The two clusters can drift from the repo.** The repo's app manifests describe `wip-deploy` installs; the AWS cluster was assembled by hand and Rancher UI edits.

### Shelved idea — do not start without being asked
Split MongoDB, NATS and PostgreSQL into a separate Rancher project/namespace (4 CPU, 20 GB) to relieve the quota. It would add 9 cross-project pod-to-pod links and require rewriting 11 hostnames in `patch_render.py`. Unknown: whether Rancher project network isolation blocks traffic between projects. [notes]

---

## 9. How to re-verify this document (all read-only, VPN on)

```bash
export KUBECONFIG=~/Development/Rancher/.secrets/kubeconfig-merged.yaml
op() { perl -e 'alarm 40; exec @ARGV' kubectl --context stgemeaworkloads -n pbs-rancher-dev "$@"; }
aw() { perl -e 'alarm 40; exec @ARGV' kubectl --context caasawsqa       -n pbs-app-dev      "$@"; }

op get deploy,sts,svc,ingress --no-headers
op get pods -o 'custom-columns=N:.metadata.name,READY:.status.containerStatuses[0].ready,RST:.status.containerStatuses[0].restartCount,IMG:.spec.containers[0].image' --no-headers
op get resourcequota -o jsonpath='{range .items[*]}{.status.used}{"\n"}{.status.hard}{"\n"}{end}'
op get middleware.traefik.io,networkpolicy --no-headers

aw get deploy,svc,ingress --no-headers
aw get ingress -o jsonpath='{range .items[*].spec.rules[*].http.paths[*]}{.path}{" -> "}{.backend.service.name}{":"}{.backend.service.port.number}{"\n"}{end}'
aw get deploy wip-pbs-clintrial -o jsonpath='{range .spec.template.spec.containers[0].env[*]}{.name}{"="}{.value}{"\n"}{end}' | grep -E '^(WIP_BASE_URL|WIP_API_URL|NODE_TLS|WIP_NAMESPACE|WIP_REF_NAMESPACE)='
```
When this document and the cluster disagree, **the cluster is right**; correct the document.

---

## 10. What was and was not checked (2026-11-04)

**Checked live, read-only:** workloads, images, replicas, restart counts and reasons, Services, Ingress paths, Traefik Middleware names, the ResourceQuota, NetworkPolicy absence, `WIP_AUTH_MODE` on the five on-prem services, selected literal env values and the Secret *names* behind `WIP_API_KEY` on the three AWS apps, and the auth-gateway's issuer settings.

**Not checked:** DNS, egress and firewall rules between AWS pods and the on-prem host (needs exec or a probe pod); that the Middleware is actually attached to the app routes (taken from the export); the Dex ConfigMap against any document; the `/mcp/mcp` client URL; runtime logs of cross-cluster traffic; PVC details; Rancher project network isolation; container logs for the 2026-10-28 restarts; the content of any Secret (deliberately).
