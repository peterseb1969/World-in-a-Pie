#!/usr/bin/env bash
#
# Run WIP component/library tests with reliable venv activation.
#
# Usage:
#   ./scripts/wip-test.sh <component> [pytest-args...]
#   ./scripts/wip-test.sh all
#
# Examples:
#   ./scripts/wip-test.sh def-store              # all def-store tests
#   ./scripts/wip-test.sh template-store -x -q   # stop on first failure
#   ./scripts/wip-test.sh wip-auth -k "test_resolve"
#   ./scripts/wip-test.sh template-store tests/test_x.py             # one file
#   ./scripts/wip-test.sh template-store tests/test_x.py::test_a     # one test
#   ./scripts/wip-test.sh all                    # run everything
#
# Test-container provisioning (CASE-320):
#   The component conftests connect to dedicated test-only services
#   (test-mongo on host:27017, test-postgres on host:5433, test-nats
#   on host:4223). These are NOT the wip-deploy install — they're
#   throwaway containers managed by this script that mirror what CI
#   does. The script auto-starts them on demand.
#
#   Override the container CLI:
#     WIP_TEST_CONTAINER_CLI=docker ./scripts/wip-test.sh registry
#   Skip provisioning entirely (e.g. for unit-only runs):
#     WIP_TEST_SKIP_CONTAINERS=1 ./scripts/wip-test.sh registry tests/test_unit.py
#
# Exits with pytest's exit code (or combined exit code for "all").
#
# Path override: if any positional pytest target is supplied (a file
# path, a tests/... reference, or a nodeid like tests/x.py::test_y),
# the default `tests/` is dropped so the user's target is the only
# selection. Without this, `pytest tests/ tests/x.py` collects both
# the directory and the targeted file, which is slow and surprising.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# --- Activate venv ---
if [[ ! -f "$REPO_ROOT/.venv/bin/activate" ]]; then
    echo "ERROR: No venv found at $REPO_ROOT/.venv — run setup first." >&2
    exit 1
fi
# shellcheck disable=SC1091
source "$REPO_ROOT/.venv/bin/activate"

# --- Test-container provisioning (CASE-320) ---
#
# Component conftests connect to test-only services on standard host
# ports (mongo:27017, postgres:5433->5432, nats:4223->4222). These
# are SEPARATE from any wip-deploy install — wip-deploy only host-maps
# the router (8443) to reduce production surface area, so the
# wip-mongodb / wip-postgres / wip-nats containers it provisions are
# not host-reachable. CI provisions throwaway test-mongo / test-postgres
# / test-nats containers per .gitea/workflows/test.yaml. This block
# mirrors that locally so wip-test.sh can run identical tests.
#
# Container CLI: WIP_TEST_CONTAINER_CLI overrides; defaults to podman
# (matches the rest of the WIP toolchain) with docker as fallback.
# Set WIP_TEST_SKIP_CONTAINERS=1 to skip provisioning (useful for
# pure-unit-test runs or when you've staged your own services).

CONTAINER_CLI="${WIP_TEST_CONTAINER_CLI:-}"
if [[ -z "$CONTAINER_CLI" ]]; then
    if command -v podman >/dev/null 2>&1; then
        CONTAINER_CLI=podman
    elif command -v docker >/dev/null 2>&1; then
        CONTAINER_CLI=docker
    fi
fi

# Per-component test-container dependencies. Mirror
# .gitea/workflows/test.yaml — keep in lockstep when CI changes.
# Case statement (not assoc array) for macOS bash 3.2 compatibility.
_component_deps() {
    case "$1" in
        registry|def-store|template-store|document-store|ingest-gateway)
            echo "mongo"
            ;;
        reporting-sync)
            echo "postgres nats"
            ;;
        mcp-server|wip-auth|deployer)
            echo ""
            ;;
        *)
            # Unknown component — return empty; the caller will hit
            # the existing "Unknown component" error in run_python_tests.
            echo ""
            ;;
    esac
}

_container_running() {
    local name="$1"
    "$CONTAINER_CLI" ps --format '{{.Names}}' 2>/dev/null | grep -qx "$name"
}

_ensure_mongo() {
    if _container_running test-mongo; then return 0; fi
    echo "  Starting test-mongo on host port 27017..."
    "$CONTAINER_CLI" start test-mongo >/dev/null 2>&1 || \
        "$CONTAINER_CLI" run -d --name test-mongo -p 27017:27017 mongo:7 >/dev/null
    for _ in $(seq 1 15); do
        if "$CONTAINER_CLI" exec test-mongo mongosh --quiet --eval "db.runCommand('ping')" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    echo "ERROR: test-mongo did not become ready within 15s" >&2
    return 1
}

_ensure_postgres() {
    if _container_running test-postgres; then return 0; fi
    echo "  Starting test-postgres on host port 5433..."
    "$CONTAINER_CLI" start test-postgres >/dev/null 2>&1 || \
        "$CONTAINER_CLI" run -d --name test-postgres -p 5433:5432 \
            -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test \
            -e POSTGRES_DB=wip_test postgres:16 >/dev/null
    for _ in $(seq 1 15); do
        if "$CONTAINER_CLI" exec test-postgres pg_isready -U test >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    echo "ERROR: test-postgres did not become ready within 15s" >&2
    return 1
}

