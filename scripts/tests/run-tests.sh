#!/bin/bash
# WIP Test Runner
#
# Orchestrates test suite execution with proper sequencing and reporting.
#
# Usage:
#   ./run-tests.sh                    # Run all suites
#   ./run-tests.sh deployment auth    # Run specific suites
#   ./run-tests.sh --list             # List available suites
#   ./run-tests.sh --quick            # Run quick validation only
#   ./run-tests.sh --skip-seed        # Skip seeding tests
#   ./run-tests.sh --output results.json  # Write machine-readable results
#
# Environment:
#   DEBUG=1                  Enable debug output
#   TEST_API_KEY=xxx         Override API key
#   TEST_HOSTNAME=xxx        Override hostname (for remote tests)
#   TEST_LOCALHOST_MODE=xxx  Override localhost mode (true/false)
#   TEST_MODULES=xxx         Override active modules (space-separated)

set -eo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$TESTS_DIR/lib/common.sh"
source "$TESTS_DIR/lib/config.sh"

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

# Map suite name to file
get_suite_file() {
    local suite="$1"
    case "$suite" in
        deployment)  echo "01-deployment.sh" ;;
        auth)        echo "02-auth.sh" ;;
        core-apis)   echo "03-core-apis.sh" ;;
        seeding)     echo "04-seeding.sh" ;;
        reporting)   echo "05-reporting.sh" ;;
        files)       echo "06-files.sh" ;;
        ingest)      echo "07-ingest.sh" ;;
        integration) echo "08-integration.sh" ;;
        *)           echo "" ;;
    esac
}

# Check if suite name is valid
is_valid_suite() {
    local suite="$1"
    [[ -n "$(get_suite_file "$suite")" ]]
}

# Suite execution order (default: all suites)
SUITE_ORDER="deployment auth core-apis seeding reporting files ingest integration"

# Quick test suites (fast validation)
QUICK_SUITES="deployment auth core-apis"

# Global state (use := to avoid resetting if already set by common.sh)
: ${TOTAL_PASSED:=0}
: ${TOTAL_FAILED:=0}
: ${TOTAL_SKIPPED:=0}
SUITES_RUN=0
SUITES_FAILED=0
START_TIME=""
SKIP_SEED=false
RUN_QUICK=false
OUTPUT_FILE=""
QUIET_MODE=false

# Data stats (populated by collect_data_stats)
DATA_STATS_TERMINOLOGIES=0
DATA_STATS_TERMS=0
DATA_STATS_TEMPLATES=0
DATA_STATS_DOCUMENTS=0
DATA_STATS_NAMESPACES=0

# ─────────────────────────────────────────────────────────────────────────────
# Argument Parsing
# ─────────────────────────────────────────────────────────────────────────────

show_usage() {
    cat << 'EOF'
WIP Test Runner

Usage:
  ./run-tests.sh [OPTIONS] [SUITE...]

Options:
  --list, -l            List available test suites
  --quick, -q           Run quick validation only (deployment, auth, core-apis)
  --skip-seed, -s       Skip seeding tests (use existing data)
  --output FILE         Write machine-readable JSON results to FILE
  --quiet               Minimal output (for CI/automation)
  --debug, -d           Enable debug output
  --help, -h            Show this help

Suites:
  deployment   Container health and service availability
  auth         API key and OIDC authentication
  core-apis    CRUD operations on all services
  seeding      Seed script and data verification
  reporting    PostgreSQL sync (if reporting module active)
  files        MinIO file storage (if files module active)
  ingest       NATS ingestion and events
  integration  End-to-end workflows

Examples:
  ./run-tests.sh                        # Run all suites
  ./run-tests.sh deployment auth        # Run specific suites
  ./run-tests.sh --quick                # Fast validation
  ./run-tests.sh --output results.json  # Output JSON results

Environment:
  DEBUG=1                  Enable debug output
  TEST_API_KEY=xxx         Override API key
  TEST_HOSTNAME=xxx        Override hostname
  TEST_LOCALHOST_MODE=xxx  true/false for localhost vs remote
  TEST_MODULES=xxx         Override active modules (space-separated)
EOF
}

