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
#   ./scripts/wip-test.sh --down                 # remove this clone's test containers
#
# Test-container provisioning (CASE-320, per clone since CASE-912):
#   The component conftests connect to dedicated test-only services
#   (MongoDB, PostgreSQL, NATS). These are NOT the wip-deploy install —
#   they're throwaway containers managed by this script that mirror what
#   CI does. Each clone gets its own set (test-mongo-<key>, …, where
#   <key> derives from the clone path), published on loopback ports the
#   container runtime picks; the script reads the ports back and exports
#   MONGO_URI / POSTGRES_TEST_URI / NATS_TEST_URL. A value already set in
#   the caller's environment wins. Containers stay running between runs;
#   --down removes them.
#
#   Override the container CLI:
#     WIP_TEST_CONTAINER_CLI=docker ./scripts/wip-test.sh registry
#   Skip provisioning entirely (e.g. for unit-only runs):
#     WIP_TEST_SKIP_CONTAINERS=1 ./scripts/wip-test.sh registry tests/test_unit.py
#
# Cross-run lock (CASE-742): a clone's runs share its databases, so runs
# that touch them are serialized per clone via /tmp/wip-test-infra-<key>.lock.
# A concurrent run in the same clone fails fast naming the holder;
# WIP_TEST_WAIT=1 queues instead (WIP_TEST_WAIT_TIMEOUT seconds, default
# 900). Different clones never wait on each other. Components with no
# container deps are never serialized.
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
# Component conftests connect to test-only services. These are SEPARATE
# from any wip-deploy install — wip-deploy only host-maps the router
# (8443) to reduce production surface area, so the wip-mongodb /
# wip-postgres / wip-nats containers it provisions are not
# host-reachable. CI gives each job its own databases as job services
# (.gitea/workflows/test.yaml). This block mirrors that locally so
# wip-test.sh can run identical tests.
#
# One container set per CLONE, not per machine: several agents work in
# separate clones on one machine, and a shared set with fixed database
# names forced every clone's runs to queue behind one machine-wide lock.
# Container names carry a key derived from the clone path, and host
# ports are left to the runtime (`-p 127.0.0.1::<port>`) and read back
# on every run, so two clones never collide and no port range needs
# managing. The clone path is also stored as a label
# (wip.test.clone=<path>), so the sets of deleted clones can be found:
#   podman ps -a --filter label=wip.test.clone --format '{{.Names}} {{.Labels}}'
# The un-suffixed test-mongo / test-postgres / test-nats containers of
# earlier versions of this script are left alone; clones still on those
# versions keep using them.
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
        registry|def-store|template-store|document-store|wip-toolkit)
            # wip-toolkit: the tests/integration/ suite mounts the four
            # services in-process and needs a real MongoDB (unit tests
            # skip gracefully without it, but the wrapper should provide it)
            echo "mongo"
            ;;
        ingest-gateway)
            # NATS: the consumer-convergence test runs against a real
            # JetStream server (it skips without NATS_TEST_URL).
            echo "mongo nats"
            ;;
        reporting-sync)
            echo "postgres nats"
            ;;
        wip-auth)
            # Mongo: the resolve/fastapi-helpers tests mount the real
            # Registry in-process against a real MongoDB. NATS: the live
            # JetStream tests in test_events.py run against test-nats
            # (they skip when it is absent — but see the wait_for guard
            # there; without it an absent server hung the suite).
            echo "mongo nats"
            ;;
        mcp-server|wip-archive|deployer|agent-scripts|scaffold|auth-gateway)
            echo ""
            ;;
        *)
            # Unknown component — return empty; the caller will hit
            # the existing "Unknown component" error in run_python_tests.
            echo ""
            ;;
    esac
}

# The key: cksum of the clone path (POSIX — present wherever bash is),
# as 8 hex digits.
CLONE_KEY="$(printf '%08x' "$(printf '%s' "$REPO_ROOT" | cksum | cut -d' ' -f1)")"
TEST_MONGO="test-mongo-$CLONE_KEY"
TEST_POSTGRES="test-postgres-$CLONE_KEY"
TEST_NATS="test-nats-$CLONE_KEY"

