#!/bin/bash
# Full Deployment Integration Tests
#
# Runs actual deployment combinations, validates they work end-to-end.
# Unlike test-setup-combinations.sh (which only tests argument parsing),
# this script starts real containers, seeds data, and runs the full test suite.
#
# Usage:
#   ./scripts/test-deployments.sh                    # Run all localhost tests
#   ./scripts/test-deployments.sh --test 1           # Run only test #1
#   ./scripts/test-deployments.sh --quick            # Run quick subset (core + standard)
#   ./scripts/test-deployments.sh --list             # List all tests without running
#   ./scripts/test-deployments.sh --continue-from 3  # Resume from test #3
#   ./scripts/test-deployments.sh --keep             # Keep deployment running after tests
#   ./scripts/test-deployments.sh --no-fail-fast     # Continue after test failures
#   ./scripts/test-deployments.sh --remote wip-pi.local  # Run remote host tests
#
# Requirements:
#   - Podman installed and running
#   - Python 3 with requests library (pip install requests faker)
#   - ~10-15 minutes per full test cycle

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BLUE='\033[0;34m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# Test results directory
RESULTS_DIR="$PROJECT_ROOT/testdata/deployment-tests"
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
RESULTS_FILE="$RESULTS_DIR/results_$TIMESTAMP.md"

# Configuration
STARTUP_TIMEOUT=180      # Max seconds to wait for services to start
HEALTH_CHECK_INTERVAL=5  # Seconds between health checks
API_KEY="dev_master_key_for_testing"
KEEP_DEPLOYMENT=false    # Keep deployment running after tests (for debugging)
FAIL_FAST=true           # Exit on first test failure (default: true)

# ────────────────────────────────────────────────────────────────────────────
# Test Definitions
# ────────────────────────────────────────────────────────────────────────────
# Format: "name|preset|flags|expected_services|seed_profile|modules"
#
# Localhost tests (default):
# - core: No OIDC, API key only
# - standard: OIDC + dev-tools
# - analytics: OIDC + reporting (PostgreSQL)
# - full: Everything (OIDC + reporting + files + ingest)
#
# Localhost tests: 8 tests (4 presets × 2 variants)
# Remote tests: 8 tests (4 presets × 2 variants) with --hostname
# Total: 16 tests for complete module × variant × mode coverage
#
# Format: "name|preset|flags|expected_services|seed_profile|modules"

LOCALHOST_TESTS=(
    # Core preset (no OIDC, no optional modules)
    "core-dev|core|--localhost|registry,def-store,template-store,document-store|minimal|dev-tools"
    "core-prod|core|--localhost --prod|registry,def-store,template-store,document-store|minimal|"
    # Standard preset (+ OIDC)
    "standard-dev|standard|--localhost|registry,def-store,template-store,document-store,console|standard|oidc dev-tools"
    "standard-prod|standard|--localhost --prod|registry,def-store,template-store,document-store,console|standard|oidc"
    # Analytics preset (+ reporting)
    "analytics-dev|analytics|--localhost|registry,def-store,template-store,document-store,console,reporting-sync|standard|oidc reporting dev-tools"
    "analytics-prod|analytics|--localhost --prod|registry,def-store,template-store,document-store,console,reporting-sync|standard|oidc reporting"
    # Full preset (+ files + ingest)
    "full-dev|full|--localhost|registry,def-store,template-store,document-store,console,reporting-sync,ingest-gateway|standard|oidc reporting files ingest dev-tools"
    "full-prod|full|--localhost --prod|registry,def-store,template-store,document-store,console,reporting-sync,ingest-gateway|standard|oidc reporting files ingest"
)

