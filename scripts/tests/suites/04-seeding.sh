#!/bin/bash
# Suite 04: Seeding Tests
#
# Verifies seed script works and data is accessible.
# Tests data presence in MongoDB and PostgreSQL (if reporting active).
#
# Tests:
#   - Run seed script
#   - Verify terminologies seeded
#   - Verify terms seeded
#   - Verify templates seeded
#   - Verify documents seeded
#   - PostgreSQL sync (if reporting module active)

SUITE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SUITE_DIR/../lib/common.sh"
source "$SUITE_DIR/../lib/config.sh"
source "$SUITE_DIR/../lib/api.sh"
source "$SUITE_DIR/../lib/assertions.sh"

# ─────────────────────────────────────────────────────────────────────────────
# Seed Script Execution
# ─────────────────────────────────────────────────────────────────────────────

test_seed_script_runs() {
    local seed_script="$TESTS_DIR/../../seed_comprehensive.py"
    local venv_activate="$TESTS_DIR/../../../.venv/bin/activate"

    if [[ ! -f "$seed_script" ]]; then
        echo "Seed script not found: $seed_script"
        return 1
    fi

    # Activate venv if available
    if [[ -f "$venv_activate" ]]; then
        source "$venv_activate"
    fi

    # Run with minimal profile for speed
    local output
    output=$(python3 "$seed_script" --profile minimal 2>&1)
    local exit_code=$?

    if [[ $exit_code -ne 0 ]]; then
        echo "Seed script failed with exit code $exit_code"
        echo "$output" | tail -20
        return 1
    fi

    # Check for success indicators
    if echo "$output" | grep -q "Seeding complete\|Created.*documents\|documents created"; then
        return 0
    fi

    echo "Seed script ran but success message not found"
    echo "$output" | tail -10
    return 1
}

# ─────────────────────────────────────────────────────────────────────────────
# Data Verification
# ─────────────────────────────────────────────────────────────────────────────

test_terminologies_seeded() {
    api_get "http://localhost:$PORT_DEF_STORE/api/def-store/terminologies"
    if ! assert_status 200; then return 1; fi

    local count
    count=$(json_count "items")

    if [[ $count -lt 3 ]]; then
        echo "Expected at least 3 terminologies, got $count"
        return 1
    fi
    return 0
}

test_terms_seeded() {
    api_get "http://localhost:$PORT_DEF_STORE/api/def-store/terms?limit=100"
    if ! assert_status 200; then return 1; fi

    local count
    count=$(json_count "items")

    if [[ $count -lt 10 ]]; then
        echo "Expected at least 10 terms, got $count"
        return 1
    fi
    return 0
}

test_templates_seeded() {
    api_get "http://localhost:$PORT_TEMPLATE_STORE/api/template-store/templates"
    if ! assert_status 200; then return 1; fi

    local count
    count=$(json_count "items")

    if [[ $count -lt 2 ]]; then
        echo "Expected at least 2 templates, got $count"
        return 1
    fi
    return 0
}

test_documents_seeded() {
    api_get "http://localhost:$PORT_DOCUMENT_STORE/api/document-store/documents?limit=100"
    if ! assert_status 200; then return 1; fi

    local count
    count=$(json_count "items")

    if [[ $count -lt 10 ]]; then
        echo "Expected at least 10 documents, got $count"
        return 1
    fi
    return 0
}

# ─────────────────────────────────────────────────────────────────────────────
# MongoDB Direct Verification
# ─────────────────────────────────────────────────────────────────────────────

test_mongodb_terminologies() {
    assert_mongo_has_docs "wip_def_store" "terminologies" 3
}

test_mongodb_terms() {
    assert_mongo_has_docs "wip_def_store" "terms" 10
}

test_mongodb_templates() {
    assert_mongo_has_docs "wip_template_store" "templates" 2
}

test_mongodb_documents() {
    assert_mongo_has_docs "wip_document_store" "documents" 10
}

# ─────────────────────────────────────────────────────────────────────────────
# PostgreSQL Sync Verification (if reporting module active)
# ─────────────────────────────────────────────────────────────────────────────

test_postgres_documents_synced() {
    # Wait a moment for sync to catch up
    sleep 2

    assert_pg_has_rows "documents" 5
}

test_postgres_document_terms_synced() {
    assert_pg_has_rows "document_terms" 1
}

# ─────────────────────────────────────────────────────────────────────────────
# Suite Execution
# ─────────────────────────────────────────────────────────────────────────────

run_suite() {
    suite_start "Seeding"

    echo -e "\n  ${DIM}Seed Script${NC}"
    run_test "Seed script executes successfully" test_seed_script_runs

    echo -e "\n  ${DIM}API Data Verification${NC}"
    run_test "Terminologies accessible via API" test_terminologies_seeded
    run_test "Terms accessible via API" test_terms_seeded
    run_test "Templates accessible via API" test_templates_seeded
    run_test "Documents accessible via API" test_documents_seeded

    echo -e "\n  ${DIM}MongoDB Direct Verification${NC}"
    run_test "MongoDB has terminologies" test_mongodb_terminologies
    run_test "MongoDB has terms" test_mongodb_terms
    run_test "MongoDB has templates" test_mongodb_templates
    run_test "MongoDB has documents" test_mongodb_documents

    # PostgreSQL sync tests (reporting module only)
    if has_module "reporting"; then
        echo -e "\n  ${DIM}PostgreSQL Sync${NC}"
        run_test "PostgreSQL has documents" test_postgres_documents_synced
        run_test "PostgreSQL has document_terms" test_postgres_document_terms_synced
    fi

    suite_end
}

# Run if executed directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    detect_config
    run_suite
    exit $TEST_SUITE_FAILED
fi