_ensure_nats() {
    if _container_running test-nats; then return 0; fi
    echo "  Starting test-nats on host ports 4223/8223..."
    "$CONTAINER_CLI" start test-nats >/dev/null 2>&1 || \
        "$CONTAINER_CLI" run -d --name test-nats -p 4223:4222 -p 8223:8222 \
            nats:2 -js -m 8222 >/dev/null
    for _ in $(seq 1 15); do
        if curl -fsS http://localhost:8223/healthz >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    echo "ERROR: test-nats did not become ready within 15s" >&2
    return 1
}

ensure_test_containers() {
    local component="$1"
    local deps
    deps="$(_component_deps "$component")"
    if [[ -z "$deps" ]]; then
        return 0
    fi
    if [[ "${WIP_TEST_SKIP_CONTAINERS:-}" == "1" ]]; then
        echo "  WIP_TEST_SKIP_CONTAINERS=1 — skipping container provisioning"
        return 0
    fi
    if [[ -z "$CONTAINER_CLI" ]]; then
        echo "ERROR: $component needs test containers ($deps) but no" >&2
        echo "       container CLI found. Install podman or docker, or set" >&2
        echo "       WIP_TEST_SKIP_CONTAINERS=1 to skip provisioning." >&2
        return 1
    fi
    for dep in $deps; do
        case "$dep" in
            mongo)
                _ensure_mongo    || return 1
                ;;
            postgres)
                _ensure_postgres || return 1
                # The conftest's skip-marker treats POSTGRES_TEST_URI
                # as the opt-in signal: unset env var → integration
                # tests skip even if the default URI would work.
                # Export here so pytest's subprocess inherits it.
                export POSTGRES_TEST_URI="postgresql://test:test@localhost:5433/wip_test"
                ;;
            nats)
                _ensure_nats     || return 1
                export NATS_TEST_URL="nats://localhost:4223"
                ;;
            *)
                echo "ERROR: unknown test-container dep '$dep' for $component" >&2
                return 1
                ;;
        esac
    done
}

# --- Argument parsing ---
if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <component|all> [pytest-args...]" >&2
    echo "" >&2
    echo "Components: registry, def-store, template-store, document-store," >&2
    echo "            reporting-sync, ingest-gateway, mcp-server" >&2
    echo "Libraries:  wip-auth" >&2
    echo "Tools:      deployer" >&2
    echo "Special:    all (run everything)" >&2
    exit 1
fi

TARGET="$1"
shift

# --- Resolve component to directory ---

PYTHON_COMPONENTS=(registry def-store template-store document-store reporting-sync ingest-gateway mcp-server)
PYTHON_LIBS=(wip-auth)
PYTHON_TOOLS=(deployer)

run_python_tests() {
    local name="$1"
    shift
    local dir

    # Check components/ first, then libs/, then top-level packages
    # (e.g., deployer/) with the standard src/ + tests/ shape.
    if [[ -d "$REPO_ROOT/components/$name" ]]; then
        dir="$REPO_ROOT/components/$name"
    elif [[ -d "$REPO_ROOT/libs/$name" ]]; then
        dir="$REPO_ROOT/libs/$name"
    elif [[ -d "$REPO_ROOT/$name" && -d "$REPO_ROOT/$name/src" && -d "$REPO_ROOT/$name/tests" ]]; then
        dir="$REPO_ROOT/$name"
    else
        echo "ERROR: Unknown component '$name'" >&2
        return 1
    fi

    if [[ ! -d "$dir/tests" ]]; then
        echo "ERROR: No tests/ directory in $dir" >&2
        return 1
    fi

    # Detect whether the caller already provided a positional pytest
    # target (file, dir, or nodeid). If so, skip the implicit `tests/`
    # so pytest only collects what was asked for. Heuristic: any
    # non-flag arg containing '.py', '/', '::', or matching exactly
    # 'tests' counts as a target. Values that follow short flags like
    # '-k' (e.g. `-k "test_foo"`) are skipped via _skip_next.
    local has_target=0
    local skip_next=0
    local arg
    for arg in "$@"; do
        if (( skip_next )); then
            skip_next=0
            continue
        fi
        case "$arg" in
            # Short flags that take a value as the next arg.
            -k|-m|-c|-p|-o|-W|-r|--maxfail|--ignore|--rootdir|--confcutdir|--basetemp)
                skip_next=1
                ;;
            # Any other flag — ignore.
            -*) ;;
            # Path-shaped tokens.
            *.py|*/*|*::*|tests)
                has_target=1
                ;;
        esac
    done

    echo "=== $name ==="
    ensure_test_containers "$name" || return 1
    if (( has_target )); then
        (cd "$dir" && PYTHONPATH=src pytest "$@")
    else
        (cd "$dir" && PYTHONPATH=src pytest tests/ "$@")
    fi
}

# --- Execute ---

if [[ "$TARGET" == "all" ]]; then
    FAILED=()
    PASSED=()

    for comp in "${PYTHON_COMPONENTS[@]}" "${PYTHON_LIBS[@]}" "${PYTHON_TOOLS[@]}"; do
        if run_python_tests "$comp" "$@"; then
            PASSED+=("$comp")
        else
            FAILED+=("$comp")
        fi
        echo ""
    done

    echo "=== Summary ==="
    echo "Passed: ${PASSED[*]:-none}"
    if [[ ${#FAILED[@]} -gt 0 ]]; then
        echo "FAILED: ${FAILED[*]}"
        exit 1
    fi
    exit 0
else
    run_python_tests "$TARGET" "$@"
fi
