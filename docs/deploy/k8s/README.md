<p align="center">
  <img src="../../images/WIP_logo_blue_small.png" alt="World In a Pie" width="160">
</p>

# WIP on Kubernetes (GHCR images, one deployer spec)

Deploy WIP to a Kubernetes cluster with `wip-deploy --target k8s` — the same spec layer as compose, rendered to k8s manifests and applied to your **current kubectl context**. Public GHCR images; no image builds.

> **Not deploying to a cluster?** Single host → **[podman quickstart](../podman/README.md)**. Hot-reload development → **[dev guide](../dev/README.md)**.

> **Status:** validated end-to-end on microk8s — a fresh install brought up all pods, the React Console + WIP-KB apps, an nginx ingress on the FQDN, and `/api/registry/health` answering through it. The upgrade/roll guidance (§7) comes from real cluster rolls done in this project.

---

## What you'll end up with

- WIP's services + infra as Deployments/StatefulSets in a namespace of your choice, images pulled from `ghcr.io/peterseb1969/*`
- An Ingress exposing WIP at your hostname over HTTPS (self-signed by default)
- Optional apps (React Console at `/apps/rc/`, WIP-KB at `/apps/kb/`)
- A starter API key + Dex users, stored per the secrets backend

---

## Prerequisites

| Requirement | Notes |
|---|---|
| A **working cluster** + `kubectl` **context pointed at it** | `wip-deploy` applies to the **active context** — check `kubectl config current-context` before every install. |
| A **StorageClass** (RWO PVCs) | For MongoDB / PostgreSQL / MinIO / NATS. Get the name from `kubectl get storageclass`. |
| An **IngressClass** + a working ingress controller | e.g. `nginx`. Get the name from `kubectl get ingressclass`. |
| An **FQDN that resolves to the ingress** | Point DNS at the ingress controller's external IP / load balancer (see the hostname note in step 5). |
| **git** + **Python 3.11+** | For the repo and the `wip-deploy` CLI. |

Images are **public** (no GHCR login). **On arm64 nodes (Raspberry Pi clusters) you must pin a multi-arch tag** — an amd64-only tag won't schedule.

<details>
<summary><b>Example: microk8s on arm64 Pis</b></summary>

```bash
# On the cluster: enable the pieces WIP needs
microk8s enable dns ingress hostpath-storage    # or a real storage addon (e.g. rook-ceph) for production

# Get a kubectl context on your workstation (or use `microk8s kubectl`)
microk8s config >> ~/.kube/config     # then `kubectl config use-context microk8s`

# Read the actual class names to pass to wip-deploy:
kubectl get storageclass        # e.g. microk8s-hostpath (or rook-ceph-block)
kubectl get ingressclass        # e.g. public / nginx
```
Namespaces must match `^[a-z][a-z0-9-]*$` — a name starting with a digit (e.g. `2606`) is rejected; use `wip-2606`.
</details>

---

## 1. Get the deployer CLI

```bash
git clone https://github.com/peterseb1969/World-in-a-Pie.git
cd World-in-a-Pie
git switch develop
python3 -m venv .venv && source .venv/bin/activate
pip install -e deployer
wip-deploy --help
```

## 2. Point kubectl at the target cluster

```bash
kubectl config current-context     # ← wip-deploy install applies HERE
kubectl get nodes                  # sanity: reachable, and note the node arch
```

## 3. Choose an image tag

