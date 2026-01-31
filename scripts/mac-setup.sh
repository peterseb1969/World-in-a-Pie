#!/bin/bash
# WIP Mac Development Setup Script
# Run this to deploy the full WIP stack on macOS
#
# Usage:
#   bash scripts/mac-setup.sh
#
# Prerequisites:
#   - Podman Desktop installed (https://podman-desktop.io/)
#   - Podman machine running: podman machine start
#
# Options:
#   --minimal    Deploy without Dex (API keys only)
#   --full       Deploy with Dex OIDC (default)

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step() { echo -e "${BLUE}[STEP]${NC} $1"; }

# Health check settings
HEALTH_CHECK_INTERVAL=5   # seconds between checks (faster on Mac)
HEALTH_CHECK_TIMEOUT=60   # total seconds before giving up
HEALTH_CHECK_ATTEMPTS=$((HEALTH_CHECK_TIMEOUT / HEALTH_CHECK_INTERVAL))

# Wait for a container to be running
# Usage: wait_for_container <container_name>
wait_for_container() {
    local container="$1"
    local attempt=1

    while [ $attempt -le $HEALTH_CHECK_ATTEMPTS ]; do
        if podman ps --format "{{.Names}}" | grep -q "^${container}$"; then
            echo "  $container: running"
            return 0
        fi
        echo "  $container: waiting... ($attempt/$HEALTH_CHECK_ATTEMPTS)"
        sleep $HEALTH_CHECK_INTERVAL
        attempt=$((attempt + 1))
    done

    log_error "$container failed to start after ${HEALTH_CHECK_TIMEOUT}s"
    podman logs "$container" 2>&1 | tail -10 || true
    return 1
}

# Wait for an HTTP health endpoint to return healthy
# Usage: wait_for_health <name> <url>
wait_for_health() {
    local name="$1"
    local url="$2"
    local attempt=1

    while [ $attempt -le $HEALTH_CHECK_ATTEMPTS ]; do
        HEALTH=$(curl -s "$url" 2>/dev/null || echo '{"status":"unreachable"}')
        if echo "$HEALTH" | grep -q '"healthy"\|"status":"healthy"'; then
            log_info "  $name is healthy"
            return 0
        fi
        echo "  $name: waiting for health... ($attempt/$HEALTH_CHECK_ATTEMPTS)"
        sleep $HEALTH_CHECK_INTERVAL
        attempt=$((attempt + 1))
    done

    log_error "$name failed health check after ${HEALTH_CHECK_TIMEOUT}s: $HEALTH"
    return 1
}

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$(dirname "$SCRIPT_DIR")"
API_KEY="dev_master_key_for_testing"

# Deployment mode: full (with Dex/OIDC) or minimal (API keys only)
DEPLOY_MODE="full"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --minimal)
            DEPLOY_MODE="minimal"
            shift
            ;;
        --full)
            DEPLOY_MODE="full"
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--full|--minimal]"
            exit 1
            ;;
    esac
done

echo "=========================================="
echo "  WIP Mac Development Setup Script"
echo "  Mode: $DEPLOY_MODE"
echo "=========================================="
echo ""

# Step 1: Check system
log_step "Step 1: Checking system..."
echo "  OS: $(sw_vers -productName) $(sw_vers -productVersion)"
echo "  Architecture: $(uname -m)"
echo ""

# Step 2: Check/install dependencies
log_step "Step 2: Checking dependencies..."

if ! command -v podman &> /dev/null; then
    log_error "Podman not found. Please install Podman Desktop: https://podman-desktop.io/"
    exit 1
fi
echo "  podman: $(podman --version | head -1)"

# Check if podman machine is running
if ! podman machine inspect 2>/dev/null | grep -q '"State": "running"'; then
    log_warn "Podman machine not running. Starting..."
    podman machine start || {
        log_error "Failed to start Podman machine. Please run: podman machine init && podman machine start"
        exit 1
    }
fi
echo "  podman machine: running"

if ! command -v podman-compose &> /dev/null; then
    log_warn "podman-compose not found. Installing via pip..."
    pip3 install podman-compose
fi
echo "  podman-compose: $(podman-compose --version 2>/dev/null || echo 'installed')"

if ! command -v jq &> /dev/null; then
    log_warn "jq not found. Installing via brew..."
    brew install jq
fi
echo "  jq: $(jq --version)"
echo ""

# Step 3: Navigate to project directory
log_step "Step 3: Setting up project directory..."
cd "$INSTALL_DIR"
echo "  Location: $INSTALL_DIR"
echo ""

# Step 4: Start infrastructure
if [ "$DEPLOY_MODE" = "full" ]; then
    log_step "Step 4: Starting infrastructure (MongoDB, PostgreSQL, NATS, Dex, Mongo Express)..."
    INFRA_COMPOSE="docker-compose.infra.yml"
else
    log_step "Step 4: Starting infrastructure (MongoDB, PostgreSQL, NATS)..."
    INFRA_COMPOSE="docker-compose.infra.minimal.yml"

    # Create minimal infra file if it doesn't exist
    if [ ! -f "$INFRA_COMPOSE" ]; then
        log_warn "Creating minimal infrastructure file..."
        # Use the full file but skip dex
        INFRA_COMPOSE="docker-compose.infra.yml"
    fi
fi

podman-compose -f "$INFRA_COMPOSE" up -d

log_info "Waiting for infrastructure containers (checking every ${HEALTH_CHECK_INTERVAL}s, timeout ${HEALTH_CHECK_TIMEOUT}s)..."

# Check infrastructure health - EXIT ON FAILURE
INFRA_HEALTHY=true

# Core containers (always required)
CORE_CONTAINERS="wip-mongodb wip-postgres wip-nats"

