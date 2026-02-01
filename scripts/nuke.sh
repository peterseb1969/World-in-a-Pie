#!/bin/bash
# WIP Nuke Script - Remove all WIP containers and data
#
# Usage:
#   bash scripts/nuke.sh           # Remove containers + volumes
#   bash scripts/nuke.sh --keep-data  # Remove containers only, keep data
#
# This script will:
#   1. Stop and remove all WIP containers
#   2. Remove WIP volumes (unless --keep-data)
#   3. Remove WIP network
#   4. Optionally remove the data directory
#
# WARNING: This is destructive! All data will be lost.

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

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$(dirname "$SCRIPT_DIR")"
WIP_DATA_DIR="${WIP_DATA_DIR:-$INSTALL_DIR/data}"

# Options
KEEP_DATA=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --keep-data)
            KEEP_DATA=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [--keep-data]"
            echo ""
            echo "Options:"
            echo "  --keep-data    Keep data directory (only remove containers)"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--keep-data]"
            exit 1
            ;;
    esac
done

echo "=========================================="
echo -e "  ${RED}WIP NUKE SCRIPT${NC}"
echo "=========================================="
echo ""
echo "This will DESTROY:"
echo "  - All WIP containers"
echo "  - All WIP volumes"
if [ "$KEEP_DATA" = false ]; then
    echo "  - Data directory: $WIP_DATA_DIR"
fi
echo ""

# Confirm
read -p "Are you sure you want to continue? (yes/no): " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "Aborted."
    exit 0
fi
echo ""

# Step 1: Stop and remove containers using docker-compose files
log_step "Step 1: Stopping services via docker-compose..."

cd "$INSTALL_DIR"

# List of compose files to stop (in reverse order of startup)
COMPOSE_FILES=(
    "ui/wip-console/docker-compose.dev.yml"
    "components/reporting-sync/docker-compose.dev.yml"
    "components/document-store/docker-compose.dev.yml"
    "components/template-store/docker-compose.dev.yml"
    "components/def-store/docker-compose.dev.yml"
    "components/registry/docker-compose.dev.yml"
    "docker-compose.infra.yml"
)

for compose_file in "${COMPOSE_FILES[@]}"; do
    if [ -f "$compose_file" ]; then
        log_info "Stopping: $compose_file"
        podman-compose -f "$compose_file" down --volumes 2>/dev/null || true
    fi
done
echo ""

# Step 2: Force remove any remaining WIP containers
log_step "Step 2: Force removing any remaining WIP containers..."

WIP_CONTAINERS=$(podman ps -a --format "{{.Names}}" | grep -E "^wip-" || true)
if [ -n "$WIP_CONTAINERS" ]; then
    echo "$WIP_CONTAINERS" | while read container; do
        log_info "Removing container: $container"
        podman rm -f "$container" 2>/dev/null || true
    done
else
    log_info "No WIP containers found"
fi
echo ""

# Step 3: Remove WIP volumes
log_step "Step 3: Removing WIP volumes..."

WIP_VOLUMES=$(podman volume ls --format "{{.Name}}" | grep -E "wip|worldinpie" || true)
if [ -n "$WIP_VOLUMES" ]; then
    echo "$WIP_VOLUMES" | while read volume; do
        log_info "Removing volume: $volume"
        podman volume rm -f "$volume" 2>/dev/null || true
    done
else
    log_info "No WIP volumes found"
fi
echo ""

# Step 4: Remove WIP network
log_step "Step 4: Removing WIP network..."

if podman network exists wip-network 2>/dev/null; then
    log_info "Removing network: wip-network"
    podman network rm wip-network 2>/dev/null || true
else
    log_info "No WIP network found"
fi
echo ""

# Step 5: Remove data directory
if [ "$KEEP_DATA" = false ]; then
    log_step "Step 5: Removing data directory..."
    if [ -d "$WIP_DATA_DIR" ]; then
        log_warn "Removing: $WIP_DATA_DIR"
        # Need to use podman unshare for directories owned by container UIDs
        podman unshare rm -rf "$WIP_DATA_DIR" 2>/dev/null || rm -rf "$WIP_DATA_DIR" 2>/dev/null || {
            log_warn "Could not remove data directory. Trying with sudo..."
            sudo rm -rf "$WIP_DATA_DIR"
        }
        log_info "Data directory removed"
    else
        log_info "Data directory does not exist: $WIP_DATA_DIR"
    fi
else
    log_step "Step 5: Keeping data directory (--keep-data specified)"
    log_info "Data preserved at: $WIP_DATA_DIR"
fi
echo ""

# Step 6: Prune dangling resources
log_step "Step 6: Pruning dangling resources..."
podman system prune -f 2>/dev/null || true
echo ""

# Final status
log_step "Final status..."
echo ""
echo "Remaining WIP containers:"
REMAINING=$(podman ps -a --format "{{.Names}}" | grep -E "^wip-" || echo "  (none)")
echo "$REMAINING"
echo ""

echo "Remaining WIP volumes:"
REMAINING_VOLS=$(podman volume ls --format "{{.Name}}" | grep -E "wip|worldinpie" || echo "  (none)")
echo "$REMAINING_VOLS"
echo ""

echo "=========================================="
echo -e "${GREEN}  Nuke complete!${NC}"
echo "=========================================="
echo ""
echo "To redeploy, run:"
echo "  bash scripts/mac-setup.sh"
echo ""