Same rules as the [podman quickstart, step 2](../podman/README.md#2-choose-an-image-tag): immutable `YYYYMMDD<letter>` tags, **no `latest`**, and **multi-arch matters** — arm64 nodes need a tag built for `arm64`. Check on the [GitHub Packages page](https://github.com/peterseb1969?tab=packages). **Recommended multi-arch default: `20260628a`.**

## 4. Render and inspect first

k8s applies to a live cluster — look before you leap:

```bash
wip-deploy render --target k8s --namespace wip --hostname wip.example.internal \
  --registry ghcr.io/peterseb1969 --tag 20260628a --output-dir /tmp/wip-k8s
# review /tmp/wip-k8s: image tags, the Ingress host, storageClassName, the TLS Secret
```

## 5. Install

```bash
wip-deploy install \
  --target k8s \
  --namespace wip \
  --hostname wip.example.internal \
  --registry ghcr.io/peterseb1969 \
  --tag 20260628a \
  --storage-class <your-storageclass> \
  --ingress-class <your-ingressclass> \
  --app react-console --image-tag react-console=20260629a \
  --app wip-kb --image-tag wip-kb=20260629a
```

- **`--hostname` must be an FQDN that resolves to your ingress.** It's baked into the **Ingress host, the TLS cert SAN, and the Dex OIDC issuer** — all three must agree. A bare or non-resolving host produces the classic failure trio: `404`s, Dex redirecting to the wrong host, and cert-SAN mismatches. Point DNS at the ingress controller first.
- **`--namespace`** must match `^[a-z][a-z0-9-]*$` (no leading digit).
- **TLS** is self-signed by default (the deployer generates a cert + a `Secret` named `wip-tls`; override with `--tls-secret-name`, or `--tls external` to bring your own). Browsers/`curl` need `-k` or CA trust.
- **Apps** work exactly as in the [podman guide](../podman/README.md#installing-apps-react-console-wip-kb): `--app <name>` + `--image-tag <name>=<tag>` (key = the image name, e.g. `wip-kb`; a mismatched key is silently dropped).
- It applies to the **active kubectl context** — re-confirm step 2.

## 6. Verify

```bash
kubectl -n wip get pods                 # all Running / Ready
kubectl -n wip get ingress              # host + address present
curl -k https://wip.example.internal/api/registry/health
# → {"status":"healthy","database":"connected","auth_enabled":true}
```

Then open **`https://wip.example.internal/apps/rc/`** and **`/apps/kb/`**. (WIP-KB offers to bootstrap its namespace on first load — accept it.)

---

## Apps (React Console, WIP-KB) — optional

The step-5 command enables both UIs. They're **optional** — drop the `--app`/`--image-tag` lines for a headless API/MCP backend, or add just the one(s) you want:

| App | `--app` / `--image-tag` name | URL |
|---|---|---|
| React Console (admin UI) | `react-console` | `https://<host>/apps/rc/` |
| WIP-KB (knowledge base) | `wip-kb` | `https://<host>/apps/kb/` |

Each app is a separate image with its own tag, so pair every `--app <name>` with `--image-tag <name>=<tag>`. The key is the **image name** (`wip-kb`, **not** `kb`; a mismatched key is silently dropped), and on arm64 nodes the app tag must be multi-arch too. Backend-only, then RC and KB added:

```bash
# headless (no UI):
wip-deploy install --target k8s --namespace wip --hostname wip.example.internal \
  --registry ghcr.io/peterseb1969 --tag 20260628a \
  --storage-class <sc> --ingress-class <ic>

# + React Console and WIP-KB (append to the same command):
  --app react-console --image-tag react-console=20260629a \
  --app wip-kb        --image-tag wip-kb=20260629a
```

Same behaviour and caveats as the [podman guide's apps section](../podman/README.md#installing-apps-react-console-wip-kb).

---

## 7. Upgrading / rolling images

**The clean, scoped way is `kubectl set image`** — gated on the tag existing in GHCR **for your node arch**. Deployment is `wip-<svc>`, container is `<svc>`; apps are `wip-<app>` / `<app>` (e.g. `wip-react-console` / `react-console`, `wip-wip-kb` / `wip-kb`).

```bash
# 1) confirm the tag exists (Packages page, or skopeo):
skopeo inspect docker://ghcr.io/peterseb1969/registry:<newtag> >/dev/null && echo OK

# 2) roll the services you rebuilt (all 8, or a subset):
for svc in registry def-store template-store document-store reporting-sync auth-gateway ingest-gateway mcp-server; do
  kubectl -n wip set image deployment/wip-$svc $svc=ghcr.io/peterseb1969/$svc:<newtag>
done
for svc in registry def-store template-store document-store reporting-sync auth-gateway ingest-gateway mcp-server; do
  kubectl -n wip rollout status deployment/wip-$svc --timeout=180s
done
```

> ⚠️ **Prefer `kubectl set image` for a scoped roll.** On k8s, `wip-deploy app-deploy` and `wip-deploy install` **re-render the whole spec** from the install's saved state and apply *everything* — and if that saved base tag has drifted (or was deleted from GHCR), they'll re-target images that no longer exist and the rollout stalls (RollingUpdate keeps the old pods, so no outage, but you have to `kubectl rollout undo`). If you manage the install through `wip-deploy`, keep its saved tag in sync with what's actually live.

**Roll back a bad roll:** `kubectl -n wip rollout undo deployment/wip-<svc>`.

---

## 8. Day-2 operations

```bash
wip-deploy status --name wip            # what's deployed
wip-deploy stop   --name wip            # scale workloads to 0 (keeps objects + data)
wip-deploy start  --name wip            # scale back up
wip-deploy nuke   --name wip            # tear down (keeps PVCs + secrets by default)
kubectl -n wip rollout undo deployment/wip-<svc>   # revert a bad image roll
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `404` / Dex redirects to the wrong host / cert-SAN mismatch | `--hostname` isn't the FQDN that resolves to the ingress. Reinstall with the correct FQDN — it drives ingress host **+** TLS SAN **+** Dex issuer. |
| `ImagePullBackOff` | The tag doesn't exist **for the node arch** (arm64 needs multi-arch). Check the [Packages page](https://github.com/peterseb1969?tab=packages); roll to a tag that exists. |
| Namespace rejected | Must match `^[a-z][a-z0-9-]*$` — no leading digit (`2606` → `wip-2606`). |
| Pods `Pending` on PVCs | Wrong/absent `--storage-class`. `kubectl get storageclass`; reinstall with a valid RWO class. |
| Applied to the wrong cluster | `wip-deploy install` uses the active `kubectl` context — always check `kubectl config current-context` first. |
| Health `200` with empty body | Unrouted path — use the exact `/api/registry/health`. |

---

## What this guide leaves out

- **Single host** (podman) → the [quickstart](../podman/README.md); **hot-reload dev** → the [dev guide](../dev/README.md).
- **Hardening** — Let's Encrypt / external TLS, per-service keys, secrets backends: `docs/wip-guide.md` §7.
</content>
