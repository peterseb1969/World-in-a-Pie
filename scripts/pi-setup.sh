#!/bin/bash
# WIP Raspberry Pi 4 Setup Script
# Run this on a fresh Pi to deploy the full WIP stack
#
# Usage:
#   curl -O http://192.168.1.17:3000/peter/World-In-A-Pie/raw/branch/main/scripts/pi-setup.sh
#   bash pi-setup.sh
#
# Or if repo is already cloned:
#   bash scripts/pi-setup.sh
#
# Options:
#   --minimal    Deploy without Caddy/Dex (API keys only)
#   --full       Deploy with Caddy/Dex (OIDC enabled, default)

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
HEALTH_CHECK_INTERVAL=10  # seconds between checks
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
REPO_URL="http://192.168.1.17:3000/peter/World-In-A-Pie.git"
INSTALL_DIR="$HOME/Development/WorldInPie"
API_KEY="dev_master_key_for_testing"

# Deployment mode: full (with Caddy/OIDC) or minimal (API keys only)
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
echo "  WIP Raspberry Pi 4 Setup Script"
echo "  Mode: $DEPLOY_MODE"
echo "=========================================="
echo ""

# Step 1: Check system
log_step "Step 1: Checking system..."
echo "  OS: $(cat /etc/os-release | grep PRETTY_NAME | cut -d'"' -f2)"
echo "  Kernel: $(uname -r)"
echo "  Architecture: $(uname -m)"
echo "  Memory: $(free -h | grep Mem | awk '{print $2}') total, $(free -h | grep Mem | awk '{print $7}') available"
echo ""

# Step 2: Check/install dependencies
log_step "Step 2: Checking dependencies..."

if ! command -v git &> /dev/null; then
    log_warn "Git not found. Installing..."
    sudo apt update && sudo apt install -y git
fi
echo "  git: $(git --version)"

if ! command -v podman &> /dev/null; then
    log_warn "Podman not found. Installing..."
    sudo apt update && sudo apt install -y podman podman-compose
fi
echo "  podman: $(podman --version | head -1)"

if ! command -v podman-compose &> /dev/null; then
    log_warn "podman-compose not found. Installing..."
    sudo apt install -y podman-compose
fi
echo "  podman-compose: $(podman-compose --version 2>/dev/null || echo 'installed')"

if ! command -v jq &> /dev/null; then
    log_warn "jq not found. Installing..."
    sudo apt install -y jq
fi
echo "  jq: $(jq --version)"
echo ""

# Step 3: Configure Podman registries (ensure proper config)
log_step "Step 3: Configuring Podman registries..."
REGISTRIES_CONF="/etc/containers/registries.conf"

# Check if docker.io is properly configured
if grep -qE '^\s*unqualified-search-registries\s*=.*docker\.io' "$REGISTRIES_CONF" 2>/dev/null; then
    log_info "  Registries already properly configured"
else
    log_warn "  Adding docker.io to unqualified-search-registries"
    # Remove any existing (possibly malformed) unqualified-search-registries line
    sudo sed -i '/unqualified-search-registries/d' "$REGISTRIES_CONF" 2>/dev/null || true
    # Add proper configuration
    echo 'unqualified-search-registries = ["docker.io"]' | sudo tee -a "$REGISTRIES_CONF" > /dev/null
    log_info "  Registry configuration updated"
fi
echo ""

# Step 4: Clone or update repository
log_step "Step 4: Setting up repository..."
mkdir -p "$(dirname $INSTALL_DIR)"

if [ -d "$INSTALL_DIR/.git" ]; then
    log_info "  Repository exists, pulling latest..."
    cd "$INSTALL_DIR"
    git pull
else
    log_info "  Cloning repository..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi
echo "  Location: $INSTALL_DIR"
echo ""

# Step 5: Set up environment file
log_step "Step 5: Setting up environment..."
cd "$INSTALL_DIR"

# Get hostname for configuration
HOSTNAME=$(hostname)
PI_HOSTNAME="${HOSTNAME}.local"

if [ "$DEPLOY_MODE" = "full" ]; then
    ENV_FILE=".env.pi"
    ENV_EXAMPLE=".env.pi.example"
    INFRA_COMPOSE="docker-compose.infra.pi.yml"

    if [ ! -f "$ENV_FILE" ]; then
        log_info "  Creating $ENV_FILE from template..."
        cp "$ENV_EXAMPLE" "$ENV_FILE"
        # Update hostname in the file
        sed -i "s/wip-dev-pi.local/$PI_HOSTNAME/g" "$ENV_FILE"
        log_info "  Hostname set to: $PI_HOSTNAME"
    else
        log_info "  Using existing $ENV_FILE"
    fi

    # Export env vars for this session
    export WIP_HOSTNAME="$PI_HOSTNAME"
    export WIP_AUTH_JWT_ISSUER_URL="https://$PI_HOSTNAME/dex"