list_suites() {
    echo -e "${BOLD}Available Test Suites${NC}"
    echo ""
    for suite in $SUITE_ORDER; do
        local desc=""
        case $suite in
            deployment)  desc="Container health and service availability" ;;
            auth)        desc="API key and OIDC authentication" ;;
            core-apis)   desc="CRUD operations on all services" ;;
            seeding)     desc="Seed script and data verification" ;;
            reporting)   desc="PostgreSQL sync (requires reporting module)" ;;
            files)       desc="MinIO file storage (requires files module)" ;;
            ingest)      desc="NATS ingestion and events" ;;
            integration) desc="End-to-end workflows" ;;
        esac
        printf "  %-12s %s\n" "$suite" "$desc"
    done
    echo ""
    echo -e "${DIM}Quick suites: ${QUICK_SUITES}${NC}"
}

parse_args() {
    local suites_to_run=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --list|-l)
                list_suites
                exit 0
                ;;
            --quick|-q)
                RUN_QUICK=true
                shift
                ;;
            --skip-seed|-s)
                SKIP_SEED=true
                shift
                ;;
            --output)
                OUTPUT_FILE="$2"
                shift 2
                ;;
            --quiet)
                QUIET_MODE=true
                shift
                ;;
            --debug|-d)
                export DEBUG=1
                shift
                ;;
            --help|-h)
                show_usage
                exit 0
                ;;
            -*)
                echo "Unknown option: $1"
                show_usage
                exit 1
                ;;
            *)
                # Suite name
                if is_valid_suite "$1"; then
                    suites_to_run="$suites_to_run $1"
                else
                    echo "Unknown suite: $1"
                    echo "Use --list to see available suites"
                    exit 1
                fi
                shift
                ;;
        esac
    done

    # Determine which suites to run
    if [[ -n "$suites_to_run" ]]; then
        SUITE_ORDER="$suites_to_run"
    elif [[ "$RUN_QUICK" == "true" ]]; then
        SUITE_ORDER="$QUICK_SUITES"
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Environment Overrides
# ─────────────────────────────────────────────────────────────────────────────

