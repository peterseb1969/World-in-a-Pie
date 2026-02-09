#!/bin/bash
# Test assertions for WIP test framework
#
# Provides assertion functions that work with API response state
#
# Usage:
#   api_get "http://localhost:8001/health"
#   assert_status 200
#   assert_json_field "status" "healthy"

# Source dependencies if not already sourced
if [[ -z "${RESPONSE_CODE:-}" ]]; then
    source "$(dirname "${BASH_SOURCE[0]}")/api.sh"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Status Assertions
# ─────────────────────────────────────────────────────────────────────────────

# Assert HTTP status code
# Usage: assert_status 200
assert_status() {
    local expected="$1"
    if [[ "$RESPONSE_CODE" != "$expected" ]]; then
        echo "Expected status $expected, got $RESPONSE_CODE"
        return 1
    fi
    return 0
}

# Assert HTTP status is in 2xx range
assert_success() {
    if [[ ! "$RESPONSE_CODE" =~ ^2[0-9][0-9]$ ]]; then
        echo "Expected 2xx status, got $RESPONSE_CODE"
        return 1
    fi
    return 0
}

# Assert HTTP status is 4xx (client error)
assert_client_error() {
    if [[ ! "$RESPONSE_CODE" =~ ^4[0-9][0-9]$ ]]; then
        echo "Expected 4xx status, got $RESPONSE_CODE"
        return 1
    fi
    return 0
}

# Assert specific auth failure (401 or 403)
assert_auth_failure() {
    if [[ "$RESPONSE_CODE" != "401" && "$RESPONSE_CODE" != "403" ]]; then
        echo "Expected 401 or 403, got $RESPONSE_CODE"
        return 1
    fi
    return 0
}

# ─────────────────────────────────────────────────────────────────────────────
# JSON Assertions
# ─────────────────────────────────────────────────────────────────────────────

# Assert JSON response has a field
# Usage: assert_has_field "items"
assert_has_field() {
    local field="$1"
    if ! json_has_field "$field"; then
        echo "Expected field '$field' in response"
        return 1
    fi
    return 0
}

# Assert JSON field equals value
# Usage: assert_json_field "status" "healthy"
assert_json_field() {
    local path="$1"
    local expected="$2"
    local actual
    actual=$(json_field "$path")

    if [[ "$actual" != "$expected" ]]; then
        echo "Expected $path='$expected', got '$actual'"
        return 1
    fi
    return 0
}

# Assert JSON field contains substring
# Usage: assert_json_contains "message" "success"
assert_json_contains() {
    local path="$1"
    local substring="$2"
    local actual
    actual=$(json_field "$path")

    if [[ "$actual" != *"$substring"* ]]; then
        echo "Expected $path to contain '$substring', got '$actual'"
        return 1
    fi
    return 0
}

# Assert JSON array has at least N items
# Usage: assert_min_count "items" 5
assert_min_count() {
    local path="$1"
    local min="$2"
    local count
    count=$(json_count "$path")

    if [[ $count -lt $min ]]; then
        echo "Expected at least $min items in $path, got $count"
        return 1
    fi
    return 0
}

# Assert JSON array has exactly N items
# Usage: assert_count "items" 10
assert_count() {
    local path="$1"
    local expected="$2"
    local count
    count=$(json_count "$path")

    if [[ $count -ne $expected ]]; then
        echo "Expected $expected items in $path, got $count"
        return 1
    fi
    return 0
}

# Assert response body contains string
# Usage: assert_body_contains "healthy"
assert_body_contains() {
    local substring="$1"
    if [[ "$RESPONSE_BODY" != *"$substring"* ]]; then
        echo "Expected response to contain '$substring'"
        return 1
    fi
    return 0
}

# Assert response body does NOT contain string
# Usage: assert_body_not_contains "error"
assert_body_not_contains() {
    local substring="$1"
    if [[ "$RESPONSE_BODY" == *"$substring"* ]]; then
        echo "Expected response NOT to contain '$substring'"
        return 1
    fi
    return 0
}

# ─────────────────────────────────────────────────────────────────────────────
# Response Time Assertions
# ─────────────────────────────────────────────────────────────────────────────

# Assert response time is under limit (milliseconds)
# Usage: assert_response_time 1000
assert_response_time() {
    local max_ms="$1"
    if [[ $RESPONSE_TIME -gt $max_ms ]]; then
        echo "Response took ${RESPONSE_TIME}ms, expected under ${max_ms}ms"
        return 1
    fi
    return 0
}

# ─────────────────────────────────────────────────────────────────────────────
# Container Assertions
# ─────────────────────────────────────────────────────────────────────────────

# Assert container is running
# Usage: assert_container_running "wip-registry"
assert_container_running() {
    local name="$1"
    # Use podman inspect for more reliable check (handles partial names, etc.)
    local state
    state=$(podman inspect --format '{{.State.Running}}' "$name" 2>/dev/null || echo "false")
    if [[ "$state" != "true" ]]; then
        echo "Container $name is not running (state: $state)"
        return 1
    fi
    return 0
}

# Assert container is healthy (if healthcheck exists)
# Usage: assert_container_healthy "wip-mongodb"
assert_container_healthy() {
    local name="$1"
    local health
    health=$(podman inspect "$name" --format "{{.State.Health.Status}}" 2>/dev/null || echo "none")

    case "$health" in
        healthy)
            return 0
            ;;
        none)
            # No healthcheck defined, just check it's running
            assert_container_running "$name"
            return $?
            ;;
        *)
            echo "Container $name health: $health"
            return 1
            ;;
    esac
}

# ─────────────────────────────────────────────────────────────────────────────
# Database Assertions
# ─────────────────────────────────────────────────────────────────────────────

# Assert PostgreSQL table has rows
# Usage: assert_pg_has_rows "documents" 10
assert_pg_has_rows() {
    local table="$1"
    local min_rows="${2:-1}"

    local count
    count=$(podman exec wip-postgres psql -U wip -d wip_reporting -t -c \
        "SELECT COUNT(*) FROM $table" 2>/dev/null | tr -d ' ')

    if [[ -z "$count" || "$count" -lt "$min_rows" ]]; then
        echo "Expected at least $min_rows rows in $table, got ${count:-0}"
        return 1
    fi
    return 0
}

# Assert MongoDB collection has documents
# Usage: assert_mongo_has_docs "wip_registry" "id_pools" 5
assert_mongo_has_docs() {
    local db="$1"
    local collection="$2"
    local min_docs="${3:-1}"

    local mongo_auth=""
    local mongo_user mongo_pass
    mongo_user=$(grep "^WIP_MONGO_USER=" "$PROJECT_ROOT/.env" 2>/dev/null | cut -d= -f2)
    mongo_pass=$(grep "^WIP_MONGO_PASSWORD=" "$PROJECT_ROOT/.env" 2>/dev/null | cut -d= -f2)
    if [[ -n "$mongo_user" && -n "$mongo_pass" ]]; then
        mongo_auth="--username $mongo_user --password $mongo_pass --authenticationDatabase admin"
    fi

    local count
    count=$(podman exec wip-mongodb mongosh --quiet $mongo_auth --eval \
        "db.getSiblingDB('$db').$collection.countDocuments()" 2>/dev/null)

    if [[ -z "$count" || "$count" -lt "$min_docs" ]]; then
        echo "Expected at least $min_docs docs in $db.$collection, got ${count:-0}"
        return 1
    fi
    return 0
}
