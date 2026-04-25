#!/bin/bash
# WIP Rebuild Script - Rebuild and restart service containers
#
# For when you've changed code and need to rebuild one or more service images.
# Does NOT regenerate .env or configs — that's setup.sh's job.
#
# Usage:
#   bash scripts/rebuild.sh                        # Rebuild all services
#   bash scripts/rebuild.sh registry               # Rebuild one service
#   bash scripts/rebuild.sh registry def-store     # Rebuild multiple
#   bash scripts/rebuild.sh --all                  # Include infrastructure
#   bash scripts/rebuild.sh --no-cache             # Force full rebuild
#   bash scripts/rebuild.sh --libs                 # Services using wip-auth

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step() { echo -e "${BLUE}[STEP]${NC} $1"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
START_TIME=$(date +%s)

# Options
NO_CACHE=false
INCLUDE_ALL=false
LIBS_ONLY=false
QUICK=false
SERVICES=()

# --- Service definitions ---
# Indexed arrays: same order, access by position
# Format: name:container:health_port:compose_dir[:health_path]
# health_path defaults to /health if omitted
SERVICE_DEFS=(
    "registry:wip-registry:8001:components/registry"
    "def-store:wip-def-store:8002:components/def-store"
    "template-store:wip-template-store:8003:components/template-store"
    "document-store:wip-document-store:8004:components/document-store"
    "reporting-sync:wip-reporting-sync:8005:components/reporting-sync"
    "ingest-gateway:wip-ingest-gateway:8006:components/ingest-gateway:/api/ingest-gateway/health"
    "mcp-server:wip-mcp-server:8006:components/mcp-server"
    "console:wip-console:0:ui/wip-console"
)

# Rebuild order (same as startup order)
SERVICE_ORDER=(registry def-store template-store document-store reporting-sync ingest-gateway mcp-server console)

# Services that use wip-auth (bind-mounted, but may cache .pyc)
LIBS_SERVICES=(registry def-store template-store document-store reporting-sync ingest-gateway)

# Infrastructure compose files (only rebuilt with --all)
INFRA_COMPOSE=(
    "docker-compose/base.yml"
)

# --- Helper: look up service definition ---
get_service_def() {
    local name=$1
    for entry in "${SERVICE_DEFS[@]}"; do
        if [[ "$entry" == "$name:"* ]]; then
            echo "$entry"
            return 0
        fi
    done
    return 1
}

# --- Argument parsing ---
while [[ $# -gt 0 ]]; do
    case $1 in
        --all)
            INCLUDE_ALL=true
            shift
            ;;
        --no-cache)
            NO_CACHE=true
            shift
            ;;
        --quick)
            QUICK=true
            shift
            ;;
        --libs)
            LIBS_ONLY=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS] [SERVICE...]"
            echo ""
            echo "Rebuild and restart WIP service containers."
            echo ""
            echo "Services:"
            echo "  registry, def-store, template-store, document-store,"
            echo "  reporting-sync, ingest-gateway, mcp-server, console"
            echo ""
            echo "Options:"
            echo "  --all       Rebuild everything including infrastructure"
            echo "  --no-cache  Force full image rebuild (no layer cache)"
            echo "  --quick     Only restart if image changed (no --force-recreate)"
            echo "  --libs      Rebuild services that depend on wip-auth"
            echo "  -h, --help  Show this help"
            echo ""
            echo "Examples:"
            echo "  $0                          # Rebuild all services"
            echo "  $0 registry                 # Rebuild registry only"
            echo "  $0 registry def-store       # Rebuild multiple services"
            echo "  $0 --libs                   # Rebuild all wip-auth consumers"
            echo "  $0 --no-cache registry      # Force full rebuild of registry"
            exit 0
            ;;
        -*)
            echo "Unknown option: $1"
            exit 1
            ;;
        *)
            SERVICES+=("$1")
            shift
            ;;
    esac
done

# --- Resolve which services to rebuild ---
TARGETS=()

if [ "$LIBS_ONLY" = true ]; then
    TARGETS=("${LIBS_SERVICES[@]}")
