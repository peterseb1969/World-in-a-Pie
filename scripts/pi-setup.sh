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

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Configuration
REPO_URL="http://192.168.1.17:3000/peter/World-In-A-Pie.git"
INSTALL_DIR="$HOME/Development/WorldInPie"
API_KEY="dev_master_key_for_testing"

echo "=========================================="
echo "  WIP Raspberry Pi 4 Setup Script"
echo "=========================================="
echo ""

# Step 1: Check system
log_info "Checking system..."
echo "  OS: $(cat /etc/os-release | grep PRETTY_NAME | cut -d'"' -f2)"
echo "  Kernel: $(uname -r)"
echo "  Architecture: $(uname -m)"
echo "  Memory: $(free -h | grep Mem | awk '{print $2}') total, $(free -h | grep Mem | awk '{print $7}') available"
echo ""

# Step 2: Check/install dependencies
log_info "Checking dependencies..."

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
echo ""

# Step 3: Configure Podman registries
log_info "Configuring Podman registries..."
if grep -q "unqualified-search-registries" /etc/containers/registries.conf 2>/dev/null; then
    log_info "  Registries already configured"
else
    sudo tee -a /etc/containers/registries.conf > /dev/null << 'EOF'

# Added by WIP setup script
unqualified-search-registries = ["docker.io"]
EOF
    log_info "  Added docker.io to unqualified-search-registries"
fi
echo ""

# Step 4: Clone or update repository
log_info "Setting up repository..."
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

# Step 5: Start infrastructure
log_info "Starting infrastructure (MongoDB 4.4, PostgreSQL, NATS, Dex)..."
cd "$INSTALL_DIR"
podman-compose -f docker-compose.infra.pi.yml up -d

log_info "Waiting for infrastructure to be healthy..."
sleep 10

# Check infrastructure health
for container in wip-mongodb wip-postgres wip-nats wip-dex; do
    if podman ps --format "{{.Names}}" | grep -q "^${container}$"; then
        echo "  $container: running"
    else
        log_error "$container is not running!"
        podman logs $container 2>&1 | tail -5
    fi
done
echo ""

# Step 6: Start Registry and initialize namespaces
log_info "Starting Registry service..."
cd "$INSTALL_DIR/components/registry"
podman-compose -f docker-compose.dev.yml up -d

log_info "Waiting for Registry to be ready..."
sleep 5

# Try to initialize namespaces (may already exist)
log_info "Initializing WIP namespaces..."
INIT_RESULT=$(curl -s -X POST http://localhost:8001/api/registry/namespaces/initialize-wip \
    -H "X-API-Key: $API_KEY" 2>/dev/null || echo "failed")

if echo "$INIT_RESULT" | grep -q "created\|exists"; then
    log_info "  Namespaces initialized"
else
    log_warn "  Namespace initialization response: $INIT_RESULT"
fi
echo ""

# Step 7: Start remaining services
log_info "Starting Def-Store..."
cd "$INSTALL_DIR/components/def-store"
podman-compose -f docker-compose.dev.yml up -d

log_info "Starting Template-Store..."
cd "$INSTALL_DIR/components/template-store"
podman-compose -f docker-compose.dev.yml up -d

log_info "Starting Document-Store..."
cd "$INSTALL_DIR/components/document-store"
podman-compose -f docker-compose.dev.yml up -d

log_info "Starting Reporting-Sync..."
cd "$INSTALL_DIR/components/reporting-sync"
podman-compose -f docker-compose.dev.yml up -d

echo ""

# Step 8: Start Console
log_info "Starting WIP Console..."
cd "$INSTALL_DIR/ui/wip-console"
podman-compose -f docker-compose.dev.yml up -d

log_info "Waiting for services to start..."
sleep 10
echo ""

# Step 9: Health checks
log_info "Running health checks..."
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

# Step 10: Final status
log_info "Final status..."
echo ""
echo "Containers running:"
podman ps --format "  {{.Names}}: {{.Status}}"
echo ""
echo "Memory usage:"
free -h | grep -E "Mem|Swap"
echo ""

# Get hostname for access info
HOSTNAME=$(hostname)
IP=$(hostname -I | awk '{print $1}')

echo "=========================================="
if $ALL_HEALTHY; then
    echo -e "${GREEN}  Setup completed successfully!${NC}"
else
    echo -e "${YELLOW}  Setup completed with warnings${NC}"
fi
echo "=========================================="
echo ""
echo "Access WIP Console:"
echo "  Local:   http://localhost:3000"
echo "  Network: http://$HOSTNAME.local:3000"
echo "           http://$IP:3000"
echo ""
echo "Login options:"
echo "  - Click 'Login with Dex' -> admin@wip.local / admin123"
echo "  - Or use API Key: $API_KEY"
echo ""
echo "API Documentation:"
echo "  Registry:       http://localhost:8001/docs"
echo "  Def-Store:      http://localhost:8002/docs"
echo "  Template-Store: http://localhost:8003/docs"
echo "  Document-Store: http://localhost:8004/docs"
echo "  Reporting-Sync: http://localhost:8005/docs"
echo ""
