#!/usr/bin/env bash
# Build all WIP container images for Kubernetes deployment.
#
# For K8s, images must be self-contained (no volume-mounted libraries).
# This script creates a temporary build context per service that includes
# libs/wip-auth so it gets pip-installed at build time.
#
# Usage:
#   ./build-images.sh                       # Build all, tag as wip/<service>:latest
#   ./build-images.sh --registry ghcr.io/myorg   # Prefix with registry
#   ./build-images.sh --push                # Build and push
#   ./build-images.sh --service registry    # Build one service only
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Defaults
REGISTRY=""
PUSH=false
TAG="latest"
ONLY_SERVICE=""
BUILDER="${BUILDER:-docker}"   # docker | podman

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  --registry REG   Image registry prefix (e.g. kubi5-1.local:32000, ghcr.io/myorg)
  --tag TAG        Image tag (default: latest)
  --push           Push images after building
  --service NAME   Build only one service (registry|def-store|template-store|
                   document-store|reporting-sync|ingest-gateway|mcp-server|wip-console|
                   dnd-compendium|statement-manager|clintrial-explorer)
  --builder CMD    Container build tool: docker or podman (default: docker)
  -h, --help       Show this help

Examples:
  # Build all and push to kubi5 registry
  $(basename "$0") --registry kubi5-1.local:32000 --push --builder podman

  # Build and push a single service
  $(basename "$0") --registry kubi5-1.local:32000 --push --service def-store
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --registry)  REGISTRY="$2"; shift 2 ;;
        --tag)       TAG="$2"; shift 2 ;;
        --push)      PUSH=true; shift ;;
        --service)   ONLY_SERVICE="$2"; shift 2 ;;
        --builder)   BUILDER="$2"; shift 2 ;;
        -h|--help)   usage ;;
        *)           echo "Unknown option: $1"; usage ;;
    esac
done

# ── Helpers ───────────────────────────────────────────────────────
image_name() {
    local svc="$1"
    if [[ -n "$REGISTRY" ]]; then
        echo "${REGISTRY}/wip-${svc}:${TAG}"
    else
        echo "wip/${svc}:${TAG}"
    fi
}

# ── Python services that need wip-auth baked in ──────────────────
PYTHON_AUTH_SERVICES=(registry def-store template-store document-store reporting-sync)
# Python services without wip-auth
PYTHON_PLAIN_SERVICES=(ingest-gateway mcp-server)

build_python_with_auth() {
    local svc="$1"
    local svc_dir="${PROJECT_ROOT}/components/${svc}"
    local img
    img="$(image_name "$svc")"

    echo "━━━ Building ${img} (with wip-auth) ━━━"

    local tmpdir
    tmpdir="$(mktemp -d)"
    trap "rm -rf '$tmpdir'" RETURN

    # Copy service files
    cp "${svc_dir}/Dockerfile" "$tmpdir/"
    cp "${svc_dir}/requirements-docker.txt" "$tmpdir/"
    [[ -d "${svc_dir}/src" ]] && cp -r "${svc_dir}/src" "$tmpdir/src"
    [[ -d "${svc_dir}/config" ]] && cp -r "${svc_dir}/config" "$tmpdir/config"

    # Copy wip-auth library
    cp -r "${PROJECT_ROOT}/libs/wip-auth" "$tmpdir/wip-auth"

    # Patch Dockerfile: insert wip-auth install BEFORE source copy
    # Strategy: add COPY+RUN lines right after the pip install of requirements
    sed -i.bak '/RUN pip install --no-cache-dir -r requirements-docker.txt/a\
\
# Install wip-auth library (baked in for K8s)\
COPY wip-auth /tmp/wip-auth\
RUN pip install --no-cache-dir /tmp/wip-auth \&\& rm -rf /tmp/wip-auth' \
        "$tmpdir/Dockerfile"
    rm -f "$tmpdir/Dockerfile.bak"

    $BUILDER build -t "$img" "$tmpdir"

    if $PUSH; then
        echo "  → Pushing ${img}"
        $BUILDER push "$img"
    fi
    echo ""
}

build_python_plain() {
    local svc="$1"
    local svc_dir="${PROJECT_ROOT}/components/${svc}"
    local img
    img="$(image_name "$svc")"

    echo "━━━ Building ${img} ━━━"
    $BUILDER build -t "$img" "$svc_dir"

    if $PUSH; then
        echo "  → Pushing ${img}"
        $BUILDER push "$img"
    fi
    echo ""
}

build_dnd_compendium() {
    local img
    # DnD compendium is not a WIP service — use plain name
    if [[ -n "$REGISTRY" ]]; then
        img="${REGISTRY}/dnd-compendium:${TAG}"
    else
        img="dnd-compendium:${TAG}"
    fi

    echo "━━━ Building ${img} ━━━"

    local app_dir="${PROJECT_ROOT}/../WIP-DnD/apps/dnd-compendium"
    if [[ ! -d "$app_dir" ]]; then
        echo "  ✗ DnD compendium not found at ${app_dir} — skipping"
        return 0
    fi

    # Copy @wip/client and @wip/react tarballs into .docker-libs/
    local dnd_libs="${app_dir}/../../libs"
    local libs_dir="${app_dir}/.docker-libs"
    mkdir -p "$libs_dir"
    cp "${dnd_libs}/wip-client-0.1.0.tgz" "$libs_dir/"
    cp "${dnd_libs}/wip-react-0.1.0.tgz" "$libs_dir/"

    $BUILDER build \
        --build-arg VITE_BASE_PATH=/apps/dnd/ \
        -t "$img" \
        "$app_dir"

    # Clean up
    rm -rf "$libs_dir"

    if $PUSH; then
        echo "  → Pushing ${img}"
        $BUILDER push "$img"
    fi
    echo ""
}