# Remote test presets - converted to full test definitions with --hostname at runtime
# Format: "name|preset|variant|services|seed_profile|modules"
REMOTE_TEST_PRESETS=(
    # Core preset
    "core-remote-dev|core|dev|registry,def-store,template-store,document-store|minimal|dev-tools"
    "core-remote-prod|core|prod|registry,def-store,template-store,document-store|minimal|"
    # Standard preset
    "standard-remote-dev|standard|dev|registry,def-store,template-store,document-store,console|standard|oidc dev-tools"
    "standard-remote-prod|standard|prod|registry,def-store,template-store,document-store,console|standard|oidc"
    # Analytics preset
    "analytics-remote-dev|analytics|dev|registry,def-store,template-store,document-store,console,reporting-sync|standard|oidc reporting dev-tools"
    "analytics-remote-prod|analytics|prod|registry,def-store,template-store,document-store,console,reporting-sync|standard|oidc reporting"
    # Full preset
    "full-remote-dev|full|dev|registry,def-store,template-store,document-store,console,reporting-sync,ingest-gateway|standard|oidc reporting files ingest dev-tools"
    "full-remote-prod|full|prod|registry,def-store,template-store,document-store,console,reporting-sync,ingest-gateway|standard|oidc reporting files ingest"
)

# Active test set (populated based on mode)
TESTS=()

# Quick subset for faster iteration
QUICK_TESTS=(0 2)  # core-dev, standard-dev

# Remote hostname (if testing remote)
REMOTE_HOSTNAME=""

# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