# Apply environment variable overrides (for test-deployments.sh integration)
apply_env_overrides() {
    if [[ -n "${TEST_API_KEY:-}" ]]; then
        API_KEY="$TEST_API_KEY"
        log_debug "Override: API_KEY from TEST_API_KEY"
    fi
    if [[ -n "${TEST_HOSTNAME:-}" ]]; then
        HOSTNAME="$TEST_HOSTNAME"
        log_debug "Override: HOSTNAME=$HOSTNAME"
    fi
    if [[ -n "${TEST_LOCALHOST_MODE:-}" ]]; then
        LOCALHOST_MODE="$TEST_LOCALHOST_MODE"
        log_debug "Override: LOCALHOST_MODE=$LOCALHOST_MODE"
    fi
    if [[ -n "${TEST_MODULES:-}" ]]; then
        ACTIVE_MODULES="$TEST_MODULES"
        log_debug "Override: ACTIVE_MODULES=$ACTIVE_MODULES"
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Suite Execution
# ─────────────────────────────────────────────────────────────────────────────

run_suite_file() {
    local suite_name="$1"
    local suite_file_name
    suite_file_name=$(get_suite_file "$suite_name")
    local suite_file="$TESTS_DIR/suites/$suite_file_name"

    if [[ ! -f "$suite_file" ]]; then
        echo -e "${YELLOW}Suite file not found: $suite_file${NC}"
        return 1
    fi

    # Skip seeding if requested
    if [[ "$SKIP_SEED" == "true" && "$suite_name" == "seeding" ]]; then
        echo -e "\n${DIM}Skipping: Seeding (--skip-seed)${NC}"
        TOTAL_SKIPPED=$((TOTAL_SKIPPED + 1))
        return 0
    fi

    # Source and run the suite
    source "$suite_file"

    # Run the suite
    if declare -f run_suite > /dev/null; then
        run_suite
        local result=$?

        # suite_end() already updates TOTAL_* counters
        SUITES_RUN=$((SUITES_RUN + 1))

        if [[ $TEST_SUITE_FAILED -gt 0 ]]; then
            SUITES_FAILED=$((SUITES_FAILED + 1))
        fi

        # Reset suite state for next suite
        TEST_SUITE_PASSED=0
        TEST_SUITE_FAILED=0
        TEST_SUITE_SKIPPED=0

        return $result
    else
        echo -e "${YELLOW}Suite $suite_name has no run_suite function${NC}"
        return 1
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Data Statistics Collection
# ─────────────────────────────────────────────────────────────────────────────

# Collect current data stats from all services
collect_data_stats() {
    log_debug "Collecting data statistics..."

    # Registry namespaces - count items in array
    local namespaces_resp
    namespaces_resp=$(curl -sf -H "X-API-Key: $API_KEY" \
        "http://localhost:${PORT_REGISTRY:-8001}/api/registry/namespaces" 2>/dev/null || echo "[]")
    DATA_STATS_NAMESPACES=$(echo "$namespaces_resp" | grep -o '"prefix"' | wc -l | tr -d ' ' | head -1)
    : "${DATA_STATS_NAMESPACES:=0}"

    # Def-Store terminologies - get total from response
    local term_resp
    term_resp=$(curl -sf -H "X-API-Key: $API_KEY" \
        "http://localhost:${PORT_DEF_STORE:-8002}/api/def-store/terminologies" 2>/dev/null || echo '{"total":0}')
    # Extract just the first "total" value (the list total, not nested totals)
    DATA_STATS_TERMINOLOGIES=$(echo "$term_resp" | sed -n 's/.*"total":\([0-9]*\).*/\1/p' | head -1)
    : "${DATA_STATS_TERMINOLOGIES:=0}"

    # Def-Store terms - sum all term_count values
    local total_terms=0
    while IFS= read -r count; do
        [[ -n "$count" ]] && total_terms=$((total_terms + count))
    done < <(echo "$term_resp" | grep -o '"term_count":[0-9]*' | grep -o '[0-9]*')
    DATA_STATS_TERMS=$total_terms

    # Template-Store templates
    local tpl_resp
    tpl_resp=$(curl -sf -H "X-API-Key: $API_KEY" \
        "http://localhost:${PORT_TEMPLATE_STORE:-8003}/api/template-store/templates" 2>/dev/null || echo '{"total":0}')
    DATA_STATS_TEMPLATES=$(echo "$tpl_resp" | sed -n 's/.*"total":\([0-9]*\).*/\1/p' | head -1)
    : "${DATA_STATS_TEMPLATES:=0}"

    # Document-Store documents
    local doc_resp
    doc_resp=$(curl -sf -H "X-API-Key: $API_KEY" \
        "http://localhost:${PORT_DOCUMENT_STORE:-8004}/api/document-store/documents?limit=1" 2>/dev/null || echo '{"total":0}')
    DATA_STATS_DOCUMENTS=$(echo "$doc_resp" | sed -n 's/.*"total":\([0-9]*\).*/\1/p' | head -1)
    : "${DATA_STATS_DOCUMENTS:=0}"

    log_debug "Stats: namespaces=$DATA_STATS_NAMESPACES terminologies=$DATA_STATS_TERMINOLOGIES terms=$DATA_STATS_TERMS templates=$DATA_STATS_TEMPLATES documents=$DATA_STATS_DOCUMENTS"
}

# ─────────────────────────────────────────────────────────────────────────────
# Reporting
# ─────────────────────────────────────────────────────────────────────────────

print_header() {
    # Detect config and apply overrides
    detect_config
    apply_env_overrides

    # Skip header in quiet mode
    if [[ "$QUIET_MODE" == "true" ]]; then
        return
    fi

    echo ""
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}  WIP Test Suite${NC}"
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  ${DIM}Date:${NC}      $(date '+%Y-%m-%d %H:%M:%S')"
    echo -e "  ${DIM}Host:${NC}      $(hostname)"
    echo -e "  ${DIM}Platform:${NC}  $(uname -s) $(uname -m)"
    echo ""

    # Show config
    echo -e "  ${DIM}Target:${NC}    ${HOSTNAME} (localhost=$LOCALHOST_MODE)"
    echo -e "  ${DIM}Modules:${NC}   ${ACTIVE_MODULES:-core}"
    echo -e "  ${DIM}Variant:${NC}   ${DEPLOYMENT_VARIANT}"
    echo -e "  ${DIM}API Key:${NC}   ${API_KEY:0:10}..."
    echo ""

    # Show suites to run
    if [[ "$RUN_QUICK" == "true" ]]; then
        echo -e "  ${DIM}Mode:${NC}      Quick validation"
    fi
    echo -e "  ${DIM}Suites:${NC}    ${SUITE_ORDER}"
    echo ""
}

write_json_output() {
    local end_time="$1"
    local duration="$2"
    local total_tests=$((TOTAL_PASSED + TOTAL_FAILED + TOTAL_SKIPPED))
    local status="pass"
    [[ $TOTAL_FAILED -gt 0 ]] && status="fail"

    cat > "$OUTPUT_FILE" << EOF
{
  "status": "$status",
  "timestamp": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "duration_seconds": $duration,
  "hostname": "$HOSTNAME",
  "localhost_mode": $([[ "$LOCALHOST_MODE" == "true" ]] && echo "true" || echo "false"),
  "modules": "$ACTIVE_MODULES",
  "variant": "$DEPLOYMENT_VARIANT",
  "suites": {
    "run": $SUITES_RUN,
    "failed": $SUITES_FAILED
  },
  "tests": {
    "total": $total_tests,
    "passed": $TOTAL_PASSED,
    "failed": $TOTAL_FAILED,
    "skipped": $TOTAL_SKIPPED
  },
  "data_stats": {
    "namespaces": $DATA_STATS_NAMESPACES,
    "terminologies": $DATA_STATS_TERMINOLOGIES,
    "terms": $DATA_STATS_TERMS,
    "templates": $DATA_STATS_TEMPLATES,
    "documents": $DATA_STATS_DOCUMENTS
  }
}
EOF
    log_debug "Wrote results to $OUTPUT_FILE"
}

print_summary() {
    local end_time
    end_time=$(date +%s)
    local duration=$((end_time - START_TIME))

    # Collect final data stats
    collect_data_stats

    # Write JSON output if requested
    if [[ -n "$OUTPUT_FILE" ]]; then
        write_json_output "$end_time" "$duration"
    fi

    # Skip console output in quiet mode
    if [[ "$QUIET_MODE" == "true" ]]; then
        local total_tests=$((TOTAL_PASSED + TOTAL_FAILED + TOTAL_SKIPPED))
        if [[ $TOTAL_FAILED -eq 0 ]]; then
            echo "PASS: $TOTAL_PASSED/$total_tests tests passed (${duration}s)"
        else
            echo "FAIL: $TOTAL_FAILED/$total_tests tests failed (${duration}s)"
        fi
        return
    fi

    echo ""
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}  Test Summary${NC}"
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""

    # Suites summary
    local suite_status
    if [[ $SUITES_FAILED -eq 0 ]]; then
        suite_status="${GREEN}ALL PASSED${NC}"
    else
        suite_status="${RED}$SUITES_FAILED FAILED${NC}"
    fi
    echo -e "  Suites:     $SUITES_RUN run, $suite_status"

    # Tests summary
    local total_tests=$((TOTAL_PASSED + TOTAL_FAILED + TOTAL_SKIPPED))
    echo -e "  Tests:      $total_tests total"
    echo -e "              ${GREEN}$TOTAL_PASSED passed${NC}"
    if [[ $TOTAL_FAILED -gt 0 ]]; then
        echo -e "              ${RED}$TOTAL_FAILED failed${NC}"
    fi
    if [[ $TOTAL_SKIPPED -gt 0 ]]; then
        echo -e "              ${YELLOW}$TOTAL_SKIPPED skipped${NC}"
    fi

    # Data statistics
    echo ""
    echo -e "  ${DIM}Data Stats:${NC}"
    echo -e "              Namespaces:    $DATA_STATS_NAMESPACES"
    echo -e "              Terminologies: $DATA_STATS_TERMINOLOGIES"
    echo -e "              Terms:         $DATA_STATS_TERMS"
    echo -e "              Templates:     $DATA_STATS_TEMPLATES"
    echo -e "              Documents:     $DATA_STATS_DOCUMENTS"

    # Duration
    echo ""
    echo -e "  Duration:   ${duration}s"
    echo ""

    # Overall result
    if [[ $TOTAL_FAILED -eq 0 ]]; then
        echo -e "  ${GREEN}${BOLD}✓ All tests passed${NC}"
    else
        echo -e "  ${RED}${BOLD}✗ Some tests failed${NC}"
    fi
    echo ""
}

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

main() {
    parse_args "$@"

    START_TIME=$(date +%s)

    print_header

    # Run each suite in order
    for suite in $SUITE_ORDER; do
        run_suite_file "$suite" || true
    done

    print_summary

    # Exit with failure if any tests failed
    if [[ $TOTAL_FAILED -gt 0 ]]; then
        exit 1
    fi
    exit 0
}

main "$@"
