#!/bin/bash
# WIP Data Wipe Script
#
# Clears all data from WIP databases and storage.
# Use this to start fresh before re-seeding with test data.
#
# Usage:
#   ./scripts/wipe-data.sh              # Wipe with confirmation
#   ./scripts/wipe-data.sh --force      # Wipe without confirmation
#   ./scripts/wipe-data.sh --help       # Show help
#
# What gets wiped:
#   - MongoDB collections (terminologies, terms, templates, documents)
#   - PostgreSQL tables (doc_* tables, sync metadata)
#   - NATS streams (optional)

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

# Script location
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Defaults
FORCE=false
WIPE_NATS=false
WIPE_STORAGE=false

show_help() {
    cat << EOF
WIP Data Wipe Script

Clears all data from WIP databases to allow re-seeding with fresh data.

Usage: $(basename "$0") [OPTIONS]

Options:
  --force        Skip confirmation prompt
  --include-nats Also clear NATS streams (usually not needed)
  --include-storage  Also clear file storage (data directory)
  --help         Show this help message

What gets wiped:
  - MongoDB: All WIP collections (terminologies, terms, templates, documents, etc.)
  - PostgreSQL: All doc_* tables and sync metadata

What is preserved:
  - Infrastructure containers (MongoDB, PostgreSQL, NATS)
  - Service containers
  - Configuration files

After wiping, run the seed script to repopulate:
  python scripts/seed_comprehensive.py --profile standard

EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --force)
                FORCE=true
                shift
                ;;
            --include-nats)
                WIPE_NATS=true
                shift
                ;;
            --include-storage)
                WIPE_STORAGE=true
                shift
                ;;
            --help)
                show_help
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                echo "Use --help for usage information"
                exit 1
                ;;
        esac
    done
}

check_containers() {
    log_step "Checking container status..."

    local containers_running=true

    if ! podman ps --format "{{.Names}}" 2>/dev/null | grep -q "wip-mongo"; then
        log_warn "MongoDB container (wip-mongo) not running"
        containers_running=false
    fi

    if ! podman ps --format "{{.Names}}" 2>/dev/null | grep -q "wip-postgres"; then
        log_warn "PostgreSQL container (wip-postgres) not running"
        containers_running=false
    fi

    if [ "$containers_running" = false ]; then
        log_error "Required containers are not running."
        log_error "Start infrastructure first: podman-compose -f docker-compose.infra.yml up -d"
        exit 1
    fi

    log_info "Required containers are running"
}

wipe_mongodb() {
    log_step "Wiping MongoDB collections..."

    # List of MongoDB databases to wipe
    local databases=("wip_registry" "wip_def_store" "wip_template_store" "wip_document_store")

    for db in "${databases[@]}"; do
        log_info "  Dropping all collections in $db..."
        podman exec wip-mongo mongosh "$db" --quiet --eval '
            db.getCollectionNames().forEach(function(c) {
                if (!c.startsWith("system.")) {
                    db[c].drop();
                    print("    Dropped: " + c);
                }
            });
        ' 2>/dev/null || log_warn "  Database $db may not exist yet (OK)"
    done

    log_info "MongoDB wipe complete"
}

wipe_postgresql() {
    log_step "Wiping PostgreSQL tables..."

    # Drop all doc_* tables (reporting sync tables)
    podman exec wip-postgres psql -U wip -d wip_reporting -q -c "
        DO \$\$
        DECLARE
            r RECORD;
        BEGIN
            -- Drop all doc_* tables
            FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE 'doc_%')
            LOOP
                EXECUTE 'DROP TABLE IF EXISTS ' || quote_ident(r.tablename) || ' CASCADE';
                RAISE NOTICE 'Dropped table: %', r.tablename;
            END LOOP;

            -- Drop sync metadata tables
            DROP TABLE IF EXISTS _wip_schema_migrations CASCADE;
            DROP TABLE IF EXISTS _wip_sync_state CASCADE;
        END \$\$;
    " 2>/dev/null || log_warn "  Some PostgreSQL tables may not exist (OK)"

    log_info "PostgreSQL wipe complete"
}

wipe_nats() {
    if [ "$WIPE_NATS" != true ]; then
        log_info "Skipping NATS wipe (use --include-nats to include)"
        return
    fi

    log_step "Wiping NATS streams..."

    # Delete WIP-related streams
    podman exec wip-nats nats stream delete WIP_DOCUMENTS -f 2>/dev/null || log_warn "  Stream WIP_DOCUMENTS may not exist"
    podman exec wip-nats nats stream delete WIP_TEMPLATES -f 2>/dev/null || log_warn "  Stream WIP_TEMPLATES may not exist"

    log_info "NATS wipe complete"
}

wipe_storage() {
    if [ "$WIPE_STORAGE" != true ]; then
        log_info "Skipping storage wipe (use --include-storage to include)"
        return
    fi

    local data_dir="${WIP_DATA_DIR:-$PROJECT_ROOT/data}"

    if [ ! -d "$data_dir" ]; then
        log_info "No data directory found at $data_dir"
        return
    fi

    log_step "Wiping storage directory: $data_dir"
    log_warn "This will delete MongoDB data, PostgreSQL data, and NATS state!"

    if [ "$FORCE" != true ]; then
        read -p "Are you sure? This cannot be undone. (type 'yes' to confirm): " confirm
        if [ "$confirm" != "yes" ]; then
            log_info "Storage wipe cancelled"
            return
        fi
    fi

    # Stop containers first
    log_info "Stopping containers..."
    podman-compose -f "$PROJECT_ROOT/docker-compose.infra.yml" down 2>/dev/null || true
    podman-compose -f "$PROJECT_ROOT/docker-compose.infra.pi.yml" down 2>/dev/null || true

    # Remove data directories
    rm -rf "$data_dir/mongodb" "$data_dir/postgres" "$data_dir/nats"

    log_info "Storage wipe complete"
    log_warn "You need to restart infrastructure: ./scripts/setup.sh ..."
}

main() {
    echo "=========================================="
    echo "  WIP Data Wipe Script"
    echo "=========================================="
    echo ""

    parse_args "$@"

    if [ "$FORCE" != true ]; then
        echo "This will wipe ALL data from WIP databases:"
        echo "  - MongoDB: terminologies, terms, templates, documents"
        echo "  - PostgreSQL: reporting tables, sync metadata"
        [ "$WIPE_NATS" = true ] && echo "  - NATS: message streams"
        [ "$WIPE_STORAGE" = true ] && echo "  - Storage: all data files"
        echo ""
        read -p "Are you sure you want to continue? (y/N): " confirm
        if [[ "$confirm" != [yY] && "$confirm" != [yY][eE][sS] ]]; then
            log_info "Wipe cancelled"
            exit 0
        fi
        echo ""
    fi

    check_containers

    wipe_mongodb
    wipe_postgresql
    wipe_nats
    wipe_storage

    echo ""
    log_info "=========================================="
    log_info "  Data wipe complete!"
    log_info "=========================================="
    echo ""
    echo "Next steps:"
    echo "  1. Re-seed with test data:"
    echo "     python scripts/seed_comprehensive.py --profile standard"
    echo ""
    echo "  2. Or re-initialize WIP namespaces:"
    echo "     curl -X POST http://localhost:8001/api/registry/namespaces/initialize-wip \\"
    echo "       -H 'X-API-Key: dev_master_key_for_testing'"
    echo ""
}

main "$@"
