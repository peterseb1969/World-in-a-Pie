#!/bin/bash
# Suite 02: Authentication Tests
#
# Verifies authentication works correctly for all endpoints.
# Tests both API key auth and OIDC (when enabled).
#
# Tests:
#   - API key authentication succeeds
#   - Missing API key is rejected
#   - Invalid API key is rejected
#   - OIDC endpoints (when oidc module active)

SUITE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SUITE_DIR/../lib/common.sh"
source "$SUITE_DIR/../lib/config.sh"
source "$SUITE_DIR/../lib/api.sh"
source "$SUITE_DIR/../lib/assertions.sh"

# ─────────────────────────────────────────────────────────────────────────────
# Test Endpoints (all require authentication)
# ─────────────────────────────────────────────────────────────────────────────

PROTECTED_ENDPOINTS=(
    "http://localhost:$PORT_REGISTRY/api/registry/id-pools"
    "http://localhost:$PORT_DEF_STORE/api/def-store/terminologies"
    "http://localhost:$PORT_TEMPLATE_STORE/api/template-store/templates"
    "http://localhost:$PORT_DOCUMENT_STORE/api/document-store/documents"
)

# ─────────────────────────────────────────────────────────────────────────────
# API Key Authentication Tests
# ─────────────────────────────────────────────────────────────────────────────

test_registry_with_api_key() {
    api_get "http://localhost:$PORT_REGISTRY/api/registry/id-pools"
    assert_success
}

test_def_store_with_api_key() {
    api_get "http://localhost:$PORT_DEF_STORE/api/def-store/terminologies"
    assert_success
}

test_template_store_with_api_key() {
    api_get "http://localhost:$PORT_TEMPLATE_STORE/api/template-store/templates"
    assert_success
}

test_document_store_with_api_key() {
    api_get "http://localhost:$PORT_DOCUMENT_STORE/api/document-store/documents"
    assert_success
}

# ─────────────────────────────────────────────────────────────────────────────
# Missing API Key Tests (should reject)
# ─────────────────────────────────────────────────────────────────────────────

test_registry_no_auth() {
    api_get_noauth "http://localhost:$PORT_REGISTRY/api/registry/id-pools"
    assert_auth_failure
}

test_def_store_no_auth() {
    api_get_noauth "http://localhost:$PORT_DEF_STORE/api/def-store/terminologies"
    assert_auth_failure
}

test_template_store_no_auth() {
    api_get_noauth "http://localhost:$PORT_TEMPLATE_STORE/api/template-store/templates"
    assert_auth_failure
}

test_document_store_no_auth() {
    api_get_noauth "http://localhost:$PORT_DOCUMENT_STORE/api/document-store/documents"
    assert_auth_failure
}

# ─────────────────────────────────────────────────────────────────────────────
# Invalid API Key Tests (should reject)
# ─────────────────────────────────────────────────────────────────────────────

test_registry_bad_key() {
    api_get_badkey "http://localhost:$PORT_REGISTRY/api/registry/id-pools"
    assert_auth_failure
}

test_def_store_bad_key() {
    api_get_badkey "http://localhost:$PORT_DEF_STORE/api/def-store/terminologies"
    assert_auth_failure
}

test_template_store_bad_key() {
    api_get_badkey "http://localhost:$PORT_TEMPLATE_STORE/api/template-store/templates"
    assert_auth_failure
}

test_document_store_bad_key() {
    api_get_badkey "http://localhost:$PORT_DOCUMENT_STORE/api/document-store/documents"
    assert_auth_failure
}

# ─────────────────────────────────────────────────────────────────────────────
# OIDC Tests (when enabled)
# ─────────────────────────────────────────────────────────────────────────────

test_dex_discovery() {
    # OIDC discovery endpoint should be publicly accessible
    # Dex is proxied via Caddy at /dex path
    local response
    response=$(curl -sk "https://localhost:$PORT_CONSOLE_HTTPS/dex/.well-known/openid-configuration" 2>/dev/null)
    if echo "$response" | grep -q "issuer"; then
        return 0
    fi
    # Also try direct Dex port as fallback
    response=$(curl -s "http://localhost:5556/dex/.well-known/openid-configuration" 2>/dev/null)
    if echo "$response" | grep -q "issuer"; then
        return 0
    fi
    echo "OIDC discovery endpoint not responding"
    return 1
}

