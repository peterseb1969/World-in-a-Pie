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

## Reporting back

After completing the builds, update this file with:
- Build times per image
- Any issues encountered
- Verification results

Commit and push so BE-YAC on the Mac Mini can see the status.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `podman: command not found` | Install podman in WSL2: `sudo apt install podman` |
| `npm pack` fails | Install Node.js 20: `curl -fsSL https://deb.nodesource.com/setup_20.x \| sudo bash - && sudo apt install nodejs` |
| GHCR push 403 | Token needs `write:packages` scope. Re-generate at GitHub → Settings → Developer settings → PATs |
| `.docker-libs/` missing | Re-read Step 1 and the per-app prep section |
| `manifest add` fails | The `--amend` flag on `manifest create` is key — it pulls the existing manifest from GHCR instead of creating empty |
