# Windows amd64 App Image Builds

## Context

The 3 WIP app images (React Console, ClinTrial Explorer, D&D Compendium) need amd64 variants added to their GHCR manifest lists. The arm64 variants are already pushed — built on a Mac Mini M4 Pro. The amd64 builds fail under QEMU emulation (Node.js/Vite/esbuild crashes during `npm run build` under qemu-user-static). Native amd64 builds on this Windows laptop solve the problem.

## What's already on GHCR

| Image | arm64 | amd64 |
|-------|-------|-------|
| 7 core services (Python) | ✅ | ✅ |
| `ghcr.io/peterseb1969/react-console:v1.0-rc4` | ✅ | ❌ needs build |
| `ghcr.io/peterseb1969/clintrial-explorer:v1.0-rc4` | ✅ | ❌ needs build |
| `ghcr.io/peterseb1969/dnd-compendium:v1.0-rc4` | ✅ | ❌ needs build |

## Prerequisites

1. **Podman or Docker** installed in WSL2
2. **Logged in to GHCR:**
   ```bash
   echo "YOUR_GITHUB_PAT" | podman login ghcr.io -u peterseb1969 --password-stdin
   ```
3. **WIP repo cloned** with the `develop` branch checked out
4. **App repos cloned** (or accessible):
   - `WIP-ReactConsole/`
   - `WIP-ClinTrial/clintrial-explorer/`
   - `WIP-DnD/apps/dnd-compendium/`

## Step 1: Prepare WIP library tarballs

The app Dockerfiles need WIP client libraries as tarballs. Pack them from the WIP repo:

```bash
cd World-in-a-Pie

# Pack wip-client
cd libs/wip-client && npm pack && cd ../..

# Pack wip-react
cd libs/wip-react && npm pack && cd ../..

# Pack wip-proxy
cd libs/wip-proxy && npm pack && cd ../..
```

## Step 2: Build and push each app

**CRITICAL: Every app build MUST include `--build-arg VITE_BASE_PATH=/apps/<slug>/`**. Without it, all JS/CSS assets return 404 in production. This is the #1 deployment failure — see `docs/app-containerization-guide.md`.

### React Console

The RC Console has its libs in its own `libs/` directory. Check they exist:

```bash
cd ../WIP-ReactConsole
ls libs/
# Should contain wip-client-*.tgz, wip-react-*.tgz, wip-proxy-*.tgz
# If missing, copy from the WIP repo packs above
```

Build and push (amd64 only — arm64 is already on GHCR):

```bash
podman build --platform linux/amd64 \
    --build-arg VITE_BASE_PATH=/apps/rc/ \
    -t ghcr.io/peterseb1969/react-console:v1.0-rc4-amd64 \
    .

podman push ghcr.io/peterseb1969/react-console:v1.0-rc4-amd64
```

### ClinTrial Explorer

Prepare the `.docker-libs/` directory:

```bash
cd ../WIP-ClinTrial/clintrial-explorer
mkdir -p .docker-libs
cp ../../World-in-a-Pie/libs/wip-client/wip-client-*.tgz .docker-libs/
cp ../../World-in-a-Pie/libs/wip-react/wip-react-*.tgz .docker-libs/
cp ../../World-in-a-Pie/libs/wip-proxy/wip-proxy-*.tgz .docker-libs/
```

Build and push:

```bash
podman build --platform linux/amd64 \
    --build-arg VITE_BASE_PATH=/apps/clintrial/ \
    -t ghcr.io/peterseb1969/clintrial-explorer:v1.0-rc4-amd64 \
    .

podman push ghcr.io/peterseb1969/clintrial-explorer:v1.0-rc4-amd64
```

### D&D Compendium

Prepare the `.docker-libs/` directory:

```bash
cd ../WIP-DnD/apps/dnd-compendium
mkdir -p .docker-libs
cp ../../../World-in-a-Pie/libs/wip-client/wip-client-*.tgz .docker-libs/
cp ../../../World-in-a-Pie/libs/wip-react/wip-react-*.tgz .docker-libs/
cp ../../../World-in-a-Pie/libs/wip-proxy/wip-proxy-*.tgz .docker-libs/
```

Build and push:

```bash
podman build --platform linux/amd64 \
    --build-arg VITE_BASE_PATH=/apps/dnd/ \
    -t ghcr.io/peterseb1969/dnd-compendium:v1.0-rc4-amd64 \
    .

podman push ghcr.io/peterseb1969/dnd-compendium:v1.0-rc4-amd64
```

## Step 3: Add amd64 images to existing manifests

The arm64 manifests are already on GHCR. Add the amd64 variants:

```bash
# React Console
podman manifest create --amend ghcr.io/peterseb1969/react-console:v1.0-rc4
podman manifest add ghcr.io/peterseb1969/react-console:v1.0-rc4 \
    ghcr.io/peterseb1969/react-console:v1.0-rc4-amd64
podman manifest push --all ghcr.io/peterseb1969/react-console:v1.0-rc4

# ClinTrial Explorer
podman manifest create --amend ghcr.io/peterseb1969/clintrial-explorer:v1.0-rc4
podman manifest add ghcr.io/peterseb1969/clintrial-explorer:v1.0-rc4 \
    ghcr.io/peterseb1969/clintrial-explorer:v1.0-rc4-amd64
podman manifest push --all ghcr.io/peterseb1969/clintrial-explorer:v1.0-rc4

# D&D Compendium
podman manifest create --amend ghcr.io/peterseb1969/dnd-compendium:v1.0-rc4
podman manifest add ghcr.io/peterseb1969/dnd-compendium:v1.0-rc4 \
    ghcr.io/peterseb1969/dnd-compendium:v1.0-rc4-amd64
podman manifest push --all ghcr.io/peterseb1969/dnd-compendium:v1.0-rc4
```

## Step 4: Verify

After pushing, verify the manifests contain both architectures:

```bash
podman manifest inspect ghcr.io/peterseb1969/react-console:v1.0-rc4 | grep architecture
# Should show both "amd64" and "arm64"
```

## Step 5: Test the install (optional but recommended)

Run the quick-install on this Windows WSL2 machine to prove the amd64 images work:

```bash
pip3 install bcrypt  # if not installed
export ANTHROPIC_API_KEY=sk-ant-...  # optional, for askBar
curl -fsSL https://raw.githubusercontent.com/peterseb1969/World-in-a-Pie/develop/scripts/quick-install.sh \
    | bash -s -- --yes localhost
```

Then open `https://localhost:8443/apps/rc/` in a Windows browser (accept cert warning).

## Build Report (2026-04-13)

**Machine:** Windows laptop, WSL2, podman 4.9.3, Node v18.19.1
**Built by:** BE-YAC on WSL2

### Build results

| Image | Build time | Image size | Push | Status |
|-------|-----------|------------|------|--------|
| `react-console:v1.0-rc4-amd64` | ~1m 30s | 235 MB | OK | Pushed to GHCR |
| `clintrial-explorer:v1.0-rc4-amd64` | ~3m | 317 MB | OK | Pushed to GHCR |
| `dnd-compendium:v1.0-rc4-amd64` | ~3m | 335 MB | OK | Pushed to GHCR |

### Issues encountered

1. **DnD Compendium — first build failed.** The downloaded zip was outdated (`wip-client-0.1.0`, `wip-proxy-0.1.0`, `wip-react-0.1.0` in `package.json`). The Dockerfile's `sed` rewrites the path prefix but not the version, so it looked for `/tmp/libs/wip-proxy-0.1.0.tgz` which didn't exist. Fixed by re-downloading the zip with updated `package.json` referencing current lib versions.

2. **ClinTrial Explorer — first build failed.** Vite build error: `clsx` not resolved. `clsx` is listed in `package.json` but the `npm install --ignore-scripts` in the Dockerfile somehow didn't install it. A `--no-cache` rebuild succeeded — likely a transient npm resolution issue or stale podman build cache.

3. **Manifest merge incomplete — arm64 missing.** The `podman manifest create --amend` + `manifest add` + `manifest push --all` workflow was executed, but the resulting manifests on GHCR only contain the amd64 variant. The `--amend` flag was expected to pull the existing arm64 manifest list from GHCR, but it appears podman created a fresh local manifest and only included what was in the local store. **The arm64 variants need to be re-added from the Mac Mini** where those images exist locally. The amd64 images are correctly on GHCR under the `-amd64` tags.

### What's left

The manifests need to be rebuilt on the Mac Mini (or any machine with both architectures available) to include both arm64 and amd64. The recommended approach:

```bash
# On Mac Mini (where arm64 images are local):
# Pull the amd64 images, then create combined manifests

for app in react-console clintrial-explorer dnd-compendium; do
    podman pull ghcr.io/peterseb1969/${app}:v1.0-rc4-amd64
    podman manifest create ghcr.io/peterseb1969/${app}:v1.0-rc4 \
        ghcr.io/peterseb1969/${app}:v1.0-rc4-arm64 \
        ghcr.io/peterseb1969/${app}:v1.0-rc4-amd64
    podman manifest push --all ghcr.io/peterseb1969/${app}:v1.0-rc4
done
```

This requires that the arm64 images are also tagged with `-arm64` suffix, or referenced by digest.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `podman: command not found` | Install podman in WSL2: `sudo apt install podman` |
| `npm pack` fails | Install Node.js 20: `curl -fsSL https://deb.nodesource.com/setup_20.x \| sudo bash - && sudo apt install nodejs` |
| GHCR push 403 | Token needs `write:packages` scope. Re-generate at GitHub → Settings → Developer settings → PATs |
| `.docker-libs/` missing | Re-read Step 1 and the per-app prep section |
| `manifest add` fails | The `--amend` flag on `manifest create` is key — it pulls the existing manifest from GHCR instead of creating empty |
