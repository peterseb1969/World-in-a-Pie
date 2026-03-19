#!/bin/bash
# Test script for setup.sh parameter combinations
#
# Validates all sensible parameter combinations without starting containers.
# Happy-path tests use --save-config to exercise arg parsing, preset loading,
# module computation, and config generation — then exit before install.
# Error tests verify that invalid inputs are rejected with correct messages.
#
# Usage:
#   ./scripts/test-setup-combinations.sh
#   ./scripts/test-setup-combinations.sh --verbose   # show config file contents on failure

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
SETUP="$PROJECT_ROOT/scripts/setup.sh"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
DIM='\033[2m'
BOLD='\033[1m'
NC='\033[0m'

# Counters
PASS=0
FAIL=0
TOTAL=0
VERBOSE="${1:-}"

# Temp directory for config files
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

pass() {
    PASS=$((PASS + 1))
    TOTAL=$((TOTAL + 1))
    echo -e "  ${GREEN}PASS${NC}  #${TOTAL} $1"
}

fail() {
    FAIL=$((FAIL + 1))
    TOTAL=$((TOTAL + 1))
    echo -e "  ${RED}FAIL${NC}  #${TOTAL} $1"
    echo -e "        ${RED}→ $2${NC}"
    if [ "$VERBOSE" = "--verbose" ] && [ -f "${3:-}" ]; then
        echo "        --- config contents ---"
        sed 's/^/        /' "$3"
        echo "        ---"
    fi
}

# Print the command being run and expected outcome
print_test_info() {
    local cmd="$1"
    local expect="$2"
    echo -e "        ${DIM}cmd:${NC}    ${CYAN}setup.sh ${cmd}${NC}"
    echo -e "        ${DIM}expect:${NC} ${expect}"
}

# Run setup.sh with --save-config and return the config file path.
# Captures both stdout and stderr.
# Usage: run_setup <test_name> <args...>
# Sets: CONF (config file path), EXIT_CODE, OUTPUT (combined stdout+stderr)
run_setup() {
    local test_name="$1"; shift
    CONF="$TMPDIR/${TOTAL}_$(echo "$test_name" | tr ' ' '_').conf"
    OUTPUT=$("$SETUP" --save-config "$CONF" "$@" 2>&1) || true
    EXIT_CODE=$?
}

# Run setup.sh expecting failure (no --save-config).
# Usage: run_setup_fail <test_name> <args...>
# Sets: EXIT_CODE, OUTPUT
run_setup_fail() {
    local test_name="$1"; shift
    CONF=""
    OUTPUT=$("$SETUP" "$@" 2>&1) || true
    EXIT_CODE=$?
}

# Assert config file contains a pattern
# Usage: assert_config_contains <pattern> <description>
assert_config_contains() {
    if ! grep -q "$1" "$CONF" 2>/dev/null; then
        return 1
    fi
    return 0
}

# Assert config file does NOT contain a pattern
# Usage: assert_config_excludes <pattern> <description>
assert_config_excludes() {
    if grep -q "$1" "$CONF" 2>/dev/null; then
        return 1
    fi
    return 0
}