_container_running() {
    local name="$1"
    "$CONTAINER_CLI" ps --format '{{.Names}}' 2>/dev/null | grep -qx "$name"
}

# Prints the host port a running container publishes for container port $2.
# Read on every run rather than remembered: whether a restarted container
# keeps its ephemeral port is the runtime's business.
_host_port() {
    local out
    out="$("$CONTAINER_CLI" port "$1" "$2" 2>/dev/null | head -n1)"
    [[ -n "$out" ]] || return 1
    echo "${out##*:}"
}

# Starts container $1, creating it from the remaining `run` arguments if it
# does not exist. Ports are published on loopback only: MongoDB and NATS
# accept unauthenticated clients, and a 0.0.0.0 binding would put them on
# the LAN for as long as they run.
_start_container() {
    local name="$1"
    shift
    echo "  Starting $name..."
    "$CONTAINER_CLI" start "$name" >/dev/null 2>&1 || \
        "$CONTAINER_CLI" run -d --name "$name" --label "wip.test.clone=$REPO_ROOT" "$@" >/dev/null
}

_ensure_mongo() {
    if _container_running "$TEST_MONGO"; then return 0; fi
    _start_container "$TEST_MONGO" -p 127.0.0.1::27017 mongo:7
    for _ in $(seq 1 15); do
        if "$CONTAINER_CLI" exec "$TEST_MONGO" mongosh --quiet --eval "db.runCommand('ping')" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    echo "ERROR: $TEST_MONGO did not become ready within 15s" >&2
    return 1
}

_ensure_postgres() {
    if _container_running "$TEST_POSTGRES"; then return 0; fi
    _start_container "$TEST_POSTGRES" -p 127.0.0.1::5432 \
        -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test \
        -e POSTGRES_DB=wip_test postgres:16
    for _ in $(seq 1 15); do
        if "$CONTAINER_CLI" exec "$TEST_POSTGRES" pg_isready -U test >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    echo "ERROR: $TEST_POSTGRES did not become ready within 15s" >&2
    return 1
}

_ensure_nats() {
    if _container_running "$TEST_NATS"; then return 0; fi
    _start_container "$TEST_NATS" -p 127.0.0.1::4222 -p 127.0.0.1::8222 nats:2 -js -m 8222
    local monitor
    for _ in $(seq 1 15); do
        if monitor="$(_host_port "$TEST_NATS" 8222)" \
            && curl -fsS "http://127.0.0.1:$monitor/healthz" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    echo "ERROR: $TEST_NATS did not become ready within 15s" >&2
    return 1
}

# Exports the address of this clone's container for dep $1, unless the
# caller's environment already names one. Returns 1 when the container
# publishes no port (not running).
_export_endpoint() {
    local var container port url
    case "$1" in
        mongo)    var=MONGO_URI;         container="$TEST_MONGO";    port=27017 ;;
        postgres) var=POSTGRES_TEST_URI; container="$TEST_POSTGRES"; port=5432 ;;
        nats)     var=NATS_TEST_URL;     container="$TEST_NATS";     port=4222 ;;
    esac
    if [[ -n "${!var:-}" ]]; then
        echo "  $var=${!var} (from the environment)"
        return 0
    fi
    port="$(_host_port "$container" "$port")" || return 1
    case "$1" in
        mongo)    url="mongodb://127.0.0.1:$port/" ;;
        # The conftest's skip-marker treats POSTGRES_TEST_URI as the opt-in
        # signal: unset env var → integration tests skip even if the
        # default URI would work.
        postgres) url="postgresql://test:test@127.0.0.1:$port/wip_test" ;;
        nats)     url="nats://127.0.0.1:$port" ;;
    esac
    export "$var=$url"
    echo "  $var=$url"
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
        # Still point the suite at this clone's containers when they are
        # up: falling through to the conftest defaults (localhost:27017,
        # …) would reach whatever another clone or an older version of
        # this script runs there, which this clone's lock does not guard.
        if [[ -n "$CONTAINER_CLI" ]]; then
            for dep in $deps; do
                _export_endpoint "$dep" || true
            done
        fi
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
            mongo)    _ensure_mongo    || return 1 ;;
            postgres) _ensure_postgres || return 1 ;;
            nats)     _ensure_nats     || return 1 ;;
            *)
                echo "ERROR: unknown test-container dep '$dep' for $component" >&2
                return 1
                ;;
        esac
        if ! _export_endpoint "$dep"; then
            echo "ERROR: could not read the host port of this clone's $dep container" >&2
            return 1
        fi
    done
}