build_statement_manager() {
    local img
    # Statement Manager is not a WIP service — use plain name
    if [[ -n "$REGISTRY" ]]; then
        img="${REGISTRY}/statement-manager:${TAG}"
    else
        img="statement-manager:${TAG}"
    fi

    echo "━━━ Building ${img} ━━━"

    local app_dir="${PROJECT_ROOT}/../WIP-Constellations/apps/statement-manager"
    if [[ ! -d "$app_dir" ]]; then
        echo "  ✗ Statement Manager not found at ${app_dir} — skipping"
        return 0
    fi

    # Pack @wip/client, @wip/react, and @wip/proxy into .docker-libs/
    local libs_dir="${app_dir}/.docker-libs"
    mkdir -p "$libs_dir"
    (cd "${PROJECT_ROOT}/libs/wip-client" && npm pack --pack-destination "$libs_dir" >/dev/null 2>&1)
    (cd "${PROJECT_ROOT}/libs/wip-react" && npm pack --pack-destination "$libs_dir" >/dev/null 2>&1)
    (cd "${PROJECT_ROOT}/libs/wip-proxy" && npm pack --pack-destination "$libs_dir" >/dev/null 2>&1)

    $BUILDER build \
        --build-arg VITE_BASE_PATH=/apps/finance/ \
        -t "$img" \
        "$app_dir"

    # Clean up
    rm -rf "$libs_dir"

    if $PUSH; then
        echo "  → Pushing ${img}"
        $BUILDER push "$img"
    fi
    echo ""
}

build_clintrial_explorer() {
    local img
    # ClinTrial Explorer is not a WIP service — use plain name
    if [[ -n "$REGISTRY" ]]; then
        img="${REGISTRY}/clintrial-explorer:${TAG}"
    else
        img="clintrial-explorer:${TAG}"
    fi

    echo "━━━ Building ${img} ━━━"

    local app_dir="${PROJECT_ROOT}/../WIP-ClinTrial/clintrial-explorer"
    if [[ ! -d "$app_dir" ]]; then
        echo "  ✗ ClinTrial Explorer not found at ${app_dir} — skipping"
        return 0
    fi

    # Pack @wip/client, @wip/react, and @wip/proxy into .docker-libs/
    local libs_dir="${app_dir}/.docker-libs"
    mkdir -p "$libs_dir"
    (cd "${PROJECT_ROOT}/libs/wip-client" && npm pack --pack-destination "$libs_dir" >/dev/null 2>&1)
    (cd "${PROJECT_ROOT}/libs/wip-react" && npm pack --pack-destination "$libs_dir" >/dev/null 2>&1)
    (cd "${PROJECT_ROOT}/libs/wip-proxy" && npm pack --pack-destination "$libs_dir" >/dev/null 2>&1)

    $BUILDER build \
        --build-arg VITE_BASE_PATH=/apps/clintrial/ \
        -t "$img" \
        "$app_dir"

    # Clean up
    rm -rf "$libs_dir"

    if $PUSH; then
        echo "  → Pushing ${img}"
        $BUILDER push "$img"
    fi
    echo ""
}

build_console() {
    local img
    img="$(image_name "console")"

    echo "━━━ Building ${img} ━━━"
    $BUILDER build \
        --target production \
        --build-arg VITE_OIDC_AUTHORITY=/dex \
        --build-arg VITE_OIDC_CLIENT_ID=wip-console \
        --build-arg VITE_OIDC_CLIENT_SECRET=wip-console-secret \
        --build-arg VITE_OIDC_PROVIDER_NAME=Dex \
        --build-arg VITE_REPORTING_ENABLED=true \
        --build-arg VITE_FILES_ENABLED=true \
        --build-arg VITE_INGEST_ENABLED=true \
        -t "$img" \
        "${PROJECT_ROOT}/ui/wip-console"

    if $PUSH; then
        echo "  → Pushing ${img}"
        $BUILDER push "$img"
    fi
    echo ""
}

# ── Main ──────────────────────────────────────────────────────────
echo "WIP Kubernetes Image Builder"
echo "Builder: ${BUILDER}"
echo "Registry: ${REGISTRY:-<local>}"
echo "Tag: ${TAG}"
echo ""

if [[ -n "$ONLY_SERVICE" ]]; then
    case "$ONLY_SERVICE" in
        wip-console|console)           build_console ;;
        dnd-compendium)                build_dnd_compendium ;;
        statement-manager)             build_statement_manager ;;
        clintrial-explorer)            build_clintrial_explorer ;;
        ingest-gateway|mcp-server)     build_python_plain "$ONLY_SERVICE" ;;
        *)                             build_python_with_auth "$ONLY_SERVICE" ;;
    esac
else
    for svc in "${PYTHON_AUTH_SERVICES[@]}"; do
        build_python_with_auth "$svc"
    done
    for svc in "${PYTHON_PLAIN_SERVICES[@]}"; do
        build_python_plain "$svc"
    done
    build_console
fi

echo "✓ All images built successfully."
if ! $PUSH; then
    echo "  Run with --push to push images to a registry."
fi
