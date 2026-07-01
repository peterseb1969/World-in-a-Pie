<p align="center">
  <img src="../../images/WIP_logo_blue_small.png" alt="World In a Pie" width="160">
</p>

# WIP Dev Deployment — Hot-Reload (podman, local source)

Run WIP in **hot-reload development mode** on one machine: the **backend runs from this repo's source** (bind-mounted, `uvicorn --reload`) and **apps run from your local checkouts** (`--app-source`, built from each app's `Dockerfile.dev`, source bind-mounted). Edit code, see it live — no image pulls, no tags.

> **Just want to *run* WIP (not develop it)?** Use the **[single-host quickstart (podman + GHCR images)](../podman/README.md)** instead — that's the production-style path.
>
> **Kubernetes:** [k8s guide](../k8s/README.md).

> **Status:** the clone → install → both-apps-up flow below is **validated** end-to-end (macOS, fresh checkouts). The hot-reload loop, `rebuild`, and the in-container env values (§6–§8) are from the deployer docs and not yet re-confirmed on this exact stack — treat those as the parts to verify.

---

## What you'll end up with

- The WIP backend running from **this repo's `components/*/src`** (bind-mounted, hot-reloads on edit)
- Any apps you pass via `--app-source`, each built from its **local checkout** and hot-reloading on edit (e.g. React Console at `/apps/rc/`, WIP-KB at `/apps/kb/`)
- HTTPS via Caddy on `https://localhost:8443` (self-signed)
- A starter API key + Dex users, on disk under `~/.wip-deploy/wip/`

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **podman** + **podman-compose**, running | `podman info` should succeed (macOS: `podman machine start`, ≥4 GB). |
| **git** + **Python 3.11+** | For this repo and the `wip-deploy` CLI. |
| **This repo** (`develop`) | Its `components/*/src` is what the backend bind-mounts and hot-reloads. |
| **A local clone of each app** you want to run | e.g. `WIP-RC` (the React Console), `WIP-KB`. App builds happen *inside* containers — **no Node needed on the host** (confirmed). |

> The examples below assume the app clones sit **next to** `World-in-a-Pie` (all under one `Development/` folder), so `--app-source` can use relative paths like `../WIP-RC`. Absolute paths work too.

---

## 1. Get the deployer CLI

```bash
git clone https://github.com/peterseb1969/World-in-a-Pie.git
cd World-in-a-Pie
git switch develop            # (or: git checkout develop)

python3 -m venv .venv
source .venv/bin/activate     # wip-deploy is on PATH while the venv is active
pip install -e deployer
wip-deploy --help
```

## 2. Clone the apps you want to run

Clone each app repo **next to `World-in-a-Pie`** (so the relative paths below work):

```bash
cd ..                                                   # back to your Development/ folder
git clone https://github.com/peterseb1969/WIP-RC        # the React Console (admin UI)
git clone https://github.com/peterseb1969/WIP-KB        # the WIP-KB knowledge base
cd World-in-a-Pie
```

**The `--app-source` path is the *app directory*, which isn't always the repo root** — some repos keep the app in a subdirectory (`WIP-ClinTrial/clintrial-explorer`, `WIP-DnD/apps/dnd-compendium`); `WIP-RC` and `WIP-KB` are at their repo root. Wrong level → a bare `Dockerfile not found in <path>`.

## 3. Check each app is deployable (before wiring it in)

```bash
wip-deploy check-app-deployability <path-to-app-dir>
```

Verifies the app satisfies the deployable-app contract (`Dockerfile.dev` at that path, vite binds `0.0.0.0`, a `dev` script, a discoverable manifest, …). Fix anything it flags before the install.

## 4. Install the dev stack

Full stack — backend (from this repo) + React Console + WIP-KB, all from local source:

```bash
wip-deploy install \
  --preset standard \
  --target dev \
  --name wip \
  --hostname localhost \
  --app-source react-console=../WIP-RC \
  --app-source wip-kb=../WIP-KB
```

On success you'll see something like `Services: 13 active, 2 apps` … `✓ Install complete. https://localhost:8443`.

