# Building WIP backend images & deploying them to kb.internal

**Read this first, then ASK THE OPERATOR which route to take — do not self-educate.**
The whole point of this doc is to skip the grep-the-workflows / list-the-cluster /
check-the-token fumbling. Two build routes exist; the deploy (roll) step is shared.

> **Decision to put to the operator:**
> *"Build route A (GHCR dual-arch via GitHub Actions — canonical, ~25 min, true
> multi-arch) or route B (localhost native build via podman — faster, no QEMU,
> single-arch arm64 only)?"*
> Then follow that one section + the shared **Deploy** section.

---

## What runs where (so you don't re-derive it)

- **kb.internal** is the `microk8s` k8s cluster — 3 **arm64** Raspberry Pi nodes
  (`kubi5-1/2/3`). kubectl context: `microk8s`. Namespace: `kb`.
- It runs the **full WIP backend platform**, not just the KB app. Backend
  deployments (all on `ghcr.io/peterseb1969/<svc>:<tag>`):
  `wip-registry, wip-def-store, wip-template-store, wip-document-store,
  wip-reporting-sync, wip-auth-gateway, wip-ingest-gateway, wip-mcp-server`.
  Deployment is `wip-<svc>`, container is `<svc>`.
- **Do NOT touch in a backend roll:** `wip-router` (upstream `caddy:2`) and
  `wip-wip-kb` (the **KB app** — a *separate* repo, `WIP-KB`, built separately;
  see the prior BE-YAC ops notes). `wip-react-console` is the **frontend**, built
  from its own repo (`WIP-ReactConsole`) — **not** by this build path.
- **Image source = the `World-in-a-Pie` repo on GitHub** (`origin`,
  `peterseb1969/World-in-a-Pie`). Builds run on **GitHub Actions → GHCR**.
  `.gitea/workflows/` is *tests* (Gitea CI), **not** image builds — don't look
  there.
- `build-release.sh` builds exactly these **8 Python services** (no react-console,
  no KB app). They build via `pip`, so the historic **`npm ci` SIGILL (exit 132)
  QEMU flake was Node-specific** (KB app / react-console) and does **not** apply
  to this set. Generic QEMU slowness still does.

## Prerequisites (both routes)

- `gh auth status` shows scope **`write:packages`** (needed to push to GHCR).
  If missing: `gh auth refresh -s write:packages`.
- You are building the branch you intend — usually **`develop`**. Confirm
  `origin/develop` has the commits you want (`git fetch origin develop`).
- kubectl reaches the cluster: `kubectl --context microk8s -n kb get deploy`.
- **Route B only:** `podman machine list` shows the machine **running** (give it
  ≥6 GiB; bump if builds OOM).

## Tag convention

`YYYYMMDD<letter>` (e.g. `20260627a`, next same-day build `20260627b`).
**Write-once** — never re-push an existing tag; bump the letter. The currently
deployed set is the tag on the running deployments (`kubectl ... get deploy -o
wide`); pick the next unused letter for today.

---

## Route A — GHCR dual-arch (GitHub Actions) — *canonical*

True multi-arch (`amd64` + `arm64`) manifest lists. ~20–25 min. Required if any
consumer is amd64; always safe for the arm64 Pi cluster.

```bash
# 1. Dispatch the build on the branch you want (NOT the default branch).
gh workflow run "Build & Push Images (x86)" \
  --repo peterseb1969/World-in-a-Pie \
  --ref develop \
  -f tag=20260627a \
  -f platforms=linux/amd64,linux/arm64       # arm64 is MANDATORY for the Pi nodes

# 2. Find the run id, then watch it (gates the roll on success).
gh run list --repo peterseb1969/World-in-a-Pie \
  --workflow "Build & Push Images (x86)" --limit 1
gh run watch <run-id> --repo peterseb1969/World-in-a-Pie --exit-status
```

- `platforms` default is `linux/amd64` only — **you must pass both** for the Pi
  cluster, which switches the workflow to the podman+QEMU path.
- If QEMU flakes (generic, not the Node SIGILL — that doesn't hit these 8):
  re-run once; if it persists, switch to **Route B**.

## Route B — localhost native build (podman) → GHCR — *fallback, no QEMU*

Use when Route A is flaking or you want speed. On **Apple Silicon the host is
arm64 = the Pi node arch**, so a native build runs on the cluster.
**Caveat: single-arch arm64** — runs on the Pi cluster, **not** on amd64. If you
need amd64, use Route A or keep a multi-arch tag for rollback.

```bash
# 1. Log podman into GHCR with a write:packages token.
echo "$(gh auth token)" | podman login ghcr.io -u peterseb1969 --password-stdin

# 2. Build + push all 8 (native arch — NO --platforms = single-arch fast path).
bash scripts/build-release.sh \
  --registry ghcr.io/peterseb1969 \
  --tag 20260627a \
  --push --builder podman

#    …or one service only:
bash scripts/build-release.sh --registry ghcr.io/peterseb1969 \
  --tag 20260627a --push --builder podman --service template-store
```

---

## Deploy (roll) — shared by both routes

**Gate the roll on the build CONCLUDING success AND the tag existing in GHCR.**
(Prior incident: rolling to a not-yet-built tag → stuck rollout. RollingUpdate
held the old pod so there was no outage, but it had to be rolled back. Don't.)

```bash
# Verify the manifest is actually in GHCR before rolling (Route B example):
podman manifest inspect ghcr.io/peterseb1969/template-store:20260627a >/dev/null && echo OK

# Roll each backend deployment to the new tag (all 8, or just the changed subset).
for svc in registry def-store template-store document-store reporting-sync \
           auth-gateway ingest-gateway mcp-server; do
  kubectl --context microk8s -n kb set image \
    deployment/wip-$svc $svc=ghcr.io/peterseb1969/$svc:20260627a
done

# Wait for each rollout, then confirm pods are healthy.
for svc in registry def-store template-store document-store reporting-sync \
           auth-gateway ingest-gateway mcp-server; do
  kubectl --context microk8s -n kb rollout status deployment/wip-$svc --timeout=180s
done
kubectl --context microk8s -n kb get pods
```

- Roll only the services you rebuilt. For a code change in one component (e.g.
  CASE-515 touched `template-store` + `mcp-server`), rolling just those is enough,
  but keeping the whole set on one coherent tag is the cleaner default.
- **Rollback:** `set image` back to the previous tag (read it off
  `kubectl ... get deploy -o wide` before you roll — e.g. `20260625b`).
- **Verify the served code, not just "pod up":** hit a changed endpoint /
  `get_wip_status`, or check the image digest the pod actually pulled.

## Gotchas (the trail, so you trust the above)

- kb.internal = the **whole platform**, not just the KB app. Image source is
  **World-in-a-Pie on GitHub**, built by **GitHub Actions** (not Gitea).
- The **KB app** (`wip-wip-kb`) is a different repo (`WIP-KB`, **main-only**) with
  its own build — out of scope here.
- **react-console** is a separate frontend repo, not built by `build-release.sh`.
- Multi-arch is **mandatory** for the arm64 Pi nodes; an amd64-only build will not
  schedule there.
