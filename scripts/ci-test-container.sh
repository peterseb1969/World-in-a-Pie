#!/usr/bin/env bash
# Ensure a CI test container is running with its ports on loopback only.
#
# Usage: scripts/ci-test-container.sh NAME [docker run args...]
#   e.g. scripts/ci-test-container.sh test-mongo -p 127.0.0.1:27017:27017 mongo:7
#
# The test containers (MongoDB and NATS without authentication, PostgreSQL
# with a fixed password) persist on the CI host between runs. One still bound
# to all interfaces — created before the loopback binding — is removed and
# recreated from the given `docker run` arguments.
#
# CI jobs run in parallel and share these containers, so every step here
# tolerates another job doing the same thing at the same moment: a removal
# already in progress, or the name taken by a concurrent `docker run`. Each
# attempt re-reads the state and the loop retries until the container runs
# bound to loopback.
#
# CONTAINER_CLI overrides the runtime (default: docker), e.g. for a local
# test with podman.
set -u

name="$1"
shift
cli="${CONTAINER_CLI:-docker}"

# Bound to all interfaces: podman reports 0.0.0.0, docker an empty HostIp.
exposed() {
    "$cli" inspect -f '{{json .HostConfig.PortBindings}}' "$name" 2>/dev/null \
        | grep -qE '"HostIp":"(0\.0\.0\.0)?"'
}

running() {
    [ "$("$cli" inspect -f '{{.State.Running}}' "$name" 2>/dev/null)" = "true" ]
}

for _ in $(seq 1 30); do
    if exposed; then
        echo "Recreating $name on 127.0.0.1 (it was published on all interfaces)"
        "$cli" rm -f "$name" >/dev/null 2>&1 || true
    elif running; then
        exit 0
    elif "$cli" start "$name" >/dev/null 2>&1; then
        continue  # re-check: started, and not exposed
    elif "$cli" run -d --name "$name" "$@" >/dev/null 2>&1; then
        continue
    fi
    sleep 2
done

echo "ERROR: $name is not running bound to 127.0.0.1 after 30 attempts" >&2
exit 1
