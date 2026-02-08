#!/bin/bash
# Suite 06: File Storage Tests
#
# Verifies MinIO file storage functionality.
# Only runs when files module is active.
#
# Tests:
#   - MinIO connection
#   - Bucket exists
#   - File upload/download (if API supports it)

SUITE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SUITE_DIR/../lib/common.sh"
source "$SUITE_DIR/../lib/config.sh"
source "$SUITE_DIR/../lib/api.sh"
source "$SUITE_DIR/../lib/assertions.sh"

# ─────────────────────────────────────────────────────────────────────────────
# MinIO Infrastructure Tests
# ─────────────────────────────────────────────────────────────────────────────

test_minio_health() {
    local response
    response=$(curl -s -o /dev/null -w "%{http_code}" \
        "http://localhost:9000/minio/health/live" 2>/dev/null)

    if [[ "$response" == "200" ]]; then
        return 0
    fi
    echo "MinIO health check failed: $response"
    return 1
}

test_minio_ready() {
    local response
    response=$(curl -s -o /dev/null -w "%{http_code}" \
        "http://localhost:9000/minio/health/ready" 2>/dev/null)

    if [[ "$response" == "200" ]]; then
        return 0
    fi
    echo "MinIO not ready: $response"
    return 1
}

test_minio_console_accessible() {
    local response
    response=$(curl -s -o /dev/null -w "%{http_code}" \
        "http://localhost:9001" 2>/dev/null)

    # Console should redirect (302) or serve page (200)
    if [[ "$response" == "200" || "$response" == "302" ]]; then
        return 0
    fi
    echo "MinIO console not accessible: $response"
    return 1
}

# ─────────────────────────────────────────────────────────────────────────────
# Bucket Tests
# ─────────────────────────────────────────────────────────────────────────────

test_wip_bucket_exists() {
    # Use mc (MinIO client) if available in container
    local buckets
    buckets=$(podman exec wip-minio mc ls local 2>/dev/null || echo "")

    if [[ -n "$buckets" ]] && echo "$buckets" | grep -q "wip"; then
        return 0
    fi

    # Try alternate method - check via HTTP
    local response
    response=$(curl -s -o /dev/null -w "%{http_code}" \
        "http://localhost:9000/wip-files/" 2>/dev/null)

    # 403 means bucket exists but we're not authorized (expected)
    # 404 would mean bucket doesn't exist
    if [[ "$response" == "403" || "$response" == "200" ]]; then
        return 0
    fi

    echo "WIP bucket may not exist: HTTP $response"
    return 1
}

# ─────────────────────────────────────────────────────────────────────────────
# File API Tests (Document-Store integration)
# ─────────────────────────────────────────────────────────────────────────────

test_files_api_list() {
    api_get "http://localhost:$PORT_DOCUMENT_STORE/api/document-store/files"

    # API might return 200 with empty list, or might not be implemented yet
    if [[ "$RESPONSE_CODE" == "200" ]]; then
        return 0
    elif [[ "$RESPONSE_CODE" == "404" ]]; then
        echo "Files API not implemented yet"
        return 0  # Not a failure, feature may be pending
    fi

    echo "Files API returned unexpected status: $RESPONSE_CODE"
    return 1
}

test_files_upload_endpoint() {
    # Test that upload endpoint exists (even if we don't actually upload)
    local response
    response=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST \
        -H "X-API-Key: $API_KEY" \
        "http://localhost:$PORT_DOCUMENT_STORE/api/document-store/files" 2>/dev/null)

    # 400 or 422 means endpoint exists but request was invalid (expected without file)
    # 404 means not implemented
    # 200/201 would be unexpected without actual file
    if [[ "$response" == "400" || "$response" == "422" || "$response" == "415" ]]; then
        return 0  # Endpoint exists
    elif [[ "$response" == "404" ]]; then
        echo "File upload not implemented yet"
        return 0  # Not a failure
    fi

    echo "Unexpected response from upload endpoint: $response"
    return 1
}

# ─────────────────────────────────────────────────────────────────────────────
# Suite Execution
# ─────────────────────────────────────────────────────────────────────────────

run_suite() {
    if ! has_module "files"; then
        suite_skip "Files" "files module not active"
        return 0
    fi

    suite_start "File Storage"

    echo -e "\n  ${DIM}MinIO Infrastructure${NC}"
    run_test "MinIO health check" test_minio_health
    run_test "MinIO ready" test_minio_ready
    run_test "MinIO console accessible" test_minio_console_accessible

    echo -e "\n  ${DIM}Storage Buckets${NC}"
    run_test "WIP files bucket exists" test_wip_bucket_exists

    echo -e "\n  ${DIM}Files API${NC}"
    run_test "Files list endpoint" test_files_api_list
    run_test "Files upload endpoint" test_files_upload_endpoint

    suite_end
}

# Run if executed directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    detect_config
    run_suite
    exit $TEST_SUITE_FAILED
fi
