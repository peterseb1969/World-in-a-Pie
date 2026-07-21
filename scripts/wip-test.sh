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
# Cross-run lock (CASE-742): the test containers are one set per machine
# with fixed database names, so runs that touch them are serialized via
# /tmp/wip-test-infra.lock. A concurrent run fails fast naming the holder;
# WIP_TEST_WAIT=1 queues instead (WIP_TEST_WAIT_TIMEOUT seconds, default
# 900). Components with no container deps are never serialized.
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
        registry|def-store|template-store|document-store|ingest-gateway|wip-toolkit)
            # wip-toolkit: the tests/integration/ suite mounts the four
            # services in-process and needs a real MongoDB (unit tests
            # skip gracefully without it, but the wrapper should provide it)
            echo "mongo"
            ;;
        reporting-sync)
            echo "postgres nats"
            ;;
        mcp-server|wip-auth|deployer|agent-scripts|scaffold|auth-gateway)
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
    echo "Tools:      deployer, agent-scripts, scaffold" >&2
    echo "Special:    all (run everything)" >&2
    exit 1
fi

TARGET="$1"
shift

# --- Resolve component to directory ---

PYTHON_COMPONENTS=(registry def-store template-store document-store reporting-sync ingest-gateway mcp-server auth-gateway)
PYTHON_LIBS=(wip-auth)
PYTHON_TOOLS=(deployer agent-scripts scaffold)

# One-time-per-component test-dep provisioning, mirroring the CI recipe
# (.gitea/workflows/test.yaml: component requirements + registry's for
# transport injection + pytest-asyncio httpx). The scaffold's venv seeds
# only pytest/ruff/mypy, so a fresh clone's first component-test run used
# to die on ModuleNotFoundError until someone replayed the CI install by
# hand. A marker under .venv/ makes re-runs free; delete the markers (or
# the venv) to force re-provisioning.
# The shared venv hosts every component's deps at once; a component
# whose pins force pip to downgrade a library another component needs
# breaks that other component silently. pip check makes the divergence
# loud and attributes it to the component whose install triggered it.
_pip_check_tripwire() {
    local name="$1"
    local out
    if ! out="$(pip check 2>&1)"; then
        echo "  WARNING: shared venv has conflicting requirements after installing $name's deps:" >&2
        echo "$out" | sed 's/^/           /' >&2
        echo "           Another component's suite may now fail on import." >&2
    fi
}

_ensure_component_test_deps() {
    local name="$1" dir="$2"
    local marker="$REPO_ROOT/.venv/.wip-test-deps-$name"
    [[ -f "$marker" ]] && return 0
    # wip-auth is a lib with its own CI recipe: the [dev] extra (carries
    # pytest-httpx for the registry-client mocks) + registry deps for the
    # transport-injection tests.
    if [[ "$name" == "wip-auth" ]]; then
        echo "  Provisioning wip-auth test deps (CI recipe, one-time)..."
        if pip install -q -e "$REPO_ROOT/libs/wip-auth[dev]" \
            && (grep -v '^\-e' "$REPO_ROOT/components/registry/requirements.txt" | pip install -q -r /dev/stdin); then
            touch "$marker"
        else
            echo "  WARNING: wip-auth test-dep provisioning failed — imports may error below." >&2
        fi
        _pip_check_tripwire "$name"
        return 0
    fi
    [[ "$dir" == "$REPO_ROOT/components/"* ]] || return 0
    echo "  Provisioning $name test deps (CI recipe, one-time)..."
    if (cd "$dir" && pip install -q -r requirements.txt) \
        && (cd "$REPO_ROOT/components/registry" && pip install -q -r requirements.txt) \
        && pip install -q pytest-asyncio httpx; then
        touch "$marker"
    else
        echo "  WARNING: test-dep provisioning failed — imports may error below." >&2
        echo "           Manual recipe: pip install -r $dir/requirements.txt \\" >&2
        echo "             -r components/registry/requirements.txt pytest-asyncio httpx" >&2
    fi
    _pip_check_tripwire "$name"
}

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

    _ensure_component_test_deps "$name" "$dir"

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