else
    ENV_FILE=".env.pi.minimal"
    INFRA_COMPOSE="docker-compose.infra.pi.minimal.yml"

    if [ ! -f "$ENV_FILE" ]; then
        log_info "  Creating $ENV_FILE for minimal deployment..."
        cat > "$ENV_FILE" << EOF
# WIP Minimal Deployment - API Keys Only
WIP_AUTH_MODE=api_key_only
VITE_OIDC_ENABLED=false
EOF
    fi

    # Export env vars for this session
    export WIP_AUTH_MODE="api_key_only"
    export VITE_OIDC_ENABLED="false"
fi

echo "  Environment file: $ENV_FILE"
echo "  Infrastructure: $INFRA_COMPOSE"
echo ""

# Step 6: Start infrastructure
if [ "$DEPLOY_MODE" = "full" ]; then
    log_step "Step 6: Starting infrastructure (MongoDB 4.4, PostgreSQL, NATS, Dex, Caddy)..."
else
    log_step "Step 6: Starting infrastructure (MongoDB 4.4, PostgreSQL, NATS)..."
fi

cd "$INSTALL_DIR"
podman-compose --env-file "$ENV_FILE" -f "$INFRA_COMPOSE" up -d

log_info "Waiting for infrastructure containers (checking every ${HEALTH_CHECK_INTERVAL}s, timeout ${HEALTH_CHECK_TIMEOUT}s)..."

# Check infrastructure health - EXIT ON FAILURE
INFRA_HEALTHY=true

# Core containers (always required)
CORE_CONTAINERS="wip-mongodb wip-postgres wip-nats"

# Add Caddy and Dex for full mode
if [ "$DEPLOY_MODE" = "full" ]; then
    CORE_CONTAINERS="$CORE_CONTAINERS wip-dex wip-caddy"
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
    echo "  1. Check available disk space: df -h"
    echo "  2. Check available memory: free -h"
    echo "  3. View all container logs: podman logs <container-name>"
    echo "  4. Try pulling images manually: podman pull docker.io/library/mongo:4.4.18"
    exit 1
fi
echo ""

# Step 7: Start Registry and initialize namespaces
log_step "Step 7: Starting Registry service..."
cd "$INSTALL_DIR/components/registry"
podman-compose --env-file "../../$ENV_FILE" -f docker-compose.dev.yml up -d

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

# Step 8: Start remaining services (one at a time with health checks)
start_service() {
    local name="$1"
    local dir="$2"
    local port="$3"

    log_info "Starting $name..."
    cd "$INSTALL_DIR/components/$dir"
    podman-compose --env-file "../../$ENV_FILE" -f docker-compose.dev.yml up -d

    # Wait for service to be healthy
    if ! wait_for_health "$name" "http://localhost:$port/health"; then
        log_warn "$name may still be starting - continuing anyway"
    fi
}

log_step "Step 8: Starting application services..."
start_service "Def-Store" "def-store" "8002"
start_service "Template-Store" "template-store" "8003"
start_service "Document-Store" "document-store" "8004"
start_service "Reporting-Sync" "reporting-sync" "8005"

echo ""

# Step 9: Start Console
log_step "Step 9: Starting WIP Console..."
cd "$INSTALL_DIR/ui/wip-console"
podman-compose --env-file "../../$ENV_FILE" -f docker-compose.dev.yml up -d

log_info "Waiting for Console to start..."
sleep 15
echo ""

# Step 10: Final health checks
log_step "Step 10: Running final health checks..."
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

# Step 11: Final status
log_step "Step 11: Final status..."
echo ""
echo "Containers running:"
podman ps --format "  {{.Names}}: {{.Status}}"
echo ""
echo "Memory usage:"
free -h | grep -E "Mem|Swap"
echo ""

# Get hostname for access info
IP=$(hostname -I | awk '{print $1}')

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
    echo "Access WIP Console (via HTTPS):"
    echo "  Network: https://$PI_HOSTNAME:8443"
    echo "           https://$IP:8443"
    echo ""
    echo "  Note: Browser will warn about self-signed certificate."
    echo "        Click 'Advanced' -> 'Proceed' to accept."
    echo ""
    echo "Login options:"
    echo "  - Click 'Login with Dex' -> admin@wip.local / admin123"
    echo "  - Or use API Key: $API_KEY"
else
    echo "Access WIP Console (HTTP only):"
    echo "  Local:   http://localhost:3000"
    echo "  Network: http://$PI_HOSTNAME:3000"
    echo "           http://$IP:3000"
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