# Run a happy-path test: expects exit 0, config file created, then runs assertions.
# Usage: happy_test <name> <setup_args...> -- <assertion_func>
# The assertion_func receives $CONF as the config file path.
happy_test() {
    local name="$1"; shift
    local args=()

    # Split args on "--" into setup args and assertion args
    while [[ $# -gt 0 && "$1" != "--" ]]; do
        args+=("$1"); shift
    done
    [ "${1:-}" = "--" ] && shift

    # Build readable assertion summary and collect assertion pairs
    local expect_parts=()
    local assert_ops=()
    local assert_vals=()
    local remaining=("$@")
    local i=0
    while [ $i -lt ${#remaining[@]} ]; do
        local op="${remaining[$i]}"
        local val="${remaining[$((i+1))]}"
        assert_ops+=("$op")
        assert_vals+=("$val")
        if [ "$op" = "contains" ]; then
            expect_parts+=("$val")
        elif [ "$op" = "excludes" ]; then
            expect_parts+=("NOT $val in ACTIVE_MODULES")
        fi
        i=$((i + 2))
    done

    # Print command and expectations
    local cmd_str="${args[*]}"
    local expect_str=""
    local sep=""
    for part in "${expect_parts[@]}"; do
        expect_str="${expect_str}${sep}${part}"
        sep="; "
    done
    print_test_info "$cmd_str" "$expect_str"

    CONF="$TMPDIR/test_$(echo "$name" | tr ' /' '_').conf"
    OUTPUT=$("$SETUP" --save-config "$CONF" "${args[@]}" 2>&1)
    EXIT_CODE=$?

    if [ $EXIT_CODE -ne 0 ]; then
        fail "$name" "Expected exit 0, got $EXIT_CODE. Output: $(echo "$OUTPUT" | head -5)" "$CONF"
        return
    fi

    if [ ! -f "$CONF" ]; then
        fail "$name" "Config file not created" "$CONF"
        return
    fi

    # Run assertions
    # "contains" checks the whole config file.
    # "excludes" checks only WIP_ACTIVE_MODULES to avoid false matches
    # in WIP_MODULES (raw preset), WIP_REMOVE_MODULES, comments, etc.
    local assertion_failed=""
    local active_modules_line
    active_modules_line=$(grep '^WIP_ACTIVE_MODULES=' "$CONF" || echo "")

    i=0
    while [ $i -lt ${#assert_ops[@]} ]; do
        local op="${assert_ops[$i]}"
        local pattern="${assert_vals[$i]}"
        case "$op" in
            contains)
                if ! grep -q "$pattern" "$CONF"; then
                    assertion_failed="Expected config to contain '$pattern'"
                    break
                fi
                ;;
            excludes)
                if echo "$active_modules_line" | grep -q "$pattern"; then
                    assertion_failed="Expected WIP_ACTIVE_MODULES NOT to contain '$pattern' (got: $active_modules_line)"
                    break
                fi
                ;;
        esac
        i=$((i + 1))
    done

    if [ -n "$assertion_failed" ]; then
        fail "$name" "$assertion_failed" "$CONF"
    else
        pass "$name"
    fi
}

# Run an error test: expects non-zero exit and error message in output.
# Usage: error_test <name> <expected_pattern> <setup_args...>
error_test() {
    local name="$1"; shift
    local expected="$1"; shift

    local cmd_str="${*:-(no arguments)}"
    print_test_info "$cmd_str" "exit non-zero; stderr contains '$expected'"

    CONF=""
    local exit_code=0
    OUTPUT=$("$SETUP" "$@" 2>&1) || exit_code=$?

    if [ $exit_code -eq 0 ]; then
        fail "$name" "Expected non-zero exit, got 0" ""
        return
    fi

    if ! echo "$OUTPUT" | grep -qi "$expected"; then
        fail "$name" "Expected output to contain '$expected', got: $(echo "$OUTPUT" | tail -3)" ""
        return
    fi

    pass "$name"
}

# ────────────────────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}══════════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}  setup.sh Parameter Combination Tests${NC}"
echo -e "${BOLD}══════════════════════════════════════════════════════════════${NC}"
echo ""

# ── Category 1: Preset + Network (8 tests) ──────────────────────────────────

echo -e "${YELLOW}Category 1: Preset + Network${NC}"

happy_test "core + localhost" \
    --preset core --localhost -y \
    -- \
    contains 'WIP_PRESET="core"' \
    contains 'WIP_LOCALHOST_MODE="true"' \
    contains 'WIP_VARIANT="dev"' \
    excludes 'oidc' \
    excludes 'nats'

happy_test "core + hostname" \
    --preset core --hostname wip.local -y \
    -- \
    contains 'WIP_PRESET="core"' \
    contains 'WIP_HOSTNAME="wip.local"' \
    contains 'WIP_LOCALHOST_MODE="false"' \
    excludes 'oidc' \
    excludes 'nats'

happy_test "standard + localhost" \
    --preset standard --localhost -y \
    -- \
    contains 'WIP_PRESET="standard"' \
    contains 'WIP_LOCALHOST_MODE="true"' \
    contains 'oidc' \
    contains 'dev-tools' \
    excludes 'nats'

happy_test "standard + hostname" \
    --preset standard --hostname wip.local -y \
    -- \
    contains 'WIP_PRESET="standard"' \
    contains 'WIP_HOSTNAME="wip.local"' \
    contains 'oidc' \
    contains 'dev-tools' \
    excludes 'nats'

happy_test "analytics + localhost" \
    --preset analytics --localhost -y \
    -- \
    contains 'WIP_PRESET="analytics"' \
    contains 'WIP_LOCALHOST_MODE="true"' \
    contains 'oidc' \
    contains 'reporting' \
    contains 'nats'

happy_test "analytics + hostname" \
    --preset analytics --hostname wip.local -y \
    -- \
    contains 'WIP_PRESET="analytics"' \
    contains 'WIP_HOSTNAME="wip.local"' \
    contains 'oidc' \
    contains 'reporting' \
    contains 'nats'

happy_test "full + localhost" \
    --preset full --localhost -y \
    -- \
    contains 'WIP_PRESET="full"' \
    contains 'WIP_LOCALHOST_MODE="true"' \
    contains 'oidc' \
    contains 'reporting' \
    contains 'files' \
    contains 'ingest' \
    contains 'nats'

happy_test "full + hostname" \
    --preset full --hostname wip.local -y \
    -- \
    contains 'WIP_PRESET="full"' \
    contains 'WIP_HOSTNAME="wip.local"' \
    contains 'oidc' \
    contains 'reporting' \
    contains 'files' \
    contains 'ingest' \
    contains 'nats'

echo ""

# ── Category 2: Preset + Production Variant (4 tests) ───────────────────────

echo -e "${YELLOW}Category 2: Preset + Production Variant${NC}"

happy_test "core + localhost + prod" \
    --preset core --localhost --prod -y \
    -- \
    contains 'WIP_VARIANT="prod"' \
    contains 'WIP_LOCALHOST_MODE="true"' \
    excludes 'dev-tools' \
    excludes 'oidc' \
    excludes 'nats'

happy_test "standard + hostname + prod" \
    --preset standard --hostname wip.local --prod -y \
    -- \
    contains 'WIP_VARIANT="prod"' \
    contains 'oidc' \
    excludes 'dev-tools' \
    excludes 'nats'

happy_test "analytics + hostname + prod" \
    --preset analytics --hostname wip.local --prod -y \
    -- \
    contains 'WIP_VARIANT="prod"' \
    contains 'oidc' \
    contains 'reporting' \
    contains 'nats' \
    excludes 'dev-tools'

happy_test "full + hostname + prod" \
    --preset full --hostname wip.local --prod -y \
    -- \
    contains 'WIP_VARIANT="prod"' \
    contains 'oidc' \
    contains 'reporting' \
    contains 'files' \
    contains 'ingest' \
    contains 'nats' \
    excludes 'dev-tools'

echo ""

# ── Category 3: Module Modifiers with Presets (6 tests) ──────────────────────

echo -e "${YELLOW}Category 3: Module Modifiers with Presets${NC}"

happy_test "standard + add reporting" \
    --preset standard --add reporting --hostname wip.local -y \
    -- \
    contains 'oidc' \
    contains 'reporting' \
    contains 'nats'

happy_test "standard + modules reporting (merge)" \
    --preset standard --modules reporting --hostname wip.local -y \
    -- \
    contains 'oidc' \
    contains 'reporting' \
    contains 'nats'

happy_test "full + remove ingest" \
    --preset full --remove ingest --hostname wip.local -y \
    -- \
    contains 'oidc' \
    contains 'reporting' \
    contains 'files' \
    contains 'nats' \
    excludes 'ingest'

happy_test "full + remove ingest,files" \
    --preset full --remove ingest,files --hostname wip.local -y \
    -- \
    contains 'oidc' \
    contains 'reporting' \
    contains 'nats' \
    excludes 'ingest' \
    excludes 'files'

happy_test "core + add files" \
    --preset core --add files --localhost -y \
    -- \
    contains 'files' \
    contains 'dev-tools' \
    excludes 'oidc' \
    excludes 'nats'

happy_test "analytics + add files,ingest" \
    --preset analytics --add files,ingest --hostname wip.local -y \
    -- \
    contains 'oidc' \
    contains 'reporting' \
    contains 'files' \
    contains 'ingest' \
    contains 'nats' \
    contains 'dev-tools'

echo ""

# ── Category 4: Custom Modules (no preset) (6 tests) ────────────────────────

echo -e "${YELLOW}Category 4: Custom Modules (no preset)${NC}"

happy_test "modules: oidc only" \
    --modules oidc --hostname wip.local -y \
    -- \
    contains 'WIP_PRESET=""' \
    contains 'oidc' \
    contains 'dev-tools' \
    excludes 'reporting' \
    excludes 'files' \
    excludes 'nats'

happy_test "modules: oidc,files" \
    --modules oidc,files --hostname wip.local -y \
    -- \
    contains 'oidc' \
    contains 'files' \
    contains 'dev-tools' \
    excludes 'reporting' \
    excludes 'nats'

happy_test "modules: reporting only" \
    --modules reporting --localhost -y \
    -- \
    contains 'reporting' \
    contains 'nats' \
    contains 'dev-tools' \
    excludes 'oidc'

happy_test "modules: oidc,reporting,files" \
    --modules oidc,reporting,files --hostname wip.local -y \
    -- \
    contains 'oidc' \
    contains 'reporting' \
    contains 'files' \
    contains 'nats' \
    excludes 'ingest'

happy_test "modules: ingest only" \
    --modules ingest --localhost -y \
    -- \
    contains 'ingest' \
    contains 'nats' \
    contains 'dev-tools' \
    excludes 'oidc' \
    excludes 'reporting' \
    excludes 'files'

happy_test "modules: files,ingest" \
    --modules files,ingest --localhost -y \
    -- \
    contains 'files' \
    contains 'ingest' \
    contains 'nats' \
    contains 'dev-tools' \
    excludes 'oidc' \
    excludes 'reporting'

echo ""

# ── Category 5: Platform Variations (3 tests) ───────────────────────────────

echo -e "${YELLOW}Category 5: Platform Variations${NC}"

happy_test "platform: default" \
    --preset standard --localhost --platform default -y \
    -- \
    contains 'WIP_PLATFORM="default"'

happy_test "platform: pi4" \
    --preset standard --localhost --platform pi4 -y \
    -- \
    contains 'WIP_PLATFORM="pi4"'

happy_test "core + platform: pi4" \
    --preset core --localhost --platform pi4 -y \
    -- \
    contains 'WIP_PLATFORM="pi4"' \
    contains 'WIP_PRESET="core"'

echo ""

# ── Category 6: TLS / Let's Encrypt (3 tests) ───────────────────────────────

echo -e "${YELLOW}Category 6: TLS / Let's Encrypt${NC}"

happy_test "LE production" \
    --preset standard --hostname wip.example.com --email admin@test.com -y \
    -- \
    contains 'WIP_HOSTNAME="wip.example.com"' \
    contains 'oidc'

happy_test "LE staging" \
    --preset standard --hostname wip.example.com --email admin@test.com --acme-staging -y \
    -- \
    contains 'WIP_HOSTNAME="wip.example.com"' \
    contains 'oidc'

happy_test "LE staging + prod" \
    --preset standard --hostname wip.local --prod --email admin@test.com --acme-staging -y \
    -- \
    contains 'WIP_VARIANT="prod"' \
    contains 'WIP_HOSTNAME="wip.local"' \
    excludes 'dev-tools'

echo ""

# ── Category 7: Custom Ports (2 tests) ──────────────────────────────────────

echo -e "${YELLOW}Category 7: Custom Ports${NC}"

happy_test "custom https port" \
    --preset standard --hostname wip.local --https-port 9443 -y \
    -- \
    contains 'WIP_HTTPS_PORT="9443"' \
    contains 'WIP_HTTP_PORT="8080"'

happy_test "standard web ports" \
    --preset standard --hostname wip.local --https-port 443 --http-port 80 -y \
    -- \
    contains 'WIP_HTTPS_PORT="443"' \
    contains 'WIP_HTTP_PORT="80"'

echo ""

# ── Category 8: Config Save/Load (3 tests) ──────────────────────────────────

echo -e "${YELLOW}Category 8: Config Save/Load${NC}"

# Test 33: Save config
SAVED_CONF="$TMPDIR/saved_config.conf"
print_test_info "--save-config <file> --preset standard --hostname wip.local -y" \
    "exit 0; file contains WIP_PRESET=\"standard\", WIP_HOSTNAME=\"wip.local\""
OUTPUT=$("$SETUP" --save-config "$SAVED_CONF" --preset standard --hostname wip.local -y 2>&1)
EXIT_CODE=$?
CONF="$SAVED_CONF"
if [ $EXIT_CODE -eq 0 ] && [ -f "$SAVED_CONF" ] && \
   grep -q 'WIP_PRESET="standard"' "$SAVED_CONF" && \
   grep -q 'WIP_HOSTNAME="wip.local"' "$SAVED_CONF"; then
    pass "save-config creates valid file"
else
    fail "save-config creates valid file" "Exit=$EXIT_CODE or config missing expected values" "$SAVED_CONF"
fi

# Test 34: Load config (uses the saved file from test 33)
# We load the config and re-save to a new file to verify values are preserved.
LOADED_CONF="$TMPDIR/loaded_config.conf"
print_test_info "--config <saved> --save-config <file> -y" \
    "exit 0; file contains WIP_PRESET=\"standard\", WIP_HOSTNAME=\"wip.local\""
OUTPUT=$("$SETUP" --config "$SAVED_CONF" --save-config "$LOADED_CONF" -y 2>&1)
EXIT_CODE=$?
CONF="$LOADED_CONF"
if [ $EXIT_CODE -eq 0 ] && [ -f "$LOADED_CONF" ] && \
   grep -q 'WIP_HOSTNAME="wip.local"' "$LOADED_CONF" && \
   grep -q 'WIP_PRESET="standard"' "$LOADED_CONF"; then
    pass "load-config preserves values"
else
    fail "load-config preserves values" "Exit=$EXIT_CODE or loaded config missing values" "$LOADED_CONF"
fi

# Test 35: Load config + add modules
EXTENDED_CONF="$TMPDIR/extended_config.conf"
print_test_info "--config <saved> --add files --save-config <file> -y" \
    "exit 0; ACTIVE_MODULES contains oidc, files"
OUTPUT=$("$SETUP" --config "$SAVED_CONF" --add files --save-config "$EXTENDED_CONF" -y 2>&1)
EXIT_CODE=$?
CONF="$EXTENDED_CONF"
if [ $EXIT_CODE -eq 0 ] && [ -f "$EXTENDED_CONF" ] && \
   grep -q 'files' "$EXTENDED_CONF" && \
   grep -q 'oidc' "$EXTENDED_CONF"; then
    pass "load-config + add modules extends correctly"
else
    fail "load-config + add modules extends correctly" "Exit=$EXIT_CODE or missing expected modules" "$EXTENDED_CONF"
fi

echo ""

# ── Category 9: Clean & Miscellaneous Flags (4 tests) ───────────────────────

echo -e "${YELLOW}Category 9: Clean & Miscellaneous Flags${NC}"

happy_test "clean flag" \
    --preset standard --localhost --clean -y \
    -- \
    contains 'WIP_PRESET="standard"' \
    contains 'WIP_LOCALHOST_MODE="true"'

happy_test "full + clean + prod" \
    --preset full --hostname wip.local --clean --prod -y \
    -- \
    contains 'WIP_VARIANT="prod"' \
    contains 'nats' \
    excludes 'dev-tools'

happy_test "generate-secrets without prod" \
    --preset standard --localhost --generate-secrets -y \
    -- \
    contains 'WIP_PRESET="standard"' \
    contains 'WIP_VARIANT="dev"'

happy_test "custom data-dir" \
    --preset core --localhost --data-dir /tmp/wip-test-data -y \
    -- \
    contains 'WIP_DATA_DIR="/tmp/wip-test-data"'

echo ""

# ── Category 10: dev-tools Behavior (3 tests) ───────────────────────────────

echo -e "${YELLOW}Category 10: dev-tools Behavior${NC}"

happy_test "dev variant auto-adds dev-tools" \
    --preset core --localhost -y \
    -- \
    contains 'WIP_VARIANT="dev"' \
    contains 'dev-tools'

happy_test "prod variant excludes dev-tools" \
    --preset core --localhost --prod -y \
    -- \
    contains 'WIP_VARIANT="prod"' \
    excludes 'dev-tools'

happy_test "prod + explicit dev-tools add → removed" \
    --preset standard --hostname wip.local --prod --add dev-tools -y \
    -- \
    contains 'WIP_VARIANT="prod"' \
    excludes 'dev-tools' \
    contains 'oidc'

echo ""

# ── Category 11: Validation / Error Cases (6 tests) ─────────────────────────

echo -e "${YELLOW}Category 11: Validation / Error Cases${NC}"

error_test "no arguments" \
    "Must specify either --preset"
    # (no args)

error_test "preset without network" \
    "Network mode requires --hostname" \
    --preset standard

error_test "unknown preset" \
    "Unknown preset" \
    --preset nonexistent --localhost -y

error_test "unknown module" \
    "Unknown module" \
    --modules bogus --localhost -y

error_test "modules without network" \
    "Network mode requires --hostname" \
    --modules oidc

error_test "hostname without preset/modules" \
    "Must specify either --preset" \
    --hostname wip.local

echo ""

# ────────────────────────────────────────────────────────────────────────────
# Summary
# ────────────────────────────────────────────────────────────────────────────

echo -e "${BOLD}══════════════════════════════════════════════════════════════${NC}"
if [ $FAIL -eq 0 ]; then
    echo -e "  ${GREEN}All $TOTAL tests passed${NC}"
else
    echo -e "  ${GREEN}$PASS passed${NC}, ${RED}$FAIL failed${NC} (out of $TOTAL)"
fi
echo -e "${BOLD}══════════════════════════════════════════════════════════════${NC}"
echo ""

exit $FAIL
