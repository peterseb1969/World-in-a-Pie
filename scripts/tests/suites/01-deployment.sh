#!/bin/bash
# Suite 01: Deployment Health Tests
#
# Verifies all expected containers are running and healthy.
# This is the foundation - if deployment fails, other tests won't work.
#
# Tests:
#   - Core infrastructure (MongoDB, NATS)
#   - Core services (Registry, Def-Store, Template-Store, Document-Store)
#   - Optional services based on active modules

SUITE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SUITE_DIR/../lib/common.sh"
source "$SUITE_DIR/../lib/config.sh"
source "$SUITE_DIR/../lib/api.sh"
source "$SUITE_DIR/../lib/assertions.sh"

# ─────────────────────────────────────────────────────────────────────────────
# Container Name Helpers
# ─────────────────────────────────────────────────────────────────────────────

# Get service container name (unified — no variant suffix)
get_service_container() {
    echo "wip-$1"
}

# ─────────────────────────────────────────────────────────────────────────────
# Test Functions
# ─────────────────────────────────────────────────────────────────────────────

test_mongodb_running() {
    assert_container_running "wip-mongodb"
}

test_mongodb_healthy() {
    assert_container_healthy "wip-mongodb"
}

test_nats_running() {
    assert_container_running "wip-nats"
}

test_registry_running() {
    assert_container_running "$(get_service_container registry)"
}

test_registry_health() {
    api_get "http://localhost:$PORT_REGISTRY/health"
    assert_status 200 && assert_body_contains "healthy"
}

test_def_store_running() {
    assert_container_running "$(get_service_container def-store)"
}

test_def_store_health() {
    api_get "http://localhost:$PORT_DEF_STORE/health"
    assert_status 200 && assert_body_contains "healthy"
}

test_template_store_running() {
    assert_container_running "$(get_service_container template-store)"
}

test_template_store_health() {
    api_get "http://localhost:$PORT_TEMPLATE_STORE/health"
    assert_status 200 && assert_body_contains "healthy"
}

test_document_store_running() {
    assert_container_running "$(get_service_container document-store)"
}

test_document_store_health() {
    api_get "http://localhost:$PORT_DOCUMENT_STORE/health"
    assert_status 200 && assert_body_contains "healthy"
}

test_console_running() {
    assert_container_running "$(get_service_container console)"
}

test_console_responding() {
    local url
    if has_module "oidc"; then
        # Access via Caddy when OIDC is enabled
        url="https://localhost:$PORT_CONSOLE_HTTPS"
        if curl -sk --fail --max-time 5 "$url" >/dev/null 2>&1; then
            return 0
        else
            echo "Console not responding at $url"
            return 1
        fi
    else
        # Without OIDC/Caddy, console port is not exposed to host
        # Check internally — always nginx on port 80
        local container
        container="$(get_service_container console)"

        if podman exec "$container" wget -qO- --timeout=5 http://localhost:80 >/dev/null 2>&1; then
            return 0
        fi
        if podman exec "$container" pgrep -f "nginx" >/dev/null 2>&1; then
            return 0
        else
            echo "Console not responding (nginx not running)"
            return 1
        fi
    fi
}

# OIDC module tests
test_dex_running() {
    assert_container_running "wip-dex"
}

test_caddy_running() {
    assert_container_running "wip-caddy"
}

test_caddy_responding() {
    # Caddy should respond on HTTPS with self-signed cert
    curl -sk "https://localhost:$PORT_CONSOLE_HTTPS" >/dev/null 2>&1
}

# Reporting module tests
test_postgres_running() {
    assert_container_running "wip-postgres"
}

test_postgres_healthy() {
    assert_container_healthy "wip-postgres"
}

test_reporting_sync_running() {
    assert_container_running "$(get_service_container reporting-sync)"
}

test_reporting_sync_health() {
    api_get "http://localhost:$PORT_REPORTING_SYNC/health"
    assert_status 200 && assert_body_contains "healthy"
}

# Files module tests
test_minio_running() {
    assert_container_running "wip-minio"
}

test_minio_health() {
    curl -s "http://localhost:9000/minio/health/live" >/dev/null 2>&1
}

# Dev-tools module tests
test_mongo_express_running() {
    assert_container_running "wip-mongo-express"
}

test_mongo_express_responding() {
    # Mongo Express uses basic auth, so we accept any HTTP response (including 401)
    local status
    status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "http://localhost:8081" 2>/dev/null)
    if [[ "$status" =~ ^[234][0-9][0-9]$ ]]; then
        return 0
    else
        echo "Mongo Express not responding at http://localhost:8081 (status: $status)"
        return 1
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Suite Execution
# ─────────────────────────────────────────────────────────────────────────────

run_suite() {
    suite_start "Deployment Health"

    echo -e "\n  ${DIM}Core Infrastructure${NC}"
    run_test "MongoDB container running" test_mongodb_running
    run_test "MongoDB healthy" test_mongodb_healthy
    run_test "NATS container running" test_nats_running

    echo -e "\n  ${DIM}Core Services${NC}"
    run_test "Registry container running" test_registry_running
    run_test "Registry health endpoint" test_registry_health
    run_test "Def-Store container running" test_def_store_running
    run_test "Def-Store health endpoint" test_def_store_health
    run_test "Template-Store container running" test_template_store_running
    run_test "Template-Store health endpoint" test_template_store_health
    run_test "Document-Store container running" test_document_store_running
    run_test "Document-Store health endpoint" test_document_store_health
    run_test "Console container running" test_console_running
    run_test "Console responding" test_console_responding

    # OIDC module
    if has_module "oidc"; then
        echo -e "\n  ${DIM}OIDC Module${NC}"
        run_test "Dex container running" test_dex_running
        run_test "Caddy container running" test_caddy_running
        run_test "Caddy HTTPS responding" test_caddy_responding
    fi

    # Reporting module
    if has_module "reporting"; then
        echo -e "\n  ${DIM}Reporting Module${NC}"
        run_test "PostgreSQL container running" test_postgres_running
        run_test "PostgreSQL healthy" test_postgres_healthy
        run_test "Reporting-Sync container running" test_reporting_sync_running
        run_test "Reporting-Sync health endpoint" test_reporting_sync_health
    fi

    # Files module
    if has_module "files"; then
        echo -e "\n  ${DIM}Files Module${NC}"
        run_test "MinIO container running" test_minio_running
        run_test "MinIO health endpoint" test_minio_health
    fi

    # Dev-tools module
    if has_module "dev-tools"; then
        echo -e "\n  ${DIM}Dev-Tools Module${NC}"
        run_test "Mongo Express container running" test_mongo_express_running
        run_test "Mongo Express responding" test_mongo_express_responding
    fi

    suite_end
}

# Run if executed directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    detect_config
    run_suite
    exit $TEST_SUITE_FAILED
fi