test_dex_jwks() {
    # JWKS endpoint should be accessible
    local response
    response=$(curl -sk "https://localhost:$PORT_CONSOLE_HTTPS/dex/keys" 2>/dev/null)
    if echo "$response" | grep -q "keys"; then
        return 0
    fi
    echo "JWKS endpoint not responding"
    return 1
}

test_caddy_auth_redirect() {
    # Unauthenticated request to console should redirect to login
    local response_code
    response_code=$(curl -sk -o /dev/null -w "%{http_code}" \
        "https://localhost:$PORT_CONSOLE_HTTPS/" 2>/dev/null)

    # Should get 200 (SPA) or 302 (redirect to auth) - both are acceptable
    if [[ "$response_code" == "200" || "$response_code" == "302" ]]; then
        return 0
    fi
    echo "Expected 200 or 302, got $response_code"
    return 1
}

test_proxy_api_with_key() {
    # API requests via Caddy proxy should work with API key
    local response
    response=$(curl -sk -w "\n%{http_code}" \
        -H "X-API-Key: $API_KEY" \
        "https://localhost:$PORT_CONSOLE_HTTPS/api/def-store/terminologies" 2>/dev/null)

    local code=$(echo "$response" | tail -1)
    if [[ "$code" == "200" ]]; then
        return 0
    fi
    echo "Proxy API call failed with status $code"
    return 1
}

# ─────────────────────────────────────────────────────────────────────────────
# Health Endpoints (should be public)
# ─────────────────────────────────────────────────────────────────────────────

test_health_endpoints_public() {
    local endpoints=(
        "http://localhost:$PORT_REGISTRY/health"
        "http://localhost:$PORT_DEF_STORE/health"
        "http://localhost:$PORT_TEMPLATE_STORE/health"
        "http://localhost:$PORT_DOCUMENT_STORE/health"
    )

    local failed=0
    for endpoint in "${endpoints[@]}"; do
        api_get_noauth "$endpoint"
        if [[ "$RESPONSE_CODE" != "200" ]]; then
            echo "Health endpoint should be public: $endpoint"
            failed=1
        fi
    done

    return $failed
}

# ─────────────────────────────────────────────────────────────────────────────
# Suite Execution
# ─────────────────────────────────────────────────────────────────────────────

run_suite() {
    suite_start "Authentication"

    echo -e "\n  ${DIM}Health Endpoints (public)${NC}"
    run_test "Health endpoints accessible without auth" test_health_endpoints_public

    echo -e "\n  ${DIM}API Key Authentication${NC}"
    run_test "Registry accepts valid API key" test_registry_with_api_key
    run_test "Def-Store accepts valid API key" test_def_store_with_api_key
    run_test "Template-Store accepts valid API key" test_template_store_with_api_key
    run_test "Document-Store accepts valid API key" test_document_store_with_api_key

    echo -e "\n  ${DIM}Missing API Key (should reject)${NC}"
    run_test "Registry rejects missing auth" test_registry_no_auth
    run_test "Def-Store rejects missing auth" test_def_store_no_auth
    run_test "Template-Store rejects missing auth" test_template_store_no_auth
    run_test "Document-Store rejects missing auth" test_document_store_no_auth

    echo -e "\n  ${DIM}Invalid API Key (should reject)${NC}"
    run_test "Registry rejects bad API key" test_registry_bad_key
    run_test "Def-Store rejects bad API key" test_def_store_bad_key
    run_test "Template-Store rejects bad API key" test_template_store_bad_key
    run_test "Document-Store rejects bad API key" test_document_store_bad_key

    # OIDC-specific tests
    if has_module "oidc"; then
        echo -e "\n  ${DIM}OIDC Authentication${NC}"
        run_test "Dex OIDC discovery endpoint" test_dex_discovery
        run_test "Dex JWKS endpoint" test_dex_jwks
        run_test "Caddy serves console" test_caddy_auth_redirect
        run_test "API via proxy with API key" test_proxy_api_with_key
    fi

    suite_end
}

# Run if executed directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    detect_config
    run_suite
    exit $TEST_SUITE_FAILED
fi