log() { echo -e "${GREEN}[TEST]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step() { echo -e "${BLUE}[STEP]${NC} $1"; }
log_dim() { echo -e "${DIM}       $1${NC}"; }

# Parse test definition
parse_test() {
    local def="$1"
    IFS='|' read -r TEST_NAME TEST_PRESET TEST_FLAGS TEST_SERVICES TEST_SEED_PROFILE TEST_MODULES <<< "$def"
}

# Clean up all WIP containers and data (fast parallel removal)
# CRITICAL: This must completely wipe data for reproducible tests
cleanup_all() {
    log_step "Cleaning up existing deployment..."

    # Remove containers in multiple passes to handle dependencies
    # (e.g., wip-mongo-express depends on wip-mongodb and must be removed first)
    local max_passes=3
    local pass=1

    while [ $pass -le $max_passes ]; do
        local containers
        containers=$(podman ps -a --filter "name=wip-" --format "{{.Names}}" 2>/dev/null | tr '\n' ' ')

        if [ -z "$containers" ]; then
            break
        fi

        # Force remove all containers in one command
        # shellcheck disable=SC2086
        podman rm -f $containers 2>/dev/null || true
        pass=$((pass + 1))
    done

    # Remove WIP volumes in parallel
    local volumes
    volumes=$(podman volume ls --format "{{.Name}}" 2>/dev/null | grep -E "wip|worldinpie" | tr '\n' ' ')
    if [ -n "$volumes" ]; then
        # shellcheck disable=SC2086
        podman volume rm -f $volumes 2>/dev/null || true
    fi

    # CRITICAL: Clean data directories for fresh state between tests
    local data_dir="$PROJECT_ROOT/data"
    if [ -d "$data_dir" ]; then
        log_dim "Wiping data directories for clean slate..."

        # Remove contents of each data directory
        for subdir in mongodb nats postgres dex minio caddy; do
            if [ -d "$data_dir/$subdir" ]; then
                # Use podman unshare for dirs owned by container UIDs (rootless podman)
                podman unshare rm -rf "$data_dir/$subdir"/* 2>/dev/null \
                    || rm -rf "$data_dir/$subdir"/* 2>/dev/null || true
                podman unshare rm -rf "$data_dir/$subdir"/.[!.]* 2>/dev/null \
                    || rm -rf "$data_dir/$subdir"/.[!.]* 2>/dev/null || true
            fi
        done

        # Verify MongoDB data is gone (most critical for reproducibility)
        if [ -d "$data_dir/mongodb" ] && [ "$(ls -A "$data_dir/mongodb" 2>/dev/null)" ]; then
            log_error "CRITICAL: Failed to clean MongoDB data directory!"
            log_error "Data from previous test may affect results."
            log_error "Manual cleanup required: rm -rf $data_dir/mongodb/*"
            return 1
        fi

        # Verify NATS JetStream data is gone
        if [ -d "$data_dir/nats/jetstream" ] && [ "$(ls -A "$data_dir/nats/jetstream" 2>/dev/null)" ]; then
            log_error "CRITICAL: Failed to clean NATS JetStream data directory!"
            log_error "Stream conflicts may cause test failures."
            log_error "Manual cleanup required: rm -rf $data_dir/nats/jetstream/*"
            return 1
        fi
    fi

    # Verify no WIP containers remain
    local remaining
    remaining=$(podman ps -a --filter "name=wip-" --format "{{.Names}}" 2>/dev/null | wc -l | tr -d ' ')
    if [ "$remaining" -gt 0 ]; then
        log_error "CRITICAL: $remaining WIP containers still exist after cleanup!"
        log_error "Cannot proceed with clean tests."
        podman ps -a --filter "name=wip-" --format "table {{.Names}}\t{{.State}}" 2>/dev/null
        return 1
    fi

    log "Cleanup complete - verified clean state"
}

# Run setup.sh with given parameters
run_setup() {
    local preset="$1"
    local flags="$2"

    log_step "Running setup.sh --preset $preset $flags --clean -y"

    # shellcheck disable=SC2086
    if "$PROJECT_ROOT/scripts/setup.sh" --preset "$preset" $flags --clean -y; then
        log "Setup completed successfully"
        # Read the actual API key from .env (may differ in prod mode)
        if [ -f "$PROJECT_ROOT/.env" ]; then
            local env_api_key
            env_api_key=$(grep "^API_KEY=" "$PROJECT_ROOT/.env" | cut -d= -f2)
            if [ -n "$env_api_key" ]; then
                API_KEY="$env_api_key"
                log_dim "Using API key from .env"
            fi
        fi
        return 0
    else
        log_error "Setup failed!"
        return 1
    fi
}

# Wait for all expected services to be healthy
wait_for_services() {
    local services="$1"
    local start_time=$(date +%s)

    log_step "Waiting for services to be healthy (timeout: ${STARTUP_TIMEOUT}s)..."
    log_dim "Expected: $services"

    while true; do
        local elapsed=$(($(date +%s) - start_time))
        if [ $elapsed -gt $STARTUP_TIMEOUT ]; then
            log_error "Timeout waiting for services after ${STARTUP_TIMEOUT}s"
            return 1
        fi

        # Check health endpoints
        local all_healthy=true
        local status_line=""

        # Registry (port 8001)
        if echo "$services" | grep -q "registry"; then
            if curl -sf http://localhost:8001/health > /dev/null 2>&1; then
                status_line+=" registry:OK"
            else
                status_line+=" registry:WAIT"
                all_healthy=false
            fi
        fi

        # Def-Store (port 8002)
        if echo "$services" | grep -q "def-store"; then
            if curl -sf http://localhost:8002/health > /dev/null 2>&1; then
                status_line+=" def-store:OK"
            else
                status_line+=" def-store:WAIT"
                all_healthy=false
            fi
        fi

        # Template-Store (port 8003)
        if echo "$services" | grep -q "template-store"; then
            if curl -sf http://localhost:8003/health > /dev/null 2>&1; then
                status_line+=" template-store:OK"
            else
                status_line+=" template-store:WAIT"
                all_healthy=false
            fi
        fi

        # Document-Store (port 8004)
        if echo "$services" | grep -q "document-store"; then
            if curl -sf http://localhost:8004/health > /dev/null 2>&1; then
                status_line+=" document-store:OK"
            else
                status_line+=" document-store:WAIT"
                all_healthy=false
            fi
        fi

        # Reporting-Sync (port 8005)
        if echo "$services" | grep -q "reporting-sync"; then
            if curl -sf http://localhost:8005/health > /dev/null 2>&1; then
                status_line+=" reporting-sync:OK"
            else
                status_line+=" reporting-sync:WAIT"
                all_healthy=false
            fi
        fi

        # Console (port 3000 or 8443)
        if echo "$services" | grep -q "console"; then
            # Try both direct port and Caddy proxy
            if curl -sf http://localhost:3000 > /dev/null 2>&1 || \
               curl -sfk https://localhost:8443 > /dev/null 2>&1; then
                status_line+=" console:OK"
            else
                status_line+=" console:WAIT"
                all_healthy=false
            fi
        fi

        log_dim "(+${elapsed}s)$status_line"

        if $all_healthy; then
            log "All services healthy after ${elapsed}s"
            return 0
        fi

        sleep $HEALTH_CHECK_INTERVAL
    done
}

# Initialize WIP namespaces
init_namespaces() {
    log_step "Initializing WIP namespaces..."

    local response
    response=$(curl -s -X POST "http://localhost:8001/api/registry/namespaces/initialize-wip" \
        -H "X-API-Key: $API_KEY" \
        -H "Content-Type: application/json")

    if echo "$response" | grep -q '"status"'; then
        log "Namespaces initialized"
        return 0
    else
        log_warn "Namespace init response: $response"
        # May already exist, continue anyway
        return 0
    fi
}

# Seed output storage (populated by run_seed)
SEED_OUTPUT=""

# Run seed script (with venv activation)
run_seed() {
    local profile="$1"
    local test_name="$2"

    log_step "Seeding data with profile: $profile"

    # Source venv if available (provides requests, faker)
    local venv_activate="$PROJECT_ROOT/.venv/bin/activate"
    if [ -f "$venv_activate" ]; then
        # shellcheck disable=SC1090
        source "$venv_activate"
    fi

    # Capture seed output for reporting
    local seed_log="$RESULTS_DIR/${test_name}_seed.log"
    if python3 "$PROJECT_ROOT/scripts/seed_comprehensive.py" --profile "$profile" --api-key "$API_KEY" 2>&1 | tee "$seed_log"; then
        log "Seeding completed successfully"
        # Extract summary stats from seed output
        SEED_OUTPUT=$(grep -E "(created|SEEDING COMPLETE|Terminologies:|Templates:|Documents:)" "$seed_log" | tail -20)
        return 0
    else
        log_error "Seeding failed!"
        return 1
    fi
}

# Run the test framework against the current deployment
run_test_framework() {
    local test_name="$1"
    local modules="$2"
    local is_remote="${3:-false}"
    local hostname="${4:-localhost}"

    log_step "Running test framework..."

    # Set up environment for test framework
    export TEST_API_KEY="$API_KEY"
    export TEST_MODULES="$modules"

    if [[ "$is_remote" == "true" ]]; then
        export TEST_HOSTNAME="$hostname"
        export TEST_LOCALHOST_MODE="false"
    else
        export TEST_HOSTNAME="localhost"
        export TEST_LOCALHOST_MODE="true"
    fi

    # Create output file for this test
    local test_output="$RESULTS_DIR/${test_name}_tests.json"
    local test_log="$RESULTS_DIR/${test_name}_output.log"

    # Clear old log file before test run
    : > "$test_log"

    # Build suite list based on active modules
    # Always run: deployment, auth, core-apis, seeding
    local suites="deployment,auth,core-apis,seeding"

    # Add module-specific suites based on what's deployed
    # This ensures every deployed module gets tested
    if [[ "$TEST_MODULES" == *"reporting"* ]]; then
        suites="$suites,reporting"
    fi
    if [[ "$TEST_MODULES" == *"files"* ]]; then
        suites="$suites,files"
    fi
    if [[ "$TEST_MODULES" == *"ingest"* ]]; then
        suites="$suites,ingest"
    fi

    # Always run integration tests at the end
    suites="$suites,integration"

    # Run the test framework with appropriate suites
    # Convert comma-separated to space-separated for positional args
    local suite_args="${suites//,/ }"

    # Run tests and capture exit code properly (pipe to tee loses it)
    set -o pipefail
    local test_exit=0
    "$PROJECT_ROOT/scripts/tests/run-tests.sh" \
        --skip-seed \
        --output "$test_output" \
        $suite_args \
        2>&1 | tee "$test_log"
    test_exit=$?
    set +o pipefail

    # Parse results from JSON output
    if [[ -f "$test_output" ]]; then
        local passed failed
        passed=$(grep -o '"passed": [0-9]*' "$test_output" | grep -o '[0-9]*' || echo "0")
        failed=$(grep -o '"failed": [0-9]*' "$test_output" | grep -o '[0-9]*' | head -1 || echo "0")
        log_dim "Test results: $passed passed, $failed failed"
    fi

    # Clean up env vars (keep TEST_MODULES for write_result)
    unset TEST_API_KEY TEST_HOSTNAME TEST_LOCALHOST_MODE

    return $test_exit
}

# Get container stats
get_container_stats() {
    log_step "Container resource usage:"
    podman stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" 2>/dev/null | grep wip- || true
}

# Write test result to report
write_result() {
    local test_name="$1"
    local status="$2"
    local duration="$3"
    local notes="$4"

    local test_json="$RESULTS_DIR/${test_name}_tests.json"
    local seed_log="$RESULTS_DIR/${test_name}_seed.log"
    local test_log="$RESULTS_DIR/${test_name}_output.log"

    {
        echo ""
        echo "## Test: $test_name"
        echo ""
        echo "| Property | Value |"
        echo "|----------|-------|"
        echo "| Status | **$status** |"
        echo "| Duration | ${duration}s |"
        echo "| Timestamp | $(date '+%Y-%m-%d %H:%M:%S') |"
        echo "| Preset | ${TEST_PRESET:-n/a} |"
        echo "| Modules | ${TEST_MODULES:-n/a} |"
        if [ -n "$notes" ]; then
            echo "| Notes | $notes |"
        fi
        echo ""

        # Include seed summary if available
        if [[ -f "$seed_log" ]]; then
            echo "### Seeding Summary"
            echo ""
            # Extract key stats from seed output
            local term_count tpl_count doc_count
            term_count=$(grep -o 'Terminologies: [0-9]* created' "$seed_log" | grep -o '[0-9]*' || echo "?")
            tpl_count=$(grep -o 'Templates: [0-9]* created' "$seed_log" | grep -o '[0-9]*' || echo "?")
            doc_count=$(grep -o 'Documents: [0-9]* created' "$seed_log" | grep -o '[0-9]*' || echo "?")

            echo "| Entity | Created |"
            echo "|--------|---------|"
            echo "| Terminologies | $term_count |"
            echo "| Templates | $tpl_count |"
            echo "| Documents | $doc_count |"
            echo ""
        fi

        # Include test framework results if available
        if [[ -f "$test_json" ]]; then
            echo "### Test Results"
            echo ""
            # Parse and display nicely
            local passed failed total
            passed=$(sed -n 's/.*"passed": *\([0-9]*\).*/\1/p' "$test_json" | head -1)
            failed=$(sed -n 's/.*"failed": *\([0-9]*\).*/\1/p' "$test_json" | head -1)
            total=$(sed -n 's/.*"total": *\([0-9]*\).*/\1/p' "$test_json" | head -1)

            echo "| Metric | Value |"
            echo "|--------|-------|"
            echo "| Total Tests | $total |"
            echo "| Passed | $passed |"
            echo "| Failed | $failed |"
            echo ""

            # Data stats
            local ns term terms tpl docs
            ns=$(sed -n 's/.*"namespaces": *\([0-9]*\).*/\1/p' "$test_json" | head -1)
            term=$(sed -n 's/.*"terminologies": *\([0-9]*\).*/\1/p' "$test_json" | head -1)
            terms=$(sed -n 's/.*"terms": *\([0-9]*\).*/\1/p' "$test_json" | head -1)
            tpl=$(sed -n 's/.*"templates": *\([0-9]*\).*/\1/p' "$test_json" | head -1)
            docs=$(sed -n 's/.*"documents": *\([0-9]*\).*/\1/p' "$test_json" | head -1)

            echo "### Data Statistics (after tests)"
            echo ""
            echo "| Entity | Count |"
            echo "|--------|-------|"
            echo "| Namespaces | $ns |"
            echo "| Terminologies | $term |"
            echo "| Terms | $terms |"
            echo "| Templates | $tpl |"
            echo "| Documents | $docs |"
            echo ""
        fi

        # Include failed tests detail if any (strip ANSI codes for clean output)
        if [[ -f "$test_log" ]] && grep -q "✗" "$test_log"; then
            echo "### Failed Tests"
            echo ""
            echo '```'
            grep -A1 "✗" "$test_log" | grep -v "^--$" | sed 's/\x1b\[[0-9;]*m//g' | head -30
            echo '```'
            echo ""
        fi

        echo "### Container Stats"
        echo ""
        echo '```'
        podman stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" 2>/dev/null | grep wip- || echo "(no containers)"
        echo '```'
        echo ""

        # Include full seed log
        if [[ -f "$seed_log" ]] && [[ -s "$seed_log" ]]; then
            echo "<details>"
            echo "<summary><strong>Seed Log</strong> (click to expand)</summary>"
            echo ""
            echo '```'
            sed 's/\x1b\[[0-9;]*m//g' "$seed_log"
            echo '```'
            echo "</details>"
            echo ""
        fi

        # Include full test output log
        if [[ -f "$test_log" ]] && [[ -s "$test_log" ]]; then
            echo "<details>"
            echo "<summary><strong>Test Output</strong> (click to expand)</summary>"
            echo ""
            echo '```'
            sed 's/\x1b\[[0-9;]*m//g' "$test_log"
            echo '```'
            echo "</details>"
            echo ""
        fi
    } >> "$RESULTS_FILE"
}