elif [ ${#SERVICES[@]} -gt 0 ]; then
    for svc in "${SERVICES[@]}"; do
        if ! get_service_def "$svc" >/dev/null 2>&1; then
            log_error "Unknown service: $svc"
            echo "Valid services: ${SERVICE_ORDER[*]}"
            exit 1
        fi
        TARGETS+=("$svc")
    done
else
    TARGETS=("${SERVICE_ORDER[@]}")
fi

# --- Detect image registry mode ---
IMAGE_REGISTRY=""
if [ -f "$PROJECT_ROOT/.env" ]; then
    IMAGE_REGISTRY=$(grep "^WIP_IMAGE_REGISTRY=" "$PROJECT_ROOT/.env" 2>/dev/null | cut -d'=' -f2 || true)
fi

if [ -n "$IMAGE_REGISTRY" ]; then
    log_warn "Image registry detected ($IMAGE_REGISTRY). Rebuild uses local build, not registry images."
fi

# --- Helper: rebuild a service ---
rebuild_service() {
    local name=$1
    local def
    def=$(get_service_def "$name") || return 0
    IFS=: read -r _name container port dir health_path <<< "$def"
    health_path="${health_path:-/health}"

    local compose_file="$PROJECT_ROOT/$dir/docker-compose.yml"
    if [ ! -f "$compose_file" ]; then
        log_warn "$name: no docker-compose.yml found at $dir — skipping"
        return 0
    fi

    # Check if container exists (service was deployed)
    if ! podman ps -a --format "{{.Names}}" | grep -q "^${container}$"; then
        log_warn "$name: container $container not found — skipping (not deployed?)"
        return 0
    fi

    local svc_start=$(date +%s)

    # Build args
    local build_args="--build"
    if [ "$QUICK" = true ]; then
        build_args=""
    fi
    local recreate_args="--force-recreate"
    if [ "$QUICK" = true ]; then
        recreate_args=""
    fi

    # No-cache: build separately first
    if [ "$NO_CACHE" = true ]; then
        log_info "$name: building (no cache)..."
        cd "$PROJECT_ROOT/$dir"
        podman-compose -f docker-compose.yml build --no-cache 2>&1 | tail -3
        build_args=""  # Already built, just recreate
    fi

    log_info "$name: rebuilding..."
    cd "$PROJECT_ROOT/$dir"
    podman-compose --env-file "$PROJECT_ROOT/.env" -f docker-compose.yml up -d $build_args $recreate_args 2>&1 | tail -3

    # Wait for health
    if [ "$port" != "0" ]; then
        local retries=30
        while [ $retries -gt 0 ]; do
            if curl -s "http://localhost:$port$health_path" 2>/dev/null | grep -q "healthy"; then
                break
            fi
            sleep 2
            retries=$((retries - 1))
        done

        if [ $retries -gt 0 ]; then
            local svc_elapsed=$(($(date +%s) - svc_start))
            log_info "$name: healthy (${svc_elapsed}s)"
        else
            local svc_elapsed=$(($(date +%s) - svc_start))
            log_warn "$name: health check timed out (${svc_elapsed}s)"
        fi
    else
        local svc_elapsed=$(($(date +%s) - svc_start))
        log_info "$name: started (${svc_elapsed}s)"
    fi
}

# --- Helper: rebuild infrastructure ---
rebuild_infra() {
    log_step "Rebuilding infrastructure..."
    local compose_files=""
    for f in "${INFRA_COMPOSE[@]}"; do
        local full_path="$PROJECT_ROOT/$f"
        if [ -f "$full_path" ]; then
            compose_files="$compose_files -f $full_path"
        fi
    done

    # Add module overlays for active modules
    for mod_file in "$PROJECT_ROOT"/docker-compose/modules/*.yml; do
        if [ -f "$mod_file" ]; then
            # Only include modules whose containers exist
            local mod_containers
            mod_containers=$(grep "container_name:" "$mod_file" 2>/dev/null | awk '{print $2}' || true)
            for mc in $mod_containers; do
                if podman ps -a --format "{{.Names}}" | grep -q "^${mc}$"; then
                    compose_files="$compose_files -f $mod_file"
                    break
                fi
            done
        fi
    done

    if [ -n "$compose_files" ]; then
        local build_flag="--build"
        if [ "$QUICK" = true ]; then
            build_flag=""
        fi
        podman-compose --env-file "$PROJECT_ROOT/.env" $compose_files up -d $build_flag --force-recreate 2>&1 | tail -5

        # Wait for MongoDB
        log_info "Waiting for MongoDB..."
        local retries=30
        while [ $retries -gt 0 ]; do
            if podman exec wip-mongodb mongosh --eval "db.runCommand('ping')" &>/dev/null 2>&1; then
                log_info "MongoDB: ready"
                break
            fi
            sleep 2
            retries=$((retries - 1))
        done
    fi
}

# --- Main ---

echo "=========================================="
echo -e "  ${BLUE}WIP Rebuild${NC}"
echo "=========================================="
echo ""

target_list=$(IFS=', '; echo "${TARGETS[*]}")
echo -e "  Targets: ${BOLD}$target_list${NC}"
[ "$NO_CACHE" = true ] && echo -e "  Cache:   ${YELLOW}disabled${NC}"
[ "$QUICK" = true ] && echo -e "  Mode:    ${CYAN}quick (skip if unchanged)${NC}"
[ "$INCLUDE_ALL" = true ] && echo -e "  Scope:   ${CYAN}including infrastructure${NC}"
echo ""

cd "$PROJECT_ROOT"

# Rebuild infrastructure if --all
if [ "$INCLUDE_ALL" = true ]; then
    rebuild_infra
    echo ""
fi

# Rebuild services
log_step "Rebuilding services..."
for svc in "${TARGETS[@]}"; do
    rebuild_service "$svc"
done

ELAPSED=$(($(date +%s) - START_TIME))

echo ""
echo "=========================================="
echo -e "  ${GREEN}Rebuild complete${NC} (${ELAPSED}s)"
echo ""
echo "  Running containers:"
podman ps --format "    {{.Names}}" | grep "wip-" | sort || echo "    (none)"
echo "=========================================="