- `--target dev` — bind-mounts backend `src/` + `libs/wip-auth/src` (read-only) and runs `uvicorn --reload`; builds + force-recreates on install and clears stale `wip-*` containers first.
- `--app-source NAME=PATH` — `NAME` is the app name (`react-console`, `wip-kb`); `PATH` is its app directory. The app is built from that checkout's `Dockerfile.dev`, its source bind-mounted **rw**, deps in a named volume. **Dev-target only.** (To pull an app from a registry image instead of building it, use `--app-from-registry NAME`.)
- **No `--registry`/`--tag`** — nothing is pulled by tag; the backend runs from this repo's source and the apps from your checkouts.
- `--hostname localhost` — the dev default (no `/etc/hosts` magic). A resolvable name (DNS/mDNS) works too; `-k` regardless (self-signed).

Run from inside this repo (or pass `--repo-root`), same as the quickstart.

## 5. Verify

```bash
curl -k https://localhost:8443/api/registry/health   # → {"status":"healthy",...}
wip-deploy status --name wip
podman ps --format "{{.Names}}  {{.Status}}"          # all (healthy)?
```

Then open the app UIs: **`https://localhost:8443/apps/rc/`** (React Console) and **`https://localhost:8443/apps/kb/`** (WIP-KB). (WIP-KB offers to bootstrap its namespace on first load — accept it.)

## 6. The hot-reload loop (the whole point)

- **Backend source** — edit `components/<svc>/src/...` in this repo → `uvicorn --reload` picks it up. Dev stacks inject `WATCHFILES_FORCE_POLLING=1` so reload works across the podman bind mount. If a change doesn't take (model/route edits sometimes don't), fall back to `podman restart wip-<svc>` (~3 s).
- **App source** — edit files in your checkout → the app's own dev server (vite / tsx watch) reloads. No deployer involvement.
- **The boundary — when a reload is *not* enough:**
  - Dependency / `Dockerfile.dev` changes: the app's `node_modules` lives in a named volume and does **not** see a host `npm install` → `wip-deploy rebuild <name> --name wip`.
  - Manifest changes (ports, routes, env): re-render — re-run `install`, or `wip-deploy add-app <name> --app-source <path> --name wip`.

## 7. Debug a container

```bash
podman logs --tail 50 wip-<svc-or-app>          # its output
podman exec wip-<svc-or-app> env | grep WIP     # what env it actually got
```

In dev, apps receive `WIP_BASE_URL=http://wip-router:8080` (in-network, never localhost), `MCP_URL=.../mcp`, `WIP_NAMESPACE`, and `WIP_API_KEY` (the install admin key). To prove a platform endpoint works independently of your app code, probe it from inside the app container with its own env:

```bash
podman exec wip-<app> sh -c 'wget -qO- --header="X-API-Key: $WIP_API_KEY" $WIP_BASE_URL/api/registry/health'
```

## 8. Day-2 operations

```bash
podman restart wip-<svc>          # backend source-only edit that --reload missed (~3 s)
wip-deploy rebuild <name> --name wip   # dependency / Dockerfile.dev change
wip-deploy status  --name wip
wip-deploy stop    --name wip     # halt, keep data + secrets
wip-deploy start   --name wip
wip-deploy nuke    --name wip     # tear down (keeps data volumes + secrets by default)
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Dockerfile not found in <path>` | The `--app-source` path is the wrong level — point at the *app directory* (may be a subdir). Run `check-app-deployability <path>` first. |
| An app fails to render / "must come from `--app-source`" | On dev, an enabled app must have a `--app-source` (or `--app-from-registry`). |
| Backend edit not reflected | `--reload` missed it (model/route change, or a stale process) — `podman restart wip-<svc>`. |
| Install fails immediately / podman socket errors | podman isn't running — `podman info` (macOS: `podman machine start`), then re-run. |
| Health `200` with empty body | Unrouted path — Caddy returns `200 + ""` on unmatched paths. Use the exact `/api/registry/health`. |

---

## What this guide leaves out

- **Running a release** (production-style, GHCR images) — the [podman quickstart](../podman/README.md).
- **Kubernetes** — [k8s guide](../k8s/README.md).
- **Hardening** — `docs/wip-guide.md` §7.
</content>
