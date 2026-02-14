#!/bin/bash
# API utilities for WIP test framework
#
# Provides authenticated API calls and response handling
#
# Usage:
#   source lib/api.sh
#   api_get "/api/def-store/terminologies"
#   api_post "/api/registry/entries/register" '{"namespace": "...", "entity_type": "..."}'

# Source dependencies if not already sourced
if [[ -z "${TESTS_DIR:-}" ]]; then
    source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
fi
if [[ -z "${API_KEY:-}" ]]; then
    source "$(dirname "${BASH_SOURCE[0]}")/config.sh"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Response State (populated after each API call)
# ─────────────────────────────────────────────────────────────────────────────

RESPONSE_CODE=""
RESPONSE_BODY=""
RESPONSE_TIME=""

# ─────────────────────────────────────────────────────────────────────────────
# Core API Functions
# ─────────────────────────────────────────────────────────────────────────────

# Make an authenticated GET request
# Usage: api_get "http://localhost:8001/api/..."
api_get() {
    local url="$1"

    local start_time
    start_time=$(get_time_ms)

    # Make request, capture response and status separately
    local response
    response=$(curl -s -w "\n%{http_code}" \
        -H "X-API-Key: $API_KEY" \
        -H "Accept: application/json" \
        "$url" 2>/dev/null)

    local end_time
    end_time=$(get_time_ms)
    RESPONSE_TIME=$((end_time - start_time))

    # Split response body and status code
    RESPONSE_CODE=$(echo "$response" | tail -1)
    RESPONSE_BODY=$(echo "$response" | sed '$d')

    log_debug "GET $url -> $RESPONSE_CODE (${RESPONSE_TIME}ms)"
}

# Make an authenticated POST request
# Usage: api_post "http://localhost:8001/api/..." '{json body}'
api_post() {
    local url="$1"
    local body="${2:-}"

    local start_time
    start_time=$(get_time_ms)

    local response
    if [[ -n "$body" ]]; then
        response=$(curl -s -w "\n%{http_code}" \
            -X POST \
            -H "X-API-Key: $API_KEY" \
            -H "Content-Type: application/json" \
            -H "Accept: application/json" \
            -d "$body" \
            "$url" 2>/dev/null)
    else
        response=$(curl -s -w "\n%{http_code}" \
            -X POST \
            -H "X-API-Key: $API_KEY" \
            -H "Accept: application/json" \
            "$url" 2>/dev/null)
    fi

    local end_time
    end_time=$(get_time_ms)
    RESPONSE_TIME=$((end_time - start_time))

    RESPONSE_CODE=$(echo "$response" | tail -1)
    RESPONSE_BODY=$(echo "$response" | sed '$d')

    log_debug "POST $url -> $RESPONSE_CODE (${RESPONSE_TIME}ms)"
}

# Make an authenticated PUT request
# Usage: api_put "http://localhost:8001/api/..." '{"json": "body"}'
api_put() {
    local url="$1"
    local body="${2:-}"

    local start_time
    start_time=$(get_time_ms)

    local response
    response=$(curl -s -w "\n%{http_code}" \
        -X PUT \
        -H "X-API-Key: $API_KEY" \
        -H "Content-Type: application/json" \
        -H "Accept: application/json" \
        -d "$body" \
        "$url" 2>/dev/null)

    local end_time
    end_time=$(get_time_ms)
    RESPONSE_TIME=$((end_time - start_time))

    RESPONSE_CODE=$(echo "$response" | tail -1)
    RESPONSE_BODY=$(echo "$response" | sed '$d')

    log_debug "PUT $url -> $RESPONSE_CODE (${RESPONSE_TIME}ms)"
}

