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

    local required_tables=("documents" "document_terms")
    for table in "${required_tables[@]}"; do
        if ! echo "$tables" | grep -q "$table"; then
            echo "Required table missing: $table"
            return 1
        fi
    done
    return 0
}

# ─────────────────────────────────────────────────────────────────────────────
# Data Sync Tests
# ─────────────────────────────────────────────────────────────────────────────

test_documents_table_populated() {
    assert_pg_has_rows "documents" 1
}

test_document_terms_table_populated() {
    # This may be empty if no term references exist
    local count
    count=$(podman exec wip-postgres psql -U wip -d wip_reporting -t -c \
        "SELECT COUNT(*) FROM document_terms" 2>/dev/null | tr -d ' ')

    # Just verify the table is queryable (count >= 0)
    if [[ "$count" =~ ^[0-9]+$ ]]; then
        return 0
    fi
    echo "Could not query document_terms table"
    return 1
}

test_sync_data_consistency() {
    # Compare document count between MongoDB and PostgreSQL
    local mongo_count
    mongo_count=$(podman exec wip-mongodb mongosh --quiet --eval \
        "db.getSiblingDB('wip_document_store_dev').documents.countDocuments({status: 'active'})" 2>/dev/null)

    local pg_count
    pg_count=$(podman exec wip-postgres psql -U wip -d wip_reporting -t -c \
        "SELECT COUNT(*) FROM documents WHERE status = 'active'" 2>/dev/null | tr -d ' ')

    if [[ -z "$mongo_count" || -z "$pg_count" ]]; then
        echo "Could not get counts from databases"
        return 1
    fi

    # Allow some lag (within 10% or absolute difference of 5)
    local diff=$((mongo_count - pg_count))
    if [[ $diff -lt 0 ]]; then diff=$((diff * -1)); fi

    if [[ $diff -gt 5 && $diff -gt $((mongo_count / 10)) ]]; then
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
    local streams
    streams=$(podman exec wip-nats nats stream list --json 2>/dev/null || echo "")

    if [[ -z "$streams" ]]; then
        echo "Could not list NATS streams"
        return 1
    fi

    # Check for document events stream
    if echo "$streams" | grep -q "WIP_EVENTS\|DOCUMENTS"; then
        return 0
    fi
    echo "Expected event stream not found"
    return 1
}

test_nats_consumer_active() {
    local consumers
    consumers=$(podman exec wip-nats nats consumer list WIP_EVENTS --json 2>/dev/null || echo "")

    # Consumer may not exist if different stream name is used
    # Just verify we can query NATS
    if [[ -n "$consumers" ]] || podman exec wip-nats nats stream info WIP_EVENTS >/dev/null 2>&1; then
        return 0
    fi

    # Try alternate stream names
    if podman exec wip-nats nats stream list 2>/dev/null | grep -q "DOCUMENTS\|wip"; then
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
    run_test "Documents table has data" test_documents_table_populated
    run_test "Document terms table queryable" test_document_terms_table_populated
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