# Add Dex and Mongo Express for full mode
if [ "$DEPLOY_MODE" = "full" ]; then
    CORE_CONTAINERS="$CORE_CONTAINERS wip-dex wip-mongo-express"
fi

for container in $CORE_CONTAINERS; do
    if ! wait_for_container "$container"; then
        INFRA_HEALTHY=false
    fi
done

if [ "$INFRA_HEALTHY" = false ]; then
    log_error "Infrastructure failed to start. Cannot continue."
    log_error "Please check the errors above and try again."
    echo ""
    echo "Troubleshooting:"
    echo "  1. Check Podman machine status: podman machine inspect"
    echo "  2. View container logs: podman logs <container-name>"
    echo "  3. Reset and try again: podman system reset"
    exit 1
fi
echo ""

# Step 5: Start Registry and initialize namespaces
log_step "Step 5: Starting Registry service..."
cd "$INSTALL_DIR/components/registry"
podman-compose -f docker-compose.dev.yml up -d

log_info "Waiting for Registry to be ready..."

# Check if Registry is healthy
if ! wait_for_health "Registry" "http://localhost:8001/health"; then
    podman logs wip-registry-dev 2>&1 | tail -20 || true
    exit 1
fi

# Initialize namespaces (may already exist)
log_info "Initializing WIP namespaces..."
INIT_RESULT=$(curl -s -X POST http://localhost:8001/api/registry/namespaces/initialize-wip \
    -H "X-API-Key: $API_KEY" 2>/dev/null || echo "failed")

if echo "$INIT_RESULT" | grep -q "created\|exists"; then
    log_info "  Namespaces initialized"
else
    log_warn "  Namespace initialization response: $INIT_RESULT"
fi
echo ""

# Step 6: Start remaining services (one at a time with health checks)
start_service() {
    local name="$1"
    local dir="$2"
    local port="$3"

    log_info "Starting $name..."
    cd "$INSTALL_DIR/components/$dir"
    podman-compose -f docker-compose.dev.yml up -d

    # Wait for service to be healthy
    if ! wait_for_health "$name" "http://localhost:$port/health"; then
        log_warn "$name may still be starting - continuing anyway"
    fi
}

log_step "Step 6: Starting application services..."
start_service "Def-Store" "def-store" "8002"
start_service "Template-Store" "template-store" "8003"
start_service "Document-Store" "document-store" "8004"
start_service "Reporting-Sync" "reporting-sync" "8005"

echo ""

# Step 7: Start Console
log_step "Step 7: Starting WIP Console..."
cd "$INSTALL_DIR/ui/wip-console"
podman-compose -f docker-compose.dev.yml up -d

log_info "Waiting for Console to start..."
sleep 10
echo ""

# Step 8: Final health checks
log_step "Step 8: Running final health checks..."
SERVICES=(
    "Registry:8001"
    "Def-Store:8002"
    "Template-Store:8003"
    "Document-Store:8004"
    "Reporting-Sync:8005"
)

ALL_HEALTHY=true
for svc in "${SERVICES[@]}"; do
    NAME="${svc%%:*}"
    PORT="${svc##*:}"
    HEALTH=$(curl -s "http://localhost:$PORT/health" 2>/dev/null || echo '{"status":"unreachable"}')
    if echo "$HEALTH" | grep -q '"healthy"\|"status":"healthy"'; then
        echo "  $NAME (port $PORT): healthy"
    else
        echo "  $NAME (port $PORT): NOT HEALTHY - $HEALTH"
        ALL_HEALTHY=false
    fi
done
echo ""

# Step 9: Final status
log_step "Step 9: Final status..."
echo ""
echo "Containers running:"
podman ps --format "  {{.Names}}: {{.Status}}"
echo ""

echo "=========================================="
if $ALL_HEALTHY; then
    echo -e "${GREEN}  Setup completed successfully!${NC}"
else
    echo -e "${YELLOW}  Setup completed with warnings${NC}"
    echo -e "${YELLOW}  Some services may still be starting.${NC}"
    echo -e "${YELLOW}  Wait a minute and check: curl http://localhost:800X/health${NC}"
fi
echo "=========================================="
echo ""

if [ "$DEPLOY_MODE" = "full" ]; then
    echo "Access WIP Console:"
    echo "  http://localhost:3000"
    echo ""
    echo "Login options:"
    echo "  - Click 'Login with Dex' -> admin@wip.local / admin123"
    echo "  - Or use API Key: $API_KEY"
else
    echo "Access WIP Console:"
    echo "  http://localhost:3000"
    echo ""
    echo "Login:"
    echo "  - Use API Key: $API_KEY"
    echo "  - (OIDC disabled in minimal mode)"
fi
echo ""
echo "Test users (for Dex OIDC):"
echo "  admin@wip.local / admin123 (wip-admins group)"
echo "  editor@wip.local / editor123 (wip-editors group)"
echo "  viewer@wip.local / viewer123 (wip-viewers group)"
echo ""
echo "API Documentation:"
echo "  Registry:       http://localhost:8001/docs"
echo "  Def-Store:      http://localhost:8002/docs"
echo "  Template-Store: http://localhost:8003/docs"
echo "  Document-Store: http://localhost:8004/docs"
echo "  Reporting-Sync: http://localhost:8005/docs"
echo ""
echo "Database UIs:"
echo "  Mongo Express:  http://localhost:8081 (admin/admin)"
echo "  PostgreSQL:     podman exec -it wip-postgres psql -U wip -d wip_reporting"
echo ""
echo "Seed test data:"
echo "  python3 -m venv .venv && source .venv/bin/activate"
echo "  pip install faker requests"
echo "  python scripts/seed_comprehensive.py"
echo ""