# Removes this clone's test containers (and with them their data).
remove_test_containers() {
    if [[ -z "$CONTAINER_CLI" ]]; then
        echo "ERROR: no container CLI found (podman or docker)." >&2
        return 1
    fi
    local name
    for name in "$TEST_MONGO" "$TEST_POSTGRES" "$TEST_NATS"; do
        "$CONTAINER_CLI" container inspect "$name" >/dev/null 2>&1 || continue
        "$CONTAINER_CLI" rm -f "$name" >/dev/null
        echo "  Removed $name"
    done
}

# --- Argument parsing ---
if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <component|all> [pytest-args...]" >&2
    echo "       $0 --down   (remove this clone's test containers)" >&2
    echo "" >&2
    echo "Components: registry, def-store, template-store, document-store," >&2
    echo "            reporting-sync, ingest-gateway, mcp-server" >&2
    echo "Libraries:  wip-auth, wip-archive" >&2
    echo "Tools:      deployer, agent-scripts, scaffold" >&2
    echo "Special:    all (run everything)" >&2
    exit 1
fi

TARGET="$1"
shift

# --- Resolve component to directory ---

PYTHON_COMPONENTS=(registry def-store template-store document-store reporting-sync ingest-gateway mcp-server auth-gateway)
PYTHON_LIBS=(wip-auth wip-archive)
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
        # shellcheck disable=SC2001  # per-line indent of multiline output — parameter expansion can't do this readably
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
    # wip-toolkit: CI's toolkit job recipe. wip-archive goes first — it is
    # the toolkit's dependency and lives in this repo, not on PyPI. The
    # integration suite mounts the four services in-process, so their
    # requirements come too, each installed from its own directory (the
    # files carry a relative -e path).
    if [[ "$name" == "wip-toolkit" ]]; then
        echo "  Provisioning wip-toolkit test deps (CI recipe, one-time)..."
        local ok=1 c
        pip install -q -e "$REPO_ROOT/libs/wip-archive" -e "$REPO_ROOT/WIP-Toolkit" || ok=0
        for c in registry def-store template-store document-store; do
            (cd "$REPO_ROOT/components/$c" && pip install -q -r requirements.txt) || ok=0
        done
        if (( ok )); then
            touch "$marker"
        else
            echo "  WARNING: wip-toolkit test-dep provisioning failed — imports may error below." >&2
        fi
        _pip_check_tripwire "$name"
        return 0
    fi
    [[ "$dir" == "$REPO_ROOT/components/"* ]] || return 0
    echo "  Provisioning $name test deps (CI recipe, one-time)..."
    if (cd "$dir" && pip install -q -r requirements.txt) \
        && (cd "$REPO_ROOT/components/registry" && pip install -q -r requirements.txt) \
        && pip install -q pytest-asyncio pytest-xdist httpx; then
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

    # Intra-run parallelism, opt-in per component: these suites' conftests
    # give every xdist worker its own databases, so they run -n auto by
    # default. Other suites join this list only after getting the same
    # per-worker isolation — without it, workers wipe each other's
    # fixtures (the corruption class the clone lock serializes at run
    # granularity). WIP_TEST_XDIST=0 disables; a caller-supplied
    # -n/--numprocesses wins. template-store's conftest isolates workers
    # too, but the suite runs ~3x SLOWER under xdist (25 s serial, ~75 s
    # -n auto) for a reason not yet found, so it stays serial.
    local xdist_args=() xdist_suite=0
    case "$name" in
        document-store|def-store|registry) xdist_suite=1 ;;
    esac
    if (( xdist_suite )) && [[ "${WIP_TEST_XDIST:-1}" != "0" ]]; then
        local caller_n=0
        for arg in "$@"; do
            case "$arg" in
                -n|-n*|--numprocesses*) caller_n=1 ;;
            esac
        done
        (( caller_n )) || xdist_args=(-n auto)
    fi

    echo "=== $name ==="
    ensure_test_containers "$name" || return 1
    # The ${arr[@]+...} guard keeps an EMPTY xdist_args from tripping
    # `set -u` on bash < 4.4 (macOS system bash is 3.2).
    if (( has_target )); then
        (cd "$dir" && PYTHONPATH=src pytest ${xdist_args[@]+"${xdist_args[@]}"} "$@")
    else
        (cd "$dir" && PYTHONPATH=src pytest tests/ ${xdist_args[@]+"${xdist_args[@]}"} "$@")
    fi
}

