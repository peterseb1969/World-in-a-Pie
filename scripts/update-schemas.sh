#!/usr/bin/env bash
# Fetch OpenAPI specs from all running WIP services into schemas/.
# Both the MCP server (Python) and @wip/client (TypeScript) generators
# read from this directory — single source of truth.
#
# Usage: ./scripts/update-schemas.sh [BASE_URL]

set -euo pipefail

BASE_URL="${1:-http://localhost}"
SCHEMA_DIR="$(cd "$(dirname "$0")/.." && pwd)/schemas"
mkdir -p "$SCHEMA_DIR"

SERVICES="registry:8001 def-store:8002 template-store:8003 document-store:8004 reporting-sync:8005"

for entry in $SERVICES; do
  name="${entry%%:*}"
  port="${entry##*:}"
  url="${BASE_URL}:${port}/openapi.json"
  out="${SCHEMA_DIR}/${name}.json"
  printf "  %-20s %s ... " "$name" "$url"
  if curl -sf "$url" -o "$out"; then
    echo "OK"
  else
    echo "FAILED (keeping existing)"
  fi
done

echo ""
echo "Schemas saved to: $SCHEMA_DIR"
echo "Next steps:"
echo "  MCP server:   cd components/mcp-server && python -m scripts.generate_schemas"
echo "  @wip/client:  cd libs/wip-client && npx tsx scripts/generate-types.ts --from-cache"