# --- Cross-run test-infrastructure lock (CASE-742) ---
#
# The test containers (test-mongo/test-postgres/test-nats) are one set per
# MACHINE, and every conftest connects under a fixed database name — so two
# concurrent suite runs (a second clone's agent, or a backgrounded run in
# this one) share databases and wipe each other's fixtures mid-flight. The
# observed symptom is mass failures with rollback / not-found signatures and
# a failure count that changes on every run: phantom regressions in both
# directions. Serialize instead: one run of the shared infrastructure at a
# time, machine-wide.
#
# Scoped, not global: components whose _component_deps is empty (deployer,
# scaffold, agent-scripts, mcp-server, wip-auth, auth-gateway) never touch
# the shared containers and are not serialized. `all` locks once up front
# (it includes mongo components; the loop runs inline in this process).
# WIP_TEST_SKIP_CONTAINERS still locks: it skips PROVISIONING, but the
# conftests connect to whatever is on the ports regardless.
#
# mkdir-based (atomic; bash-3.2- and Darwin-safe — macOS has no flock(1)),
# with the owner recorded inside. Stale locks are broken by PID liveness,
# not TTL: a slow `all` run must never have its lock expire out from under
# it, while a SIGKILLed holder is detected dead and cleared. The EXIT trap
# releases on every exit set -euo pipefail can produce.
#
# On contention: fail fast, printing the holder (clone, pid, component,
# since) so the operator knows what to wait for. WIP_TEST_WAIT=1 polls
# instead, bounded by WIP_TEST_WAIT_TIMEOUT seconds (default 900) — bounded
# because queueing blindly on a wedged holder is the same class of mistake
# as the corruption this lock prevents.

WIP_TEST_LOCK_DIR="${WIP_TEST_LOCK_DIR:-/tmp/wip-test-infra.lock}"

_lock_owner_summary() {
    if [[ -f "$WIP_TEST_LOCK_DIR/owner" ]]; then
        sed 's/^/         /' "$WIP_TEST_LOCK_DIR/owner"
    else
        echo "         (owner file missing — lock dir exists without metadata)"
    fi
}

_lock_holder_alive() {
    local pid
    pid="$(sed -n 's/^pid=//p' "$WIP_TEST_LOCK_DIR/owner" 2>/dev/null)"
    [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

_try_acquire() {
    if mkdir "$WIP_TEST_LOCK_DIR" 2>/dev/null; then
        printf 'pid=%s\nclone=%s\ncomponent=%s\nsince=%s\n' \
            "$$" "$REPO_ROOT" "$TARGET" "$(date '+%Y-%m-%d %H:%M:%S')" \
            > "$WIP_TEST_LOCK_DIR/owner"
        # shellcheck disable=SC2064
        trap "rm -rf '$WIP_TEST_LOCK_DIR'" EXIT
        return 0
    fi
    return 1
}

acquire_test_lock() {
    _try_acquire && return 0

    if ! _lock_holder_alive; then
        echo "  Stale test-infra lock (holder dead) — breaking it:"
        _lock_owner_summary
        rm -rf "$WIP_TEST_LOCK_DIR"
        _try_acquire && return 0
    fi

    if [[ "${WIP_TEST_WAIT:-}" == "1" ]]; then
        local waited=0 timeout="${WIP_TEST_WAIT_TIMEOUT:-900}"
        echo "  Test infrastructure locked — waiting (WIP_TEST_WAIT=1, up to ${timeout}s):"
        _lock_owner_summary
        while (( waited < timeout )); do
            sleep 5
            waited=$(( waited + 5 ))
            if ! ls -d "$WIP_TEST_LOCK_DIR" >/dev/null 2>&1 || ! _lock_holder_alive; then
                rm -rf "$WIP_TEST_LOCK_DIR" 2>/dev/null || true
                _try_acquire && return 0
            fi
        done
        echo "ERROR: still locked after ${timeout}s — giving up." >&2
        _lock_owner_summary >&2
        return 1
    fi

    echo "ERROR: another test run holds the shared test infrastructure:" >&2
    _lock_owner_summary >&2
    echo "       Concurrent runs share databases and corrupt each other" >&2
    echo "       (CASE-742). Re-run when it finishes, or WIP_TEST_WAIT=1" >&2
    echo "       to queue (WIP_TEST_WAIT_TIMEOUT bounds the wait)." >&2
    return 1
}

_needs_lock() {
    [[ "$TARGET" == "all" ]] && return 0
    [[ -n "$(_component_deps "$TARGET")" ]]
}

if [[ "${WIP_TEST_LOCK_HELD:-}" != "1" ]] && _needs_lock; then
    acquire_test_lock || exit 1
    export WIP_TEST_LOCK_HELD=1
fi

# Test hook: prove lock behaviour without running a suite. Prints the
# outcome and exits — LOCK_ACQUIRED (this run holds it), or LOCK_SKIPPED
# (no-dep target / already held by a parent). Contention exits above with
# the owner message before reaching this line.
if [[ "${WIP_TEST_LOCK_PROBE:-}" == "1" ]]; then
    if [[ -f "$WIP_TEST_LOCK_DIR/owner" ]] && grep -q "^pid=$$\$" "$WIP_TEST_LOCK_DIR/owner"; then
        echo "LOCK_ACQUIRED"
    else
        echo "LOCK_SKIPPED"
    fi
    exit 0
fi

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
