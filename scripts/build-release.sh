#!/usr/bin/env bash
# Build WIP release images and optionally push to a container registry.
#
# Builds self-contained images with wip-auth (and wip-toolkit for
# document-store) baked in. No volume mounts needed at runtime.
#
# Usage:
#   scripts/build-release.sh                                    # Build all, local tags
#   scripts/build-release.sh --registry gitea.local:3000/peter --tag 1.0.0
#   scripts/build-release.sh --registry gitea.local:3000/peter --tag 1.0.0 --push --insecure
#   scripts/build-release.sh --service document-store           # Build one service
#   scripts/build-release.sh --generate-compose                 # Also emit docker-compose.production.yml
#
# Image naming:
#   With --registry: <registry>/<service>:<tag>    (e.g. gitea.local:3000/peter/registry:1.0.0)
#   Without:         wip/<service>:<tag>           (local only)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Defaults
REGISTRY=""
TAG="latest"
PUSH=false
INSECURE=false
ONLY_SERVICE=""
GENERATE_COMPOSE=false
BUILDER="${BUILDER:-podman}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step()  { echo -e "${BLUE}[STEP]${NC} $1"; }

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Build WIP release images with libraries baked in.

Options:
  --registry REG       Image registry prefix (e.g. gitea.local:3000/peter)
  --tag TAG            Image tag (default: latest)
  --push               Push images after building
  --insecure           Use --tls-verify=false for push (needed for HTTP registries)
  --service NAME       Build only one service
  --generate-compose   Generate docker-compose.production.yml after building
  --builder CMD        Container build tool: podman or docker (default: podman)
  -h, --help           Show this help

Services: registry, def-store, template-store, document-store,
          reporting-sync, ingest-gateway, mcp-server, console

Examples:
  # Build all and push to Gitea
  $(basename "$0") --registry gitea.local:3000/peter --tag 1.0.0 --push --insecure

  # Build one service locally
  $(basename "$0") --service document-store

  # Build all + generate production compose
  $(basename "$0") --registry gitea.local:3000/peter --tag 1.0.0 --push --insecure --generate-compose
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --registry)          REGISTRY="$2"; shift 2 ;;
        --tag)               TAG="$2"; shift 2 ;;
        --push)              PUSH=true; shift ;;
        --insecure)          INSECURE=true; shift ;;
        --service)           ONLY_SERVICE="$2"; shift 2 ;;
        --generate-compose)  GENERATE_COMPOSE=true; shift ;;
        --builder)           BUILDER="$2"; shift 2 ;;
        -h|--help)           usage ;;
        *)                   log_error "Unknown option: $1"; usage ;;
    esac
done

# ── Helpers ──────────────────────────────────────────────────────

image_name() {
    local svc="$1"
    if [[ -n "$REGISTRY" ]]; then
        echo "${REGISTRY}/${svc}:${TAG}"
    else
        echo "wip/${svc}:${TAG}"
    fi
}

push_image() {
    local img="$1"
    if $PUSH; then
        log_info "  Pushing ${img}"
        if $INSECURE; then
            $BUILDER push --tls-verify=false "$img"
        else
            $BUILDER push "$img"
        fi
    fi
}

BUILT_IMAGES=()
FAILED=()

# ── Services requiring wip-auth ─────────────────────────────────
# document-store also needs wip-toolkit (backup engine imports it)
AUTH_SERVICES=(registry def-store template-store document-store reporting-sync)
TOOLKIT_SERVICES=(document-store)
PLAIN_SERVICES=(ingest-gateway mcp-server)

build_python_with_libs() {
    local svc="$1"
    local svc_dir="${PROJECT_ROOT}/components/${svc}"
    local img
    img="$(image_name "$svc")"

    log_step "Building ${img} (with baked libs)"

    local tmpdir
    tmpdir="$(mktemp -d)"
    # Clean up temp dir on function exit
    trap "rm -rf '$tmpdir'" RETURN

    # Copy service files into temp build context
    cp "${svc_dir}/Dockerfile" "$tmpdir/"
    cp "${svc_dir}/requirements-docker.txt" "$tmpdir/"
    [[ -d "${svc_dir}/src" ]] && cp -r "${svc_dir}/src" "$tmpdir/src"
    [[ -d "${svc_dir}/config" ]] && cp -r "${svc_dir}/config" "$tmpdir/config"

    # Copy wip-auth library
    cp -r "${PROJECT_ROOT}/libs/wip-auth" "$tmpdir/wip-auth"

    # Determine if this service also needs wip-toolkit
    local needs_toolkit=false
    for ts in "${TOOLKIT_SERVICES[@]}"; do
        if [[ "$svc" == "$ts" ]]; then
            needs_toolkit=true
            break
        fi
    done

    if $needs_toolkit; then
        cp -r "${PROJECT_ROOT}/WIP-Toolkit" "$tmpdir/wip-toolkit"
    fi

    # Patch Dockerfile: insert lib installs after pip install of requirements.
    # Uses a temp file approach for macOS/BSD sed compatibility.
    local dockerfile="$tmpdir/Dockerfile"
    local patched="$tmpdir/Dockerfile.patched"
    awk -v needs_toolkit="$needs_toolkit" '
    /RUN pip install --no-cache-dir -r requirements-docker.txt/ {
        print
        print ""
        print "# Install wip-auth library (baked for release)"
        print "COPY wip-auth /tmp/wip-auth"
        print "RUN pip install --no-cache-dir /tmp/wip-auth && rm -rf /tmp/wip-auth"
        if (needs_toolkit == "true") {
            print ""
            print "# Install wip-toolkit (baked for release)"
            print "COPY wip-toolkit /tmp/wip-toolkit"
            print "RUN pip install --no-cache-dir /tmp/wip-toolkit && rm -rf /tmp/wip-toolkit"
        }
        next
    }
    { print }
    ' "$dockerfile" > "$patched"
    mv "$patched" "$dockerfile"

    if $BUILDER build -t "$img" "$tmpdir"; then
        BUILT_IMAGES+=("$img")
        push_image "$img"
        log_info "  ${svc}: OK"
    else
        log_error "  ${svc}: BUILD FAILED"
        FAILED+=("$svc")
    fi
    echo ""
}

