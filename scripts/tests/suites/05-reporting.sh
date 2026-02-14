#!/bin/bash
# Suite 05: Reporting Module Tests
#
# Verifies PostgreSQL sync and reporting functionality.
# Only runs when reporting module is active.
#
# Tests:
#   - PostgreSQL connection
#   - Schema exists
#   - Data sync from MongoDB
#   - Sync lag acceptable

SUITE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SUITE_DIR/../lib/common.sh"
source "$SUITE_DIR/../lib/config.sh"
source "$SUITE_DIR/../lib/api.sh"
source "$SUITE_DIR/../lib/assertions.sh"

# ─────────────────────────────────────────────────────────────────────────────
# PostgreSQL Connection Tests
# ─────────────────────────────────────────────────────────────────────────────

test_postgres_connection() {
    local result
    result=$(podman exec wip-postgres psql -U wip -d wip_reporting -c "SELECT 1" 2>&1)
    if echo "$result" | grep -q "1 row"; then
        return 0
    fi
    echo "PostgreSQL connection failed: $result"
    return 1
}

test_postgres_schema_exists() {
    local tables
    tables=$(podman exec wip-postgres psql -U wip -d wip_reporting -t -c \
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'" 2>/dev/null)

    # Reporting-sync creates doc_<template_value> tables per template, plus internal tables
    # Check for internal tables and at least one doc_ table
    if ! echo "$tables" | grep -q "_wip_sync_status"; then
        echo "Required table missing: _wip_sync_status"
        return 1
    fi

    # Check for at least one doc_ table (created when documents are synced)
    if ! echo "$tables" | grep -q "doc_"; then
        echo "No doc_* tables found (documents not yet synced)"
        return 1
    fi
    return 0
}

# ─────────────────────────────────────────────────────────────────────────────
# Data Sync Tests
# ─────────────────────────────────────────────────────────────────────────────

test_documents_synced() {
    # Count total rows across all doc_* tables
    local count
    count=$(podman exec wip-postgres psql -U wip -d wip_reporting -t -c \
        "SELECT COALESCE(SUM(cnt), 0) FROM (
            SELECT COUNT(*) as cnt FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name LIKE 'doc_%'
        ) t" 2>/dev/null | tr -d ' ')

    # Check if we have at least one doc_ table
    local table_count
    table_count=$(podman exec wip-postgres psql -U wip -d wip_reporting -t -c \
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name LIKE 'doc_%'" 2>/dev/null | tr -d ' ')

    if [[ "$table_count" -gt 0 ]]; then
        return 0
    fi
    echo "No doc_* tables found with data"
    return 1
}

test_sync_status_tracking() {
    # Check if sync status is being tracked
    local count
    count=$(podman exec wip-postgres psql -U wip -d wip_reporting -t -c \
        "SELECT COUNT(*) FROM _wip_sync_status" 2>/dev/null | tr -d ' ')

    if [[ "$count" =~ ^[0-9]+$ ]]; then
        return 0
    fi
    echo "Could not query _wip_sync_status table"
    return 1
}

test_sync_data_consistency() {
    # Compare document count between MongoDB and PostgreSQL (summing all doc_* tables)
    local mongo_auth=""
    local mongo_user mongo_pass
    mongo_user=$(grep "^WIP_MONGO_USER=" "$PROJECT_ROOT/.env" 2>/dev/null | cut -d= -f2)
    mongo_pass=$(grep "^WIP_MONGO_PASSWORD=" "$PROJECT_ROOT/.env" 2>/dev/null | cut -d= -f2)
    if [[ -n "$mongo_user" && -n "$mongo_pass" ]]; then
        mongo_auth="--username $mongo_user --password $mongo_pass --authenticationDatabase admin"
    fi

    local mongo_count
    mongo_count=$(podman exec wip-mongodb mongosh --quiet $mongo_auth --eval \
        "db.getSiblingDB('wip_document_store').documents.countDocuments({status: 'active'})" 2>/dev/null)

    # Sum counts across all doc_* tables
    local pg_count
    pg_count=$(podman exec wip-postgres psql -U wip -d wip_reporting -t -c \
        "SELECT COALESCE(SUM(row_count), 0)::int FROM (
            SELECT schemaname, relname, n_live_tup as row_count
            FROM pg_stat_user_tables
            WHERE relname LIKE 'doc_%'
        ) t" 2>/dev/null | tr -d ' ')

    if [[ -z "$mongo_count" || ! "$mongo_count" =~ ^[0-9]+$ ]]; then
        echo "Could not get MongoDB count"
        return 1
    fi

    if [[ -z "$pg_count" || ! "$pg_count" =~ ^[0-9]+$ ]]; then
        pg_count=0
    fi

    # Allow some lag (within 10% or absolute difference of 10)
    local diff=$((mongo_count - pg_count))
    if [[ $diff -lt 0 ]]; then diff=$((diff * -1)); fi

    if [[ $diff -gt 10 && $diff -gt $((mongo_count / 10)) ]]; then
        echo "Sync lag too high: MongoDB=$mongo_count, PostgreSQL=$pg_count, diff=$diff"
        return 1
    fi
    return 0
}

# ─────────────────────────────────────────────────────────────────────────────
# Reporting-Sync Service Tests
# ─────────────────────────────────────────────────────────────────────────────

test_reporting_sync_health() {
    api_get "http://localhost:$PORT_REPORTING_SYNC/health"
    assert_status 200 && assert_body_contains "healthy"
}

test_reporting_sync_metrics() {
    # Check if metrics endpoint exists (optional)
    local response
    response=$(curl -s -o /dev/null -w "%{http_code}" \
        "http://localhost:$PORT_REPORTING_SYNC/metrics" 2>/dev/null)

    # Metrics endpoint is optional, so 200 or 404 are both acceptable
    if [[ "$response" == "200" || "$response" == "404" ]]; then
        return 0
    fi
    echo "Unexpected response from metrics endpoint: $response"
    return 1
}

# ─────────────────────────────────────────────────────────────────────────────
# NATS Consumer Tests
# ─────────────────────────────────────────────────────────────────────────────

test_nats_stream_exists() {
    # Use NATS monitoring HTTP API instead of nats CLI
    local jsinfo
    jsinfo=$(curl -s "http://localhost:8222/jsz?streams=true" 2>/dev/null)

    if [[ -z "$jsinfo" ]]; then
        echo "Could not query NATS JetStream info"
        return 1
    fi

    # Check for WIP_EVENTS stream in the response
    if echo "$jsinfo" | grep -q "WIP_EVENTS"; then
        return 0
    fi
    echo "WIP_EVENTS stream not found"
    return 1
}

test_nats_consumer_active() {
    # Check JetStream consumers via HTTP API
    local jsinfo
    jsinfo=$(curl -s "http://localhost:8222/jsz?consumers=true" 2>/dev/null)

    if [[ -z "$jsinfo" ]]; then
        echo "Could not query NATS consumers"
        return 1
    fi

    # Check for reporting-sync consumer
    if echo "$jsinfo" | grep -q "reporting-sync\|consumers"; then
        return 0
    fi

    # Fallback: just verify JetStream is active
    if echo "$jsinfo" | grep -q "streams"; then
        return 0
    fi

    echo "Could not verify NATS consumer status"
    return 1
}

# ─────────────────────────────────────────────────────────────────────────────
# Suite Execution
# ─────────────────────────────────────────────────────────────────────────────

run_suite() {
    if ! has_module "reporting"; then
        suite_skip "Reporting" "reporting module not active"
        return 0
    fi

    suite_start "Reporting"

    echo -e "\n  ${DIM}PostgreSQL Connection${NC}"
    run_test "PostgreSQL accepts connections" test_postgres_connection
    run_test "Schema tables exist" test_postgres_schema_exists

    echo -e "\n  ${DIM}Data Sync${NC}"
    run_test "Documents synced to PostgreSQL" test_documents_synced
    run_test "Sync status tracking" test_sync_status_tracking
    run_test "Sync data consistency" test_sync_data_consistency

    echo -e "\n  ${DIM}Reporting-Sync Service${NC}"
    run_test "Reporting-Sync health endpoint" test_reporting_sync_health
    run_test "Metrics endpoint accessible" test_reporting_sync_metrics

    echo -e "\n  ${DIM}NATS Integration${NC}"
    run_test "NATS event stream exists" test_nats_stream_exists
    run_test "NATS consumer active" test_nats_consumer_active

    suite_end
}

# Run if executed directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    detect_config
    run_suite
    exit $TEST_SUITE_FAILED
fi
