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
#   scripts/build-release.sh --platforms linux/amd64,linux/arm64 --push  # Multi-arch
#
# Image naming:
#   With --registry: <registry>/<service>:<tag>    (e.g. gitea.local:3000/peter/registry:1.0.0)
#   Without:         wip/<service>:<tag>           (local only)
#
# Multi-arch builds:
#   Pass --platforms with a comma-separated list (e.g. linux/amd64,linux/arm64).
#   Each platform is built in turn and added to a manifest list under the image
#   name. On --push, the manifest list + all per-arch images are pushed together.
#   Non-native builds use QEMU emulation — expect 2-3x longer per emulated arch.
#   Without --platforms, the build is native-only (current fast path).

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
PLATFORMS=""  # empty = native only (fast path). Set via --platforms to go multi-arch.

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
  --platforms LIST     Comma-separated platforms (e.g. linux/amd64,linux/arm64).
                       Enables multi-arch manifest builds via QEMU emulation.
                       Default: native only (fast).
  --builder CMD        Container build tool: podman or docker (default: podman)
  -h, --help           Show this help

Services: registry, def-store, template-store, document-store,
          reporting-sync, ingest-gateway, mcp-server

Examples:
  # Build all and push to Gitea (native arch only)
  $(basename "$0") --registry gitea.local:3000/peter --tag 1.0.0 --push --insecure

  # Build one service locally
  $(basename "$0") --service document-store

  # Build all + generate production compose
  $(basename "$0") --registry gitea.local:3000/peter --tag 1.0.0 --push --insecure --generate-compose

  # Multi-arch build for GHCR (amd64 + arm64)
  $(basename "$0") --registry ghcr.io/peterseb1969 --tag v1.0 \\
                   --platforms linux/amd64,linux/arm64 --push
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
        --platforms)         PLATFORMS="$2"; shift 2 ;;
        --builder)           BUILDER="$2"; shift 2 ;;
        -h|--help)           usage ;;
        *)                   log_error "Unknown option: $1"; usage ;;
    esac
done

# Multi-arch requires a registry (manifests must be pushed — local-only is nonsense)
if [[ -n "$PLATFORMS" ]] && [[ -z "$REGISTRY" ]]; then
    log_error "--platforms requires --registry (multi-arch builds must be pushable)"
    exit 1
fi

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

# run_build: unified build driver.
#   $1         = image name (fully qualified if REGISTRY is set)
#   $2         = build context path
#   $3..       = any extra args to pass to the builder (e.g. --target, --build-arg)
#
# Native path (PLATFORMS empty): runs one `builder build -t IMG CONTEXT EXTRA_ARGS`
# and then push_image. Same behavior as before the multi-arch refactor.
#
# Multi-arch path (PLATFORMS set): drops any existing manifest under the image
# name, creates a fresh one, runs one `builder build --platform P --manifest IMG ...`
# per platform, then (if --push) runs `builder manifest push --all` to ship the
# whole list + referenced blobs in one go.
run_build() {
    local img="$1"
    local context="$2"
    shift 2
    local extra=("$@")

    # bash 3.2 (default on macOS) aborts under `set -u` on "${arr[@]}" when arr
    # is empty. The ${arr[@]+"${arr[@]}"} idiom expands to nothing for an
    # empty/unset array and to the array contents otherwise.

    if [[ -z "$PLATFORMS" ]]; then
        # Native-only fast path (unchanged behavior).
        if $BUILDER build ${extra[@]+"${extra[@]}"} -t "$img" "$context"; then
            push_image "$img"
            return 0
        fi
        return 1
    fi

    # Multi-arch path.
    log_info "  Multi-arch build: ${PLATFORMS}"
    $BUILDER manifest rm "$img" 2>/dev/null || true
    if ! $BUILDER manifest create "$img"; then
        log_error "  Failed to create manifest ${img}"
        return 1
    fi

    local plat
    IFS=',' read -ra plat_list <<< "$PLATFORMS"
    for plat in "${plat_list[@]}"; do
        log_info "  Building ${plat}"
        if ! $BUILDER build --platform "$plat" --manifest "$img" ${extra[@]+"${extra[@]}"} "$context"; then
            log_error "  Build failed for platform ${plat}"
            return 1
        fi
    done

    if $PUSH; then
        log_info "  Pushing manifest ${img} (all platforms)"
        if $INSECURE; then
            $BUILDER manifest push --all --tls-verify=false "$img" || return 1
        else
            $BUILDER manifest push --all "$img" || return 1
        fi
    fi
    return 0
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

    if run_build "$img" "$tmpdir"; then
        BUILT_IMAGES+=("$img")
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

    if run_build "$img" "$svc_dir"; then
        BUILT_IMAGES+=("$img")
        log_info "  ${svc}: OK"
    else
        log_error "  ${svc}: BUILD FAILED"
        FAILED+=("$svc")
    fi
    echo ""
}

### Vue Console removed — replaced by React Console app chunk.
### Root URL redirects to /apps/rc/ via Caddyfile.

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
    # for the 7 WIP core service names.
    local tmp="${out}.tmp"
    sed -E "s|image: [^ ]+/(registry\|def-store\|template-store\|document-store\|reporting-sync\|ingest-gateway\|mcp-server):[^ ]+|image: ${REGISTRY}/\1:${TAG}|g" \
        "$out" > "$tmp" && mv "$tmp" "$out"

    log_info "Updated image tags to ${REGISTRY}/<service>:${TAG}"
    log_info "File: ${out}"
}

# Old heredoc template removed — docker-compose.production.yml is the source of truth.
# generate_production_compose() now does an in-place sed on that file.


# ── Main ─────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}WIP Release Image Builder${NC}"
echo "Builder:   ${BUILDER}"
echo "Registry:  ${REGISTRY:-<local>}"
echo "Tag:       ${TAG}"
echo "Push:      ${PUSH}"
echo "Insecure:  ${INSECURE}"
echo "Platforms: ${PLATFORMS:-<native>}"
echo ""

START_TIME=$(date +%s)

if [[ -n "$ONLY_SERVICE" ]]; then
    case "$ONLY_SERVICE" in
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
