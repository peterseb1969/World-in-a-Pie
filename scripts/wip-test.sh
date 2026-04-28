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

# --- Argument parsing ---
if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <component|all> [pytest-args...]" >&2
    echo "" >&2
    echo "Components: registry, def-store, template-store, document-store," >&2
    echo "            reporting-sync, ingest-gateway, mcp-server" >&2
    echo "Libraries:  wip-auth" >&2
    echo "Special:    all (run everything)" >&2
    exit 1
fi

TARGET="$1"
shift

# --- Resolve component to directory ---

PYTHON_COMPONENTS=(registry def-store template-store document-store reporting-sync ingest-gateway mcp-server)
PYTHON_LIBS=(wip-auth)

run_python_tests() {
    local name="$1"
    shift
    local dir

    # Check components/ first, then libs/
    if [[ -d "$REPO_ROOT/components/$name" ]]; then
        dir="$REPO_ROOT/components/$name"
    elif [[ -d "$REPO_ROOT/libs/$name" ]]; then
        dir="$REPO_ROOT/libs/$name"
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

    for comp in "${PYTHON_COMPONENTS[@]}" "${PYTHON_LIBS[@]}"; do
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