# ────────────────────────────────────────────────────────────────────────────
# Main Test Runner
# ────────────────────────────────────────────────────────────────────────────

run_single_test() {
    local test_idx="$1"
    local test_def="${TESTS[$test_idx]}"

    parse_test "$test_def"

    echo ""
    echo -e "${BOLD}════════════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}  Test $((test_idx + 1))/${#TESTS[@]}: $TEST_NAME${NC}"
    echo -e "${BOLD}════════════════════════════════════════════════════════════════${NC}"
    echo ""
    log_dim "Preset:   $TEST_PRESET"
    log_dim "Flags:    $TEST_FLAGS"
    log_dim "Services: $TEST_SERVICES"
    log_dim "Modules:  $TEST_MODULES"
    log_dim "Seed:     $TEST_SEED_PROFILE"
    echo ""

    local test_start=$(date +%s)
    local status="PASS"
    local notes=""

    # Step 1: Clean up (CRITICAL - must succeed for reproducible tests)
    if ! cleanup_all; then
        status="FAIL"
        notes="Cleanup failed - cannot guarantee clean slate for test"
        local duration=$(($(date +%s) - test_start))
        write_result "$TEST_NAME" "$status" "$duration" "$notes"
        return 1
    fi

    # Step 2: Run setup
    if ! run_setup "$TEST_PRESET" "$TEST_FLAGS"; then
        status="FAIL"
        notes="Setup failed"
        local duration=$(($(date +%s) - test_start))
        write_result "$TEST_NAME" "$status" "$duration" "$notes"
        return 1
    fi

    # Step 3: Wait for services
    if ! wait_for_services "$TEST_SERVICES"; then
        status="FAIL"
        notes="Services failed to start within timeout"
        local duration=$(($(date +%s) - test_start))
        write_result "$TEST_NAME" "$status" "$duration" "$notes"
        return 1
    fi

    # Step 4: Initialize namespaces
    if ! init_namespaces; then
        status="FAIL"
        notes="Namespace initialization failed"
        local duration=$(($(date +%s) - test_start))
        write_result "$TEST_NAME" "$status" "$duration" "$notes"
        return 1
    fi

    # Step 5: Seed data
    if ! run_seed "$TEST_SEED_PROFILE" "$TEST_NAME"; then
        status="FAIL"
        notes="Seeding failed"
        local duration=$(($(date +%s) - test_start))
        write_result "$TEST_NAME" "$status" "$duration" "$notes"
        return 1
    fi

    # Step 6: Run test framework
    local is_remote="false"
    local test_hostname="localhost"
    if [[ -n "$REMOTE_HOSTNAME" ]]; then
        is_remote="true"
        test_hostname="$REMOTE_HOSTNAME"
    fi

    if ! run_test_framework "$TEST_NAME" "$TEST_MODULES" "$is_remote" "$test_hostname"; then
        status="FAIL"
        notes="Test framework reported failures"
    fi

    # Step 7: Get stats
    get_container_stats

    local duration=$(($(date +%s) - test_start))

    echo ""
    if [ "$status" = "PASS" ]; then
        echo -e "${GREEN}${BOLD}  TEST PASSED${NC} (${duration}s)"
    else
        echo -e "${RED}${BOLD}  TEST FAILED${NC}: $notes (${duration}s)"
    fi
    echo ""

    write_result "$TEST_NAME" "$status" "$duration" "$notes"

    [ "$status" = "PASS" ] && return 0 || return 1
}

