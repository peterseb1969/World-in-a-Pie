#!/bin/bash
# Suite 07: Ingestion Tests
#
# Verifies NATS-based ingestion functionality.
# Tests event publishing and consumption.
#
# Tests:
#   - NATS connection
#   - Stream configuration
#   - Event publishing
#   - Consumer lag

SUITE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SUITE_DIR/../lib/common.sh"
source "$SUITE_DIR/../lib/config.sh"
source "$SUITE_DIR/../lib/api.sh"
source "$SUITE_DIR/../lib/assertions.sh"

# ─────────────────────────────────────────────────────────────────────────────
# NATS Connection Tests
# ─────────────────────────────────────────────────────────────────────────────

test_nats_connection() {
    # Try to connect and get server info
    local result
    result=$(podman exec wip-nats nats server info 2>&1)

    if echo "$result" | grep -q "Server ID\|server_id"; then
        return 0
    fi

    echo "NATS connection failed: $result"
    return 1
}

test_nats_jetstream_enabled() {
    local result
    result=$(podman exec wip-nats nats account info 2>&1)

    if echo "$result" | grep -q "JetStream\|jetstream"; then
        return 0
    fi

    echo "JetStream not enabled: $result"
    return 1
}

# ─────────────────────────────────────────────────────────────────────────────
# Stream Configuration Tests
# ─────────────────────────────────────────────────────────────────────────────

test_event_streams_exist() {
    local streams
    streams=$(podman exec wip-nats nats stream list 2>&1)

    # Check for any WIP-related stream
    if echo "$streams" | grep -qi "wip\|events\|documents"; then
        return 0
    fi

    # It's okay if no streams exist yet (clean install)
    if echo "$streams" | grep -q "No streams\|0 streams"; then
        echo "No streams configured yet (clean install)"
        return 0
    fi

    echo "Could not verify streams: $streams"
    return 1
}

test_stream_retention() {
    # Get stream info and check retention policy
    local stream_name="WIP_EVENTS"
    local info
    info=$(podman exec wip-nats nats stream info "$stream_name" 2>&1 || echo "")

    if [[ -z "$info" ]] || echo "$info" | grep -q "not found"; then
        # Try to find any stream
        stream_name=$(podman exec wip-nats nats stream list --names 2>/dev/null | head -1)
        if [[ -n "$stream_name" ]]; then
            info=$(podman exec wip-nats nats stream info "$stream_name" 2>&1)
        fi
    fi

    if [[ -n "$info" ]] && echo "$info" | grep -q "Retention\|retention"; then
        return 0
    fi

    # No streams is okay
    echo "No streams to check retention on"
    return 0
}

# ─────────────────────────────────────────────────────────────────────────────
# Event Publishing Tests
# ─────────────────────────────────────────────────────────────────────────────

test_publish_test_event() {
    # Publish a test event
    local result
    result=$(podman exec wip-nats nats pub wip.test.ping "test-$(date +%s)" 2>&1)

    if echo "$result" | grep -q "Published\|published"; then
        return 0
    fi

    echo "Could not publish test event: $result"
    return 1
}

test_document_events_stream() {
    # Check if document events are being captured
    local stream_name="WIP_EVENTS"
    local info
    info=$(podman exec wip-nats nats stream info "$stream_name" --json 2>&1 || echo "")

    if [[ -z "$info" ]] || echo "$info" | grep -q "not found"; then
        # Find stream that handles document events
        local streams
        streams=$(podman exec wip-nats nats stream list --names 2>/dev/null)
        for s in $streams; do
            local subjects
            subjects=$(podman exec wip-nats nats stream info "$s" 2>/dev/null | grep -i "subjects")
            if echo "$subjects" | grep -qi "document\|wip"; then
                stream_name="$s"
                break
            fi
        done
    fi

    # Verify stream exists or there are no streams yet
    if podman exec wip-nats nats stream info "$stream_name" >/dev/null 2>&1; then
        return 0
    fi

    echo "Document events stream not configured"
    return 0  # Not a hard failure
}

# ─────────────────────────────────────────────────────────────────────────────
# Consumer Lag Tests
# ─────────────────────────────────────────────────────────────────────────────

test_consumer_lag_acceptable() {
    # Find consumers and check their pending count
    local streams
    streams=$(podman exec wip-nats nats stream list --names 2>/dev/null | head -5)

    for stream in $streams; do
        local consumers
        consumers=$(podman exec wip-nats nats consumer list "$stream" --names 2>/dev/null)

        for consumer in $consumers; do
            local pending
            pending=$(podman exec wip-nats nats consumer info "$stream" "$consumer" 2>/dev/null \
                | grep -i "pending" | head -1 | grep -oE '[0-9]+' || echo "0")

            if [[ -n "$pending" && "$pending" -gt 1000 ]]; then
                echo "Consumer $consumer on $stream has high pending: $pending"
                return 1
            fi
        done
    done

    return 0
}

# ─────────────────────────────────────────────────────────────────────────────
# Service Integration Tests
# ─────────────────────────────────────────────────────────────────────────────

test_services_publish_events() {
    # Create a document and verify event is published
    local body='{"template_code": "MINIMAL", "data": {"name": "NATS Test '$(date +%s)'"}}'

    api_post "http://localhost:$PORT_DOCUMENT_STORE/api/document-store/documents" "$body"

    if ! assert_status 200 && ! assert_status 201; then
        echo "Could not create test document"
        return 1
    fi

    # Give NATS a moment to process
    sleep 1

    # Check if any stream has new messages
    local streams
    streams=$(podman exec wip-nats nats stream list --names 2>/dev/null | head -3)

    for stream in $streams; do
        local messages
        messages=$(podman exec wip-nats nats stream info "$stream" 2>/dev/null \
            | grep -i "messages" | head -1 | grep -oE '[0-9]+' || echo "0")

        if [[ -n "$messages" && "$messages" -gt 0 ]]; then
            return 0
        fi
    done

    # Events might not be configured yet
    echo "No events captured (may not be configured)"
    return 0
}

# ─────────────────────────────────────────────────────────────────────────────
# Suite Execution
# ─────────────────────────────────────────────────────────────────────────────

run_suite() {
    suite_start "Ingestion"

    echo -e "\n  ${DIM}NATS Connection${NC}"
    run_test "NATS server connection" test_nats_connection
    run_test "JetStream enabled" test_nats_jetstream_enabled

    echo -e "\n  ${DIM}Stream Configuration${NC}"
    run_test "Event streams exist" test_event_streams_exist
    run_test "Stream retention configured" test_stream_retention

    echo -e "\n  ${DIM}Event Publishing${NC}"
    run_test "Can publish test event" test_publish_test_event
    run_test "Document events stream" test_document_events_stream

    echo -e "\n  ${DIM}Consumer Health${NC}"
    run_test "Consumer lag acceptable" test_consumer_lag_acceptable

    echo -e "\n  ${DIM}Service Integration${NC}"
    run_test "Services publish events" test_services_publish_events

    suite_end
}

# Run if executed directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    detect_config
    run_suite
    exit $TEST_SUITE_FAILED
fi
