#!/bin/bash
# Common utilities for WIP test framework
#
# Source this file in all test scripts:
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

set -u

# ─────────────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────────────

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPTS_DIR="$(dirname "$TESTS_DIR")"
PROJECT_ROOT="$(dirname "$SCRIPTS_DIR")"

# ─────────────────────────────────────────────────────────────────────────────
# Colors
# ─────────────────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# ─────────────────────────────────────────────────────────────────────────────
# Test State
# ─────────────────────────────────────────────────────────────────────────────

TEST_SUITE_NAME=""
TEST_SUITE_PASSED=0
TEST_SUITE_FAILED=0
TEST_SUITE_SKIPPED=0
CURRENT_TEST=""
FAIL_FAST="${FAIL_FAST:-false}"
VERBOSE="${VERBOSE:-false}"

# Global counters (across all suites)
# Use := to avoid resetting when sourced multiple times
: ${TOTAL_PASSED:=0}
: ${TOTAL_FAILED:=0}
: ${TOTAL_SKIPPED:=0}

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_debug() { [[ "${VERBOSE:-false}" == "true" ]] && echo -e "${DIM}[DEBUG] $1${NC}" || true; }

# ─────────────────────────────────────────────────────────────────────────────
# Timing (cross-platform)
# ─────────────────────────────────────────────────────────────────────────────

# Get current time in milliseconds (works on macOS and Linux)
get_time_ms() {
    if [[ "$(uname)" == "Darwin" ]]; then
        # macOS: use python for milliseconds
        python3 -c 'import time; print(int(time.time() * 1000))'
    else
        # Linux: date supports %N for nanoseconds
        echo $(($(date +%s%N) / 1000000))
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Suite Management
# ─────────────────────────────────────────────────────────────────────────────

# Start a test suite
# Usage: suite_start "Suite Name"
suite_start() {
    TEST_SUITE_NAME="$1"
    TEST_SUITE_PASSED=0
    TEST_SUITE_FAILED=0
    TEST_SUITE_SKIPPED=0

    echo ""
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}  Suite: $TEST_SUITE_NAME${NC}"
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

# End a test suite and print summary
# Usage: suite_end
suite_end() {
    local total=$((TEST_SUITE_PASSED + TEST_SUITE_FAILED + TEST_SUITE_SKIPPED))

    echo ""
    echo -e "${DIM}  ──────────────────────────────────────────────────────────────${NC}"
    if [[ $TEST_SUITE_FAILED -eq 0 ]]; then
        echo -e "  ${GREEN}✓ $TEST_SUITE_NAME: $TEST_SUITE_PASSED passed${NC}"
    else
        echo -e "  ${RED}✗ $TEST_SUITE_NAME: $TEST_SUITE_PASSED passed, $TEST_SUITE_FAILED failed${NC}"
    fi
    [[ $TEST_SUITE_SKIPPED -gt 0 ]] && echo -e "  ${YELLOW}  ($TEST_SUITE_SKIPPED skipped)${NC}"

    # Update global counters
    TOTAL_PASSED=$((TOTAL_PASSED + TEST_SUITE_PASSED))
    TOTAL_FAILED=$((TOTAL_FAILED + TEST_SUITE_FAILED))
    TOTAL_SKIPPED=$((TOTAL_SKIPPED + TEST_SUITE_SKIPPED))

    return $TEST_SUITE_FAILED
}

# Skip entire suite with reason
# Usage: suite_skip "reason"
suite_skip() {
    local reason="$1"
    echo -e "  ${YELLOW}⊘ Suite skipped: $reason${NC}"
    TEST_SUITE_SKIPPED=$((TEST_SUITE_SKIPPED + 1))
    TOTAL_SKIPPED=$((TOTAL_SKIPPED + 1))
}

# ─────────────────────────────────────────────────────────────────────────────
# Test Execution
# ─────────────────────────────────────────────────────────────────────────────

# Run a single test
# Usage: run_test "test name" test_function
run_test() {
    local name="$1"
    local func="$2"
    CURRENT_TEST="$name"

    # Check fail-fast
    if [[ "$FAIL_FAST" == "true" && $TEST_SUITE_FAILED -gt 0 ]]; then
        echo -e "  ${YELLOW}⊘${NC} $name ${DIM}(skipped - fail fast)${NC}"
        TEST_SUITE_SKIPPED=$((TEST_SUITE_SKIPPED + 1))
        return 0
    fi

    # Run the test function
    local start_time
    start_time=$(get_time_ms)
    local output
    local exit_code=0

    output=$($func 2>&1) || exit_code=$?

    local end_time
    end_time=$(get_time_ms)
    local duration=$((end_time - start_time))

    if [[ $exit_code -eq 0 ]]; then
        echo -e "  ${GREEN}✓${NC} $name ${DIM}(${duration}ms)${NC}"
        TEST_SUITE_PASSED=$((TEST_SUITE_PASSED + 1))
    else
        echo -e "  ${RED}✗${NC} $name ${DIM}(${duration}ms)${NC}"
        if [[ -n "$output" ]]; then
            echo -e "    ${RED}→ $output${NC}"
        fi
        TEST_SUITE_FAILED=$((TEST_SUITE_FAILED + 1))
    fi

    return $exit_code
}

# Skip a single test with reason
# Usage: skip_test "test name" "reason"
skip_test() {
    local name="$1"
    local reason="$2"
    echo -e "  ${YELLOW}⊘${NC} $name ${DIM}($reason)${NC}"
    TEST_SUITE_SKIPPED=$((TEST_SUITE_SKIPPED + 1))
}

# ─────────────────────────────────────────────────────────────────────────────
# Timing
# ─────────────────────────────────────────────────────────────────────────────

TIMER_START=0

timer_start() {
    TIMER_START=$(date +%s)
}

timer_elapsed() {
    local now=$(date +%s)
    echo $((now - TIMER_START))
}

# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

# Wait for a condition with timeout
# Usage: wait_for "description" timeout_seconds check_command
wait_for() {
    local desc="$1"
    local timeout="$2"
    local cmd="$3"
    local start=$(date +%s)

    while true; do
        if eval "$cmd" >/dev/null 2>&1; then
            return 0
        fi

        local elapsed=$(($(date +%s) - start))
        if [[ $elapsed -ge $timeout ]]; then
            echo "Timeout waiting for: $desc"
            return 1
        fi

        sleep 1
    done
}

# Check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}