# Make an authenticated DELETE request
# Usage: api_delete "http://localhost:8001/api/..."
api_delete() {
    local url="$1"
    local body="${2:-}"

    local start_time
    start_time=$(get_time_ms)

    local response
    if [[ -n "$body" ]]; then
        response=$(curl -s -w "\n%{http_code}" \
            -X DELETE \
            -H "X-API-Key: $API_KEY" \
            -H "Content-Type: application/json" \
            -H "Accept: application/json" \
            -d "$body" \
            "$url" 2>/dev/null)
    else
        response=$(curl -s -w "\n%{http_code}" \
            -X DELETE \
            -H "X-API-Key: $API_KEY" \
            -H "Accept: application/json" \
            "$url" 2>/dev/null)
    fi

    local end_time
    end_time=$(get_time_ms)
    RESPONSE_TIME=$((end_time - start_time))

    RESPONSE_CODE=$(echo "$response" | tail -1)
    RESPONSE_BODY=$(echo "$response" | sed '$d')

    log_debug "DELETE $url -> $RESPONSE_CODE (${RESPONSE_TIME}ms)"
}

# ─────────────────────────────────────────────────────────────────────────────
# Unauthenticated Requests (for testing auth rejection)
# ─────────────────────────────────────────────────────────────────────────────

# Make an unauthenticated GET request
api_get_noauth() {
    local url="$1"

    local response
    response=$(curl -s -w "\n%{http_code}" \
        -H "Accept: application/json" \
        "$url" 2>/dev/null)

    RESPONSE_CODE=$(echo "$response" | tail -1)
    RESPONSE_BODY=$(echo "$response" | sed '$d')

    log_debug "GET (noauth) $url -> $RESPONSE_CODE"
}

# Make request with invalid API key
api_get_badkey() {
    local url="$1"

    local response
    response=$(curl -s -w "\n%{http_code}" \
        -H "X-API-Key: invalid_key_12345" \
        -H "Accept: application/json" \
        "$url" 2>/dev/null)

    RESPONSE_CODE=$(echo "$response" | tail -1)
    RESPONSE_BODY=$(echo "$response" | sed '$d')

    log_debug "GET (badkey) $url -> $RESPONSE_CODE"
}

# ─────────────────────────────────────────────────────────────────────────────
# Health Check Helpers
# ─────────────────────────────────────────────────────────────────────────────

# Check if a service health endpoint returns healthy
# Usage: check_health "http://localhost:8001/health"
check_health() {
    local url="$1"
    local response

    response=$(curl -s -w "\n%{http_code}" "$url" 2>/dev/null)
    local code
    code=$(echo "$response" | tail -1)
    local body
    body=$(echo "$response" | sed '$d')

    if [[ "$code" == "200" ]] && echo "$body" | grep -q '"healthy"\|"status"'; then
        return 0
    fi
    return 1
}

# Wait for a service to become healthy
# Usage: wait_for_health "http://localhost:8001/health" 30
wait_for_health() {
    local url="$1"
    local timeout="${2:-30}"

    wait_for "health at $url" "$timeout" "check_health '$url'"
}

# ─────────────────────────────────────────────────────────────────────────────
# JSON Helpers
# ─────────────────────────────────────────────────────────────────────────────

# Extract a field from JSON response using Python (portable)
# Usage: json_field ".items[0].name"
json_field() {
    local path="$1"
    echo "$RESPONSE_BODY" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    keys = '$path'.strip('.').split('.')
    for key in keys:
        if key.startswith('[') and key.endswith(']'):
            data = data[int(key[1:-1])]
        elif '[' in key:
            base, idx = key.split('[')
            idx = int(idx.rstrip(']'))
            data = data[base][idx]
        else:
            data = data[key]
    print(data if not isinstance(data, (dict, list)) else json.dumps(data))
except:
    sys.exit(1)
" 2>/dev/null
}

# Count items in a JSON array
# Usage: json_count ".items"
json_count() {
    local path="$1"
    echo "$RESPONSE_BODY" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    keys = '$path'.strip('.').split('.')
    for key in keys:
        if key:
            data = data[key]
    print(len(data) if isinstance(data, list) else 0)
except:
    print(0)
" 2>/dev/null
}

# Check if JSON response contains a field
# Usage: json_has_field "items"
json_has_field() {
    local field="$1"
    echo "$RESPONSE_BODY" | grep -q "\"$field\""
}