# --- Cross-run test-infrastructure lock (CASE-742) ---
#
# A clone's test containers are shared by every run in that clone, and every
# conftest connects under a fixed database name — so two concurrent suite
# runs in one clone (a backgrounded run next to a foreground one) share
# databases and wipe each other's fixtures mid-flight. The observed symptom
# is mass failures with rollback / not-found signatures and a failure count
# that changes on every run: phantom regressions in both directions.
# Serialize instead: one run of a clone's infrastructure at a time. The lock
# is per clone, like the containers — other clones have their own of both.
#
# Scoped, not global: components whose _component_deps is empty (deployer,
# scaffold, agent-scripts, mcp-server, wip-archive, auth-gateway) never touch
# the containers and are not serialized. `all` locks once up front (it
# includes mongo components; the loop runs inline in this process). --down
# locks so it cannot remove containers from under a running suite.
# WIP_TEST_SKIP_CONTAINERS still locks: it skips PROVISIONING, but the
# conftests still connect to this clone's containers when they are up.
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

WIP_TEST_LOCK_DIR="${WIP_TEST_LOCK_DIR:-/tmp/wip-test-infra-$CLONE_KEY.lock}"

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

    echo "ERROR: another test run holds this clone's test infrastructure:" >&2
    _lock_owner_summary >&2
    echo "       Concurrent runs in one clone share its databases and" >&2
    echo "       corrupt each other. Re-run when it finishes, or" >&2
    echo "       WIP_TEST_WAIT=1 to queue (WIP_TEST_WAIT_TIMEOUT bounds the wait)." >&2
    return 1
}

_needs_lock() {
    [[ "$TARGET" == "all" || "$TARGET" == "--down" ]] && return 0
    [[ -n "$(_component_deps "$TARGET")" ]]
}

if [[ "${WIP_TEST_LOCK_HELD:-}" != "1" ]] && _needs_lock; then
    acquire_test_lock || exit 1
    export WIP_TEST_LOCK_HELD=1
fi

# Test hook: prove lock behaviour without running a suite. Prints the
# outcome and exits — LOCK_ACQUIRED (this run holds it), or LOCK_SKIPPED
# (no-dep target / already held by a parent), plus the lock path in use.
# Contention exits above with the owner message before reaching this line.
if [[ "${WIP_TEST_LOCK_PROBE:-}" == "1" ]]; then
    echo "lock_dir=$WIP_TEST_LOCK_DIR"
    if [[ -f "$WIP_TEST_LOCK_DIR/owner" ]] && grep -q "^pid=$$\$" "$WIP_TEST_LOCK_DIR/owner"; then
        echo "LOCK_ACQUIRED"
    else
        echo "LOCK_SKIPPED"
    fi
    exit 0
fi

# --- Execute ---

if [[ "$TARGET" == "--down" ]]; then
    remove_test_containers
    exit $?
fi

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
