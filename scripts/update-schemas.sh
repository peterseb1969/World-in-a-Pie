#!/usr/bin/env bash
# Fetch OpenAPI specs from running WIP service containers into schemas/.
#
# Both the MCP server (Python) and @wip/client (TypeScript) generators
# read schemas/ as the single source of truth.
#
# Usage: ./scripts/update-schemas.sh
#
# wip-deploy v2 does not publish service ports to the host, so this
# script runs `curl` inside each container via `podman exec`. If you
# rename containers away from the `wip-` prefix, override CONTAINER_PREFIX:
#
#     CONTAINER_PREFIX=myprefix- ./scripts/update-schemas.sh
#

set -euo pipefail

CONTAINER_PREFIX="${CONTAINER_PREFIX:-wip-}"
SCHEMA_DIR="$(cd "$(dirname "$0")/.." && pwd)/schemas"
mkdir -p "$SCHEMA_DIR"

# service-name:internal-port — port is the one the service listens on
# inside its container, NOT the (now unpublished) host port.
SERVICES="registry:8001 def-store:8002 template-store:8003 document-store:8004 reporting-sync:8005"

if ! command -v podman >/dev/null 2>&1; then
  echo "ERROR: podman not found on PATH" >&2
  exit 1
fi

failed=0
for entry in $SERVICES; do
  name="${entry%%:*}"
  port="${entry##*:}"
  container="${CONTAINER_PREFIX}${name}"
  out="${SCHEMA_DIR}/${name}.json"
  printf "  %-20s %s ... " "$name" "${container}:${port}"

  # Run curl inside the container; capture stdout to the host file.
  # `podman exec` has no -q flag — use --no-trunc=false isn't a thing
  # either; just pipe and check the exit code.
  if podman exec "${container}" curl -sf "http://localhost:${port}/openapi.json" > "${out}.tmp" 2>/dev/null \
     && [ -s "${out}.tmp" ]; then
    mv "${out}.tmp" "${out}"
    echo "OK ($(wc -c < "${out}" | tr -d ' ') bytes)"
  else
    rm -f "${out}.tmp"
    echo "FAILED (keeping existing ${out} if present)"
    failed=$((failed + 1))
  fi
done

echo
echo "Schemas saved to: $SCHEMA_DIR"
echo "Next steps:"
echo "  MCP server:   cd components/mcp-server && python -m scripts.generate_schemas"
echo "  @wip/client:  cd libs/wip-client && npx tsx scripts/generate-types.ts --from-cache"

if [ "$failed" -gt 0 ]; then
  echo
  echo "WARNING: $failed of 5 services failed. Check 'podman ps' for ${CONTAINER_PREFIX}* containers." >&2
  exit 1
fi