list_tests() {
    # Populate TESTS if not already done
    if [[ ${#TESTS[@]} -eq 0 ]]; then
        if [[ -n "$REMOTE_HOSTNAME" ]]; then
            for preset in "${REMOTE_TEST_PRESETS[@]}"; do
                IFS='|' read -r name preset_name variant services seed_profile modules <<< "$preset"
                local flags="--hostname $REMOTE_HOSTNAME"
                [[ "$variant" == "prod" ]] && flags="$flags --prod"
                TESTS+=("${name}|${preset_name}|${flags}|${services}|${seed_profile}|${modules}")
            done
        else
            TESTS=("${LOCALHOST_TESTS[@]}")
        fi
    fi

    echo ""
    echo -e "${BOLD}Available Deployment Tests${NC}"
    if [[ -n "$REMOTE_HOSTNAME" ]]; then
        echo -e "${DIM}Mode: Remote ($REMOTE_HOSTNAME)${NC}"
    else
        echo -e "${DIM}Mode: Localhost${NC}"
    fi
    echo ""
    for i in "${!TESTS[@]}"; do
        parse_test "${TESTS[$i]}"
        echo -e "  ${CYAN}$((i + 1))${NC}. $TEST_NAME"
        echo -e "     ${DIM}--preset $TEST_PRESET $TEST_FLAGS${NC}"
        echo -e "     ${DIM}Modules: $TEST_MODULES${NC}"
    done
    echo ""
    echo -e "${DIM}Quick tests (--quick): indices ${QUICK_TESTS[*]}${NC}"
    echo ""
}

show_usage() {
    echo "Usage: $0 [options]"
    echo ""
    echo "Options:"
    echo "  --test N              Run only test #N"
    echo "  --quick               Run quick subset (core-dev, standard-dev)"
    echo "  --list                List all tests without running"
    echo "  --continue-from N     Resume from test #N"
    echo "  --remote HOSTNAME     Run remote deployment tests against HOSTNAME"
    echo "  --keep                Keep deployment running after tests (for debugging)"
    echo "  --no-fail-fast        Continue running tests after a failure"
    echo "  --help                Show this help"
    echo ""
    echo "Examples:"
    echo "  $0                    # Run all localhost tests"
    echo "  $0 --quick            # Run quick localhost tests"
    echo "  $0 --test 4 --keep    # Run test #4 and keep containers running"
    echo "  $0 --remote wip-pi.local  # Run remote tests against wip-pi.local"
    echo ""
}

main() {
    local run_test=""
    local quick_mode=false
    local continue_from=0
    local list_only=false

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --test)
                run_test="$2"
                shift 2
                ;;
            --quick)
                quick_mode=true
                shift
                ;;
            --list)
                list_only=true
                shift
                ;;
            --continue-from)
                continue_from="$2"
                shift 2
                ;;
            --remote)
                REMOTE_HOSTNAME="$2"
                shift 2
                ;;
            --keep)
                KEEP_DEPLOYMENT=true
                shift
                ;;
            --no-fail-fast)
                FAIL_FAST=false
                shift
                ;;
            --help)
                show_usage
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                show_usage
                exit 1
                ;;
        esac
    done

    # Populate TESTS array based on mode
    if [[ -n "$REMOTE_HOSTNAME" ]]; then
        # Remote tests: convert presets to full test definitions with --hostname
        for preset in "${REMOTE_TEST_PRESETS[@]}"; do
            IFS='|' read -r name preset_name variant services seed_profile modules <<< "$preset"
            local flags="--hostname $REMOTE_HOSTNAME"
            [[ "$variant" == "prod" ]] && flags="$flags --prod"
            TESTS+=("${name}|${preset_name}|${flags}|${services}|${seed_profile}|${modules}")
        done
        log "Remote mode: testing against $REMOTE_HOSTNAME (8 tests: 4 presets × 2 variants)"
    else
        # Localhost tests
        TESTS=("${LOCALHOST_TESTS[@]}")
    fi

    # Handle --list
    if $list_only; then
        list_tests
        exit 0
    fi

    # Create results directory
    mkdir -p "$RESULTS_DIR"

    # Initialize results file
    {
        echo "# WIP Deployment Test Results"
        echo ""
        echo "- **Date:** $(date '+%Y-%m-%d %H:%M:%S')"
        echo "- **Platform:** $(uname -s) $(uname -m)"
        echo "- **Podman:** $(podman --version 2>/dev/null || echo 'not installed')"
        if [[ -n "$REMOTE_HOSTNAME" ]]; then
            echo "- **Mode:** Remote ($REMOTE_HOSTNAME)"
        else
            echo "- **Mode:** Localhost"
        fi
        echo ""
    } > "$RESULTS_FILE"

    echo ""
    echo -e "${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}║         WIP Deployment Integration Tests                     ║${NC}"
    echo -e "${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "Results will be saved to: ${CYAN}$RESULTS_FILE${NC}"
    echo ""

    local pass=0
    local fail=0
    local total=0

    # Determine which tests to run
    local test_indices=()

    if [ -n "$run_test" ]; then
        # Single test
        test_indices+=($((run_test - 1)))
    elif $quick_mode; then
        # Quick subset
        test_indices=("${QUICK_TESTS[@]}")
    else
        # All tests (optionally starting from continue_from)
        for i in "${!TESTS[@]}"; do
            if [ "$i" -ge "$((continue_from - 1))" ] || [ "$continue_from" -eq 0 ]; then
                test_indices+=("$i")
            fi
        done
    fi

    # Run tests
    for idx in "${test_indices[@]}"; do
        if run_single_test "$idx"; then
            pass=$((pass + 1))
        else
            fail=$((fail + 1))
            if $FAIL_FAST; then
                log_error "Test failed - exiting (use --no-fail-fast to continue)"
                total=$((total + 1))
                break
            fi
        fi
        total=$((total + 1))
    done

    # Summary
    {
        echo ""
        echo "---"
        echo ""
        echo "# Summary"
        echo ""
        echo "- **Total:** $total"
        echo "- **Passed:** $pass"
        echo "- **Failed:** $fail"
    } >> "$RESULTS_FILE"

    echo ""
    echo -e "${BOLD}════════════════════════════════════════════════════════════════${NC}"
    if [ "$fail" -eq 0 ]; then
        echo -e "  ${GREEN}All $total tests passed${NC}"
    else
        echo -e "  ${GREEN}$pass passed${NC}, ${RED}$fail failed${NC} (out of $total)"
    fi
    echo -e "${BOLD}════════════════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "Full results: ${CYAN}$RESULTS_FILE${NC}"
    echo ""

    # Cleanup after all tests (unless --keep was specified)
    if $KEEP_DEPLOYMENT; then
        log "Keeping deployment running (--keep specified)"
        echo ""
        echo -e "${CYAN}Containers still running:${NC}"
        podman ps --filter "name=wip-" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null | head -20
        echo ""
        echo -e "${DIM}To clean up manually: podman rm -f --depend \$(podman ps -aq --filter name=wip-)${NC}"
        echo ""
    else
        log "Final cleanup..."
        cleanup_all
    fi

    exit $fail
}

main "$@"