build_python_plain() {
    local svc="$1"
    local svc_dir="${PROJECT_ROOT}/components/${svc}"
    local img
    img="$(image_name "$svc")"

    log_step "Building ${img}"

    if $BUILDER build -t "$img" "$svc_dir"; then
        BUILT_IMAGES+=("$img")
        push_image "$img"
        log_info "  ${svc}: OK"
    else
        log_error "  ${svc}: BUILD FAILED"
        FAILED+=("$svc")
    fi
    echo ""
}

build_console() {
    local img
    img="$(image_name "console")"

    log_step "Building ${img}"

    if $BUILDER build \
        --target production \
        --build-arg VITE_OIDC_AUTHORITY=/dex \
        --build-arg VITE_OIDC_CLIENT_ID=wip-console \
        --build-arg VITE_OIDC_CLIENT_SECRET=wip-console-secret \
        --build-arg VITE_OIDC_PROVIDER_NAME=Dex \
        --build-arg VITE_REPORTING_ENABLED=true \
        --build-arg VITE_FILES_ENABLED=true \
        --build-arg VITE_INGEST_ENABLED=true \
        -t "$img" \
        "${PROJECT_ROOT}/ui/wip-console"; then
        BUILT_IMAGES+=("$img")
        push_image "$img"
        log_info "  console: OK"
    else
        log_error "  console: BUILD FAILED"
        FAILED+=("console")
    fi
    echo ""
}

# ── Production compose generation ────────────────────────────────
#
# The source of truth is docker-compose.production.yml in the repo root.
# This function updates the image tags in that file to match the current
# registry and tag, rather than maintaining a separate heredoc template.

generate_production_compose() {
    if [[ -z "$REGISTRY" ]]; then
        log_error "--generate-compose requires --registry"
        return 1
    fi

    local out="${PROJECT_ROOT}/docker-compose.production.yml"

    if [[ ! -f "$out" ]]; then
        log_error "docker-compose.production.yml not found at ${out}"
        log_error "This file is the source of truth — it should be in the repo."
        return 1
    fi

    log_step "Updating image tags in ${out}"

    # Replace any existing registry/tag references with the current ones.
    # Matches patterns like: image: <anything>/<service>:<anything>
    # for the 8 WIP service names + console.
    local tmp="${out}.tmp"
    sed -E "s|image: [^ ]+/(registry\|def-store\|template-store\|document-store\|reporting-sync\|ingest-gateway\|mcp-server\|console):[^ ]+|image: ${REGISTRY}/\1:${TAG}|g" \
        "$out" > "$tmp" && mv "$tmp" "$out"

    log_info "Updated image tags to ${REGISTRY}/<service>:${TAG}"
    log_info "File: ${out}"
}

# Old heredoc template removed — docker-compose.production.yml is the source of truth.
# generate_production_compose() now does an in-place sed on that file.


# ── Main ─────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}WIP Release Image Builder${NC}"
echo "Builder:  ${BUILDER}"
echo "Registry: ${REGISTRY:-<local>}"
echo "Tag:      ${TAG}"
echo "Push:     ${PUSH}"
echo "Insecure: ${INSECURE}"
echo ""

START_TIME=$(date +%s)

if [[ -n "$ONLY_SERVICE" ]]; then
    case "$ONLY_SERVICE" in
        console)
            build_console ;;
        ingest-gateway|mcp-server)
            build_python_plain "$ONLY_SERVICE" ;;
        registry|def-store|template-store|document-store|reporting-sync)
            build_python_with_libs "$ONLY_SERVICE" ;;
        *)
            log_error "Unknown service: $ONLY_SERVICE"
            exit 1 ;;
    esac
else
    for svc in "${AUTH_SERVICES[@]}"; do
        build_python_with_libs "$svc"
    done
    for svc in "${PLAIN_SERVICES[@]}"; do
        build_python_plain "$svc"
    done
    build_console
fi

if $GENERATE_COMPOSE; then
    generate_production_compose
fi

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

echo ""
echo "=========================================="
if [[ ${#FAILED[@]} -gt 0 ]]; then
    echo -e "  ${RED}Build completed with errors${NC} (${DURATION}s)"
    echo ""
    echo "  Failed: ${FAILED[*]}"
else
    echo -e "  ${GREEN}All images built successfully${NC} (${DURATION}s)"
fi
echo ""
echo "  Built ${#BUILT_IMAGES[@]} images:"
for img in "${BUILT_IMAGES[@]}"; do
    echo "    ${img}"
done
if ! $PUSH; then
    echo ""
    echo "  Run with --push to push images to the registry."
fi
echo "=========================================="
