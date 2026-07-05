<p align="center">
  <img src="../../images/WIP_logo_blue_small.png" alt="World In a Pie" width="160">
</p>

# WIP Quickstart — Single Host with Podman (GHCR images)

Stand up World-In-a-Pie on **one machine**, using the **published container images from GHCR**, orchestrated with **podman**. This is the production-style path (`--target compose`): real images, no source checkout of the services, no hot-reload.

> **🤖 AI agent?** Skip to [**Agent instructions**](#agent-instructions) — it walks you through asking the user what they want and bringing it up for them.

> **Other deployment styles have their own guides:**
> - Hot-reload **dev** (bind-mounted source): **[dev deployment guide](../dev/README.md)**
> - **Kubernetes**: [k8s guide](../k8s/README.md)

---

## What you'll end up with

The install below brings up the backend **plus the React Console admin UI**, so you have something to click on immediately:

- The 7 WIP backend services (Registry, Def-Store, Template-Store, Document-Store, Auth-Gateway, Reporting-Sync, MCP Server) from `ghcr.io/peterseb1969/*`
- Supporting infrastructure (MongoDB, PostgreSQL, NATS, MinIO, Dex for OIDC, Caddy) from their upstream public images
- The **React Console** at `https://localhost:8443/apps/rc/`
- HTTPS via Caddy on port **8443** (self-signed by default)
- A starter **API key** (for API/MCP access), written to disk

Everything runs as `wip-*` containers under podman on the single host. (Want more apps, e.g. the WIP-KB knowledge base? See [Installing apps](#installing-apps-react-console-wip-kb).)

---

## Prerequisites

| Requirement | Notes |
|---|---|
| A single host, **amd64 or arm64** | Linux (incl. WSL2 on Windows), or macOS via `podman machine`. ~4 GB free RAM. |
| **podman** + **podman-compose** | The deployer drives `podman-compose` under the hood (falls back to `docker-compose` if that's what it finds). |
| **git** and **Python 3.11+** | To fetch the repo and run the `wip-deploy` CLI. |

The images are **public** — no GHCR login or GitHub token is required to pull them.

> **Make sure podman is actually running before you install.** `wip-deploy` does not pre-check it, and its error if the podman socket is down is not obvious. Confirm with `podman info` (on macOS: `podman machine init && podman machine start` first, ≥4 GB).

---

## 1. Get the deployer CLI

The `wip-deploy` CLI lives in this repo, on the **`develop`** branch — check it out before installing the package.

```bash
git clone https://github.com/peterseb1969/World-in-a-Pie.git
cd World-in-a-Pie
git checkout develop          # the deployer CLI lives here

python3 -m venv .venv
./.venv/bin/pip install -e deployer

# Put it on PATH for this shell (or call ./.venv/bin/wip-deploy directly)
export PATH="$PWD/.venv/bin:$PATH"
wip-deploy --help
```

## 2. Choose an image tag

WIP images use **immutable date tags** (`YYYYMMDD<letter>`, e.g. `20260628a`). **There is no `latest`** — a floating tag is deliberately avoided (a restart could silently pull different code). You pin one coherent tag for the whole set.

Not every build is multi-arch: routine builds are **amd64-only**, while periodic sets are built for **amd64 + arm64**. Pick a tag whose arch matches your host. The easiest way to see the available tags and their architectures is the **GitHub Packages page**: <https://github.com/peterseb1969?tab=packages> (open a package, e.g. `registry`, to see each version's `OS/Arch`).

If you prefer the CLI and have `skopeo` installed (optional):

```bash
skopeo inspect --raw docker://ghcr.io/peterseb1969/registry:<TAG> \
  | python3 -c 'import sys,json;print([m["platform"]["architecture"] for m in json.load(sys.stdin).get("manifests",[])])'
```

**Recommended default: `20260628a`** — the newest set built **multi-arch (amd64 + arm64)**, so it runs on a Raspberry Pi and a cloud VM alike. (Newer amd64-only tags exist; use one only if your host is amd64.)

## 3. Install (backend + React Console)

```bash
wip-deploy install \
  --preset standard \
  --target compose \
  --name wip \
  --hostname localhost \
  --registry ghcr.io/peterseb1969 \
  --tag 20260628a \
  --app react-console --image-tag react-console=20260629a
```

- `--preset standard` — core services + Dex (OIDC) + reporting + MinIO + MCP. Lighter: `core`, `analytics`; heavier: `full` (adds the async ingest-gateway).
- `--app react-console` — enables the admin UI; `--image-tag react-console=20260629a` pins the current multi-arch RC build. **An `--image-tag` only takes effect for an app you also `--app`-enable — otherwise it's silently ignored** (so keep the `--app` and its `--image-tag` together).
- `--name wip` — names the install; its files live in `~/.wip-deploy/wip/` (rendered compose, config, **secrets**, saved spec).
- `--hostname localhost` — simplest and always works *on the host itself*. See the note below for reaching it by a real hostname.
- TLS is **self-signed by default** — no flag needed.
- **Production install?** Add `--variant prod`. It injects `WIP_VARIANT=prod` into every backend service, arming their startup guards: a service finding a known-default secret (the documented dev API key, the placeholder session secret) refuses to start instead of running forgeable. Defaults to `dev`; the setting persists in the install's saved spec, so redeploys keep it.

First run pulls all images, so give it a few minutes.

> **Hostname / how you reach it.** `--hostname` is baked into the TLS cert and the Dex OIDC issuer. **`localhost` always works on the host.** To reach WIP by a real name (e.g. `wip.internal`) — from the host or across your LAN — that name **must resolve to the host**: a DNS entry, or mDNS (`*.local`; likely works, untested here). No `/etc/hosts` edit is otherwise required. Either way the cert is self-signed, so use `-k` / click through the browser warning.

> **Run this from inside the cloned repo.** `wip-deploy install`/`render` locate the repo by walking up from your current directory for a `.git` folder (to find the `components/`/`apps/` manifests). From elsewhere you'll get `no .git directory found above ...` — pass `--repo-root /path/to/World-in-a-Pie` or set `WIP_REPO_ROOT`.

> **Just the backend, no UI?** Drop `--app react-console --image-tag …` — you'll get a headless API/MCP platform. **Want the knowledge base too?** See [Installing apps](#installing-apps-react-console-wip-kb).

## 4. Verify

```bash
# Health through Caddy (-k because the cert is self-signed)
curl -k https://localhost:8443/api/registry/health
# → {"status":"healthy","database":"connected","auth_enabled":true}

# What's running, on which images
wip-deploy status --name wip
```

All services should read `running / healthy`, then open **`https://localhost:8443/apps/rc/`** in a browser (accept the self-signed cert warning). The console loads directly in the default install.

## 5. Talk to it (API key)

Beyond the UI, you drive WIP over the **API** with the **API key** (auth runs in `dual`/`hybrid` mode: API-key for services/MCP, OIDC for humans). Your key:

```bash
cat ~/.wip-deploy/wip/secrets/api-key
```

Use it as `X-API-Key`:

```bash
curl -k -H "X-API-Key: $(cat ~/.wip-deploy/wip/secrets/api-key)" \
  https://localhost:8443/api/registry/namespaces
```

The same key is what an MCP client uses to connect to the MCP server at `/mcp` (a later topic).

---

## Installing apps (React Console, WIP-KB)

Apps are opt-in with `--app <name>`. Each is its own container image (its own tag) and serves under `/apps/<base>/`:

| App | `--app` / `--image-tag` name | URL | Notes |
|---|---|---|---|
| React Console (admin UI) | `react-console` | `/apps/rc/` | In the [step 3](#3-install-backend--react-console) default. |
| WIP-KB (knowledge base) | `wip-kb` | `/apps/kb/` | On first load it offers to bootstrap its namespace — accept it. |

> The `--image-tag` key is the **image/app name** — the same value you pass to `--app` (`wip-kb`, **not** `kb`; everything WIP-built carries the `wip-` prefix). A mismatched or non-`--app`-enabled key is **silently dropped** and the service falls back to the base `--tag` — verify what actually deployed with `podman inspect <container> --format '{{.ImageName}}'`.

**Install with the apps you want** — the flag set is declarative, so list every app each time. Backend + React Console + WIP-KB:

```bash
wip-deploy install \
  --preset standard --target compose --name wip \
  --hostname localhost --registry ghcr.io/peterseb1969 --tag 20260628a \
  --app react-console --image-tag react-console=20260629a \
  --app wip-kb --image-tag wip-kb=20260629a
```

**Adding an app to a running install** is the same command — just re-run `install` with the extra app included. It pulls the new image and does a **full restart (fast)**, reusing all data and secrets:

```bash
# add WIP-KB to a running "wip" that already had the React Console:
wip-deploy install \
  --preset standard --target compose --name wip \
  --hostname localhost --registry ghcr.io/peterseb1969 --tag 20260628a \
  --app react-console --image-tag react-console=20260629a \
  --app wip-kb --image-tag wip-kb=20260629a
```

> Don't use `wip-deploy add-app` for this — it only adds images already present **locally**, not ones that still need pulling from GHCR. Re-running `install` (above) is the path for registry images.

---

## Day-2 operations

```bash
wip-deploy status  --name wip     # what's deployed, on which images
wip-deploy stop    --name wip     # halt without deleting anything (data + secrets kept)
wip-deploy start   --name wip     # bring it back up
wip-deploy nuke    --name wip     # tear down (KEEPS data volumes + secrets by default)
```

**Upgrade to a newer tag** — re-run install with the new tag; volumes and secrets are reused by name.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Install fails immediately / podman socket errors | podman isn't running. `podman info` should succeed (macOS: `podman machine start`), then re-run install. |
| `requesting bearer token: ... 403 (Forbidden)` (common on **WSL / Docker Desktop**) | The images are public, so this is **stale/invalid GHCR credentials** being sent by podman — not a privacy issue. Run `podman logout ghcr.io`, and check `~/.docker/config.json` for a `credsStore`/`credHelpers` entry pointing `ghcr.io` at `docker-credential-desktop` (remove it), plus any stale `ghcr.io` entry in `~/.config/containers/auth.json`. Then retry. |
| An app deployed at the wrong version (e.g. base tag instead of your `--image-tag`) | The `--image-tag` was silently dropped — its key didn't match an `--app`-enabled image name (e.g. `kb` instead of `wip-kb`). Confirm with `podman inspect <container> --format '{{.ImageName}}'` and re-run install with the exact image name as the key. |
| `ErrImagePull` / `manifest unknown` on a `ghcr.io/peterseb1969/*` image | The tag doesn't exist **for your arch**. Check the [Packages page](https://github.com/peterseb1969?tab=packages) or the `skopeo` command in step 2. |
| Containers start but a service crashes on arm64 | You pinned an amd64-only tag. Use a multi-arch tag (e.g. `20260628a`). |
| `no .git directory found above ...` | You ran `install`/`render` outside the repo. `cd` into the clone, or pass `--repo-root`/set `WIP_REPO_ROOT`. |
| `curl`/browser to `:8443` refused or cert error | Cert is self-signed — use `-k` / click through. If the port is taken, add `--https-port <port>` at install. A real hostname must resolve to the host (DNS/mDNS); on the host, use `localhost`. |
| Health returns `200` with an empty body | You hit an unrouted path — Caddy returns `200 + ""` on unmatched paths. Use the exact `/api/registry/health` path. |

---

## Agent instructions

If you are an AI agent setting WIP up for a user on a single host with podman, follow this — don't just run the default blindly. Everything you need is in the numbered sections above; use those exact commands, don't invent flags or tags.

1. **Check prerequisites** (see [Prerequisites](#prerequisites)): `podman info` succeeds; `git` and Python 3.11+ present. If podman isn't running, start it before continuing.
2. **Ask the user two questions:**
   - Install the **React Console** admin UI (browsable)? *(Recommended — otherwise it's API/MCP only.)*
   - Also install **WIP-KB** (knowledge-base app)?
3. **Pick the tag by arch.** Run `uname -m`. Default to the recommended multi-arch tag in [step 2](#2-choose-an-image-tag) (`20260628a`). Only pick a newer amd64-only tag if the host is amd64 and the user asks for the latest.
4. **Get the CLI** ([step 1](#1-get-the-deployer-cli)) — clone, `git checkout develop`, venv, `pip install -e deployer`. Run `install` from inside the repo (or pass `--repo-root`).
5. **Install** with the apps the user chose ([step 3](#3-install-backend--react-console) / [Installing apps](#installing-apps-react-console-wip-kb)). Keep each `--app` together with its `--image-tag`, using the same name for both (RC: `react-console=20260629a`; KB: `wip-kb=20260629a` — note the `wip-` prefix, `kb` won't match) — a mismatched or un-`--app`-ed image-tag is silently ignored.
6. **Verify and report** ([step 4](#4-verify)): curl the health endpoint and **validate the JSON body** (never trust a bare `200`); optionally confirm each app's deployed image with `podman inspect <container> --format '{{.ImageName}}'`. Then tell the user:
   - the Console URL(s) — `https://localhost:8443/apps/rc/`, `/apps/kb/` (or their real hostname if it resolves),
   - their API key: `cat ~/.wip-deploy/wip/secrets/api-key`,
   - how to stop/start ([Day-2](#day-2-operations)).
7. If a pull returns **403**, it's stale GHCR creds, not privacy — apply the [Troubleshooting](#troubleshooting) fix and retry.

---

## Appendix: host prep (Pi SSD, podman sizing)

**Platforms**
- **Raspberry Pi 5 (8 GB+)** — recommended for home/lab; 16 GB has plenty of headroom. **Pi 4 is not supported** (services start but compound under load).
- **macOS (16 GB+)** — fine for development and small production loads. Most operations run within the default 2 GB Podman machine; bump to 4 GB for sustained work (NL queries, bulk runs, multiple apps).
- **Linux VM (~8 GB+)** — with **real local disk**, not network-attached. NAS-backed volumes pay heavily on MongoDB writes and NATS JetStream persistence; a 16 GB VM with an SSD-class block device is comfortable for production.

**Pi: SSD is mandatory.** SD-card storage is the single biggest performance variable — MongoDB, NATS, PostgreSQL, and MinIO all compound on slow storage. The 200+ docs/second tested throughput is an SSD figure.

```bash
lsblk                                    # identify the device
sudo mkfs.ext4 /dev/sda1                 # CAUTION: destroys existing data on the partition
sudo mkdir -p /mnt/wip-data && sudo mount /dev/sda1 /mnt/wip-data
sudo chown -R "$USER:$USER" /mnt/wip-data
sudo blkid /dev/sda1                     # copy the UUID
echo 'UUID=<your-uuid>  /mnt/wip-data  ext4  defaults,noatime  0  2' | sudo tee -a /etc/fstab

# then install with the SSD as the data dir (add --data-dir to the step 3 command):
wip-deploy install --preset standard --target compose --name wip \
  --hostname localhost --registry ghcr.io/peterseb1969 --tag 20260628a \
  --data-dir /mnt/wip-data
```

**macOS: Podman machine sizing** — resize to 4 GB for sustained loads:

```bash
podman machine stop
podman machine set --memory 4096
podman machine start
```

---

## What this guide intentionally leaves out

- **Hardening** — Let's Encrypt TLS, per-service API keys, MongoDB/NATS auth, encryption at rest: see `docs/wip-guide.md` §2 (tiers) and §7 (hardening).
- **Hot-reload dev** and **Kubernetes** — separate guides (see the links at the top).
</content>
