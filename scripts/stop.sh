#!/bin/bash
# WIP Stop Script - Cleanly stop all WIP containers (data preserved)
#
# Usage:
#   bash scripts/stop.sh          # Stop all services
#   bash scripts/stop.sh --status # Just show running WIP containers

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_step() { echo -e "${BLUE}[STEP]${NC} $1"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$(dirname "$SCRIPT_DIR")"

# Status-only mode
if [[ "${1:-}" == "--status" ]]; then
    echo "Running WIP containers:"
    podman ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -E "^wip-|NAMES" || echo "  (none)"
    exit 0
fi

echo "=========================================="
echo -e "  ${BLUE}WIP Clean Shutdown${NC}"
echo "=========================================="
echo ""
echo -e "  ${GREEN}Data is preserved — only containers are stopped.${NC}"
echo ""

cd "$INSTALL_DIR"

# Shutdown order: UI → app services → optional modules → infrastructure
# Reverse of startup order to avoid upstream errors

log_step "Stopping UI..."
[ -f "ui/wip-console/docker-compose.yml" ] && \
    podman-compose -f ui/wip-console/docker-compose.yml down 2>/dev/null || true

log_step "Stopping application services..."
for svc in reporting-sync ingest-gateway mcp-server document-store template-store def-store registry; do
    if [ -f "components/$svc/docker-compose.yml" ]; then
        podman-compose -f "components/$svc/docker-compose.yml" down 2>/dev/null || true
        log_info "Stopped $svc"
    fi
done

log_step "Stopping optional modules..."
for mod in docker-compose/modules/*.yml; do
    [ -f "$mod" ] && podman-compose -f "$mod" down 2>/dev/null || true
done

log_step "Stopping infrastructure..."
[ -f "docker-compose.infra.yml" ] && \
    podman-compose -f docker-compose.infra.yml down 2>/dev/null || true

echo ""

# Catch any stragglers
REMAINING=$(podman ps --format "{{.Names}}" | grep -E "^wip-" || true)
if [ -n "$REMAINING" ]; then
    log_step "Stopping remaining WIP containers..."
    echo "$REMAINING" | while read container; do
        podman stop "$container" 2>/dev/null || true
        log_info "Stopped $container"
    done
fi

echo ""
echo "=========================================="
echo -e "  ${GREEN}All WIP services stopped.${NC}"
echo -e "  Data preserved in: ${INSTALL_DIR}/data/"
echo ""
echo "  To restart:  bash scripts/start.sh          (fast, no rebuild)"
echo "  To rebuild:  bash scripts/rebuild.sh        (rebuild + restart)"
echo "  Full setup:  bash scripts/setup.sh --preset standard --localhost"
echo "=========================================="
