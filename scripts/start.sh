#!/bin/bash
# WIP Start Script - Start stopped WIP containers (no rebuild)
#
# The inverse of stop.sh. Starts existing containers in correct order.
# Fast — just podman start, no image builds.
#
# Usage:
#   bash scripts/start.sh                      # Start all WIP containers
#   bash scripts/start.sh registry def-store   # Start specific services
#   bash scripts/start.sh --status             # Show container states
#   bash scripts/start.sh --wait               # Wait for health checks

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
WAIT_HEALTH=false
STATUS_ONLY=false
SERVICES=()

# --- Service definitions ---
# Startup order: infrastructure → registry → services → UI
# Each entry: short_name:container_name:health_port:compose_dir
# Infrastructure: no HTTP /health endpoints — readiness checked via podman exec
INFRA_CONTAINERS=(
    "mongodb:wip-mongodb"
    "nats:wip-nats"
    "postgres:wip-postgres"
    "minio:wip-minio"
    "dex:wip-dex"
    "caddy:wip-caddy"
    "mongo-express:wip-mongo-express"
)

SERVICE_ORDER=(
    "registry:wip-registry:8001:components/registry"
    "def-store:wip-def-store:8002:components/def-store"
    "template-store:wip-template-store:8003:components/template-store"
    "document-store:wip-document-store:8004:components/document-store"
    "reporting-sync:wip-reporting-sync:8005:components/reporting-sync"
    "ingest-gateway:wip-ingest-gateway:8006:components/ingest-gateway"
    "console:wip-console:0:ui/wip-console"
)

# --- Argument parsing ---
while [[ $# -gt 0 ]]; do
    case $1 in
        --status)
            STATUS_ONLY=true
            shift
            ;;
        --wait)
            WAIT_HEALTH=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS] [SERVICE...]"
            echo ""
            echo "Start stopped WIP containers in correct order."
            echo ""
            echo "Services:"
            echo "  registry, def-store, template-store, document-store,"
            echo "  reporting-sync, ingest-gateway, console"
            echo ""
            echo "Options:"
            echo "  --status    Show current container states"
            echo "  --wait      Wait for health checks after starting"
            echo "  -h, --help  Show this help"
            echo ""
            echo "Examples:"
            echo "  $0                          # Start all containers"
            echo "  $0 registry def-store       # Start specific services"
            echo "  $0 --wait                   # Start all, wait for healthy"
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

# --- Status mode ---
if [ "$STATUS_ONLY" = true ]; then
    echo "WIP containers:"
    podman ps -a --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -E "^wip-|NAMES" || echo "  (none)"
    exit 0
fi

# --- Check containers exist ---
ALL_WIP=$(podman ps -a --format "{{.Names}}" | grep -E "^wip-" || true)
if [ -z "$ALL_WIP" ]; then
    log_error "No WIP containers found. Run setup.sh first:"
    echo "  bash scripts/setup.sh --preset standard --localhost"
    exit 1
fi

# --- Helper: container exists ---
container_exists() {
    echo "$ALL_WIP" | grep -q "^$1$"
}

# --- Helper: container is running ---
container_running() {
    podman ps --format "{{.Names}}" | grep -q "^$1$"
}

# --- Helper: start a container ---
start_container() {
    local name=$1
    local container=$2
    local port=$3

    if ! container_exists "$container"; then
        return 0  # Not part of this deployment
    fi

    if container_running "$container"; then
        log_info "$name: already running"
        return 0
    fi

    podman start "$container" >/dev/null 2>&1
    log_info "$name: started"

    if [ "$WAIT_HEALTH" = true ] && [ "$port" != "0" ]; then
        wait_healthy "$name" "$port"
    fi
}

# --- Helper: wait for health ---
wait_healthy() {
    local name=$1
    local port=$2
    local retries=30

    while [ $retries -gt 0 ]; do
        if curl -s "http://localhost:$port/health" 2>/dev/null | grep -q "healthy"; then
            log_info "$name: healthy"
            return 0
        fi
        sleep 2
        retries=$((retries - 1))
    done
    log_warn "$name: health check timed out (may still be starting)"
}

# --- Helper: resolve service name to container ---
resolve_service() {
    local name=$1
    for entry in "${SERVICE_ORDER[@]}"; do
        IFS=: read -r sname container port dir <<< "$entry"
        if [ "$sname" = "$name" ]; then
            echo "$container:$port"
            return 0
        fi
    done
    log_error "Unknown service: $name"
    echo ""
    return 1
}

# --- Main ---

echo "=========================================="
echo -e "  ${BLUE}WIP Start${NC}"
echo "=========================================="
echo ""

cd "$PROJECT_ROOT"

if [ ${#SERVICES[@]} -gt 0 ]; then
    # Start specific services
    for svc in "${SERVICES[@]}"; do
        resolved=$(resolve_service "$svc") || exit 1
        IFS=: read -r container port <<< "$resolved"
        start_container "$svc" "$container" "$port"
    done
else
    # Start all in order

    log_step "Starting infrastructure..."
    for entry in "${INFRA_CONTAINERS[@]}"; do
        IFS=: read -r name container <<< "$entry"
        start_container "$name" "$container" "0"
    done

    # Wait for MongoDB specifically (services depend on it)
    if container_exists "wip-mongodb"; then
        local_retries=15
        while [ $local_retries -gt 0 ]; do
            if podman exec wip-mongodb mongosh --eval "db.runCommand('ping')" &>/dev/null 2>&1; then
                break
            fi
            sleep 2
            local_retries=$((local_retries - 1))
        done
    fi

    log_step "Starting services..."
    for entry in "${SERVICE_ORDER[@]}"; do
        IFS=: read -r name container port dir <<< "$entry"
        start_container "$name" "$container" "$port"
    done
fi

ELAPSED=$(($(date +%s) - START_TIME))

echo ""
echo "=========================================="
echo -e "  ${GREEN}WIP started${NC} (${ELAPSED}s)"
echo ""
echo "  Running containers:"
podman ps --format "    {{.Names}}" | grep "wip-" | sort || echo "    (none)"
echo "=========================================="
