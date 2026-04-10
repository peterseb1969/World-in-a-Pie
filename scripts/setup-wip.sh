#!/usr/bin/env bash
# Set up a WIP production install.
#
# Takes a hostname and generates all config files from templates.
# Run this once on the target machine before `docker compose up`.
#
# Usage:
#   ./setup-wip.sh <hostname>
#   ./setup-wip.sh pi-poe-8gb.local
#   ./setup-wip.sh --help
#
# What it does:
#   1. Generates .env from .env.production.example (if .env doesn't exist)
#   2. Generates config/caddy/Caddyfile from template
#   3. Generates config/dex/config.yaml from template
#   4. Prints next steps

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The install directory is the parent of scripts/
# (install kit layout: wip/scripts/setup-wip.sh, wip/docker-compose.production.yml, etc.)
INSTALL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

usage() {
    cat <<EOF
Usage: $(basename "$0") <hostname>

Set up WIP for production deployment.

Arguments:
  hostname    Your machine's hostname or IP as seen by browsers
              (e.g. pi-poe-8gb.local, 192.168.1.50, wip.example.com)

Examples:
  $(basename "$0") pi-poe-8gb.local
  $(basename "$0") 192.168.1.50

What this does:
  1. Creates .env with your hostname and generated passwords
  2. Creates Caddy and Dex configs from templates
  3. Tells you what to do next
EOF
    exit 0
}

if [[ $# -lt 1 ]] || [[ "$1" == "--help" ]] || [[ "$1" == "-h" ]]; then
    usage
fi

HOSTNAME="$1"

echo ""
echo -e "${BOLD}WIP Production Setup${NC}"
echo "Hostname: ${HOSTNAME}"
echo ""

# ── Step 1: Generate .env ────────────────────────────────────────

ENV_FILE="${INSTALL_DIR}/.env"
ENV_TEMPLATE="${INSTALL_DIR}/.env.production.example"

if [[ -f "$ENV_FILE" ]]; then
    echo -e "${YELLOW}[SKIP]${NC} .env already exists at ${ENV_FILE} — not overwriting"
    echo "       Delete it first if you want to regenerate."
else
    if [[ ! -f "$ENV_TEMPLATE" ]]; then
        echo -e "${RED}[ERROR]${NC} .env.production.example not found"
        exit 1
    fi

    # Generate a random API key (32 chars, URL-safe)
    API_KEY=$(head -c 32 /dev/urandom | base64 | tr -d '+/=' | head -c 32)
    PG_PASS=$(head -c 16 /dev/urandom | base64 | tr -d '+/=' | head -c 16)
    MINIO_PASS=$(head -c 16 /dev/urandom | base64 | tr -d '+/=' | head -c 16)

    sed \
        -e "s/CHANGE-ME-hostname.local/${HOSTNAME}/g" \
        -e "s/CHANGE-ME-random-string/${API_KEY}/g" \
        -e "s/CHANGE-ME-postgres-password/${PG_PASS}/g" \
        -e "s/CHANGE-ME-minio-password/${MINIO_PASS}/g" \
        "$ENV_TEMPLATE" > "$ENV_FILE"

    echo -e "${GREEN}[OK]${NC} .env created"
    echo "     API_KEY: ${API_KEY}"
    echo "     Postgres password: ${PG_PASS}"
    echo "     MinIO password: ${MINIO_PASS}"
fi

# ── Step 1b: Generate user passwords ────────────────────────────

# Generate random passwords for Dex static users
ADMIN_PASS=$(head -c 12 /dev/urandom | base64 | tr -d '+/=' | head -c 12)
EDITOR_PASS=$(head -c 12 /dev/urandom | base64 | tr -d '+/=' | head -c 12)
VIEWER_PASS=$(head -c 12 /dev/urandom | base64 | tr -d '+/=' | head -c 12)

# Hash with bcrypt (Dex requires bcrypt hashes)
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}[ERROR]${NC} python3 is required for password hashing"
    echo "       Install python3 and python3-bcrypt, then re-run."
    exit 1
fi

bcrypt_hash() {
    python3 -c "import bcrypt; print(bcrypt.hashpw(b'$1', bcrypt.gensalt(10)).decode())"
}

ADMIN_HASH=$(bcrypt_hash "$ADMIN_PASS")
EDITOR_HASH=$(bcrypt_hash "$EDITOR_PASS")
VIEWER_HASH=$(bcrypt_hash "$VIEWER_PASS")

# ── Step 2: Generate Caddy config ────────────────────────────────

CADDY_DIR="${INSTALL_DIR}/config/caddy"
CADDY_TEMPLATE="${INSTALL_DIR}/config/production/Caddyfile.template"
CADDY_OUT="${CADDY_DIR}/Caddyfile"

mkdir -p "$CADDY_DIR"

if [[ ! -f "$CADDY_TEMPLATE" ]]; then
    echo -e "${RED}[ERROR]${NC} Caddyfile.template not found at ${CADDY_TEMPLATE}"
    exit 1
fi

sed "s/{{WIP_HOSTNAME}}/${HOSTNAME}/g" "$CADDY_TEMPLATE" > "$CADDY_OUT"
echo -e "${GREEN}[OK]${NC} config/caddy/Caddyfile generated"

# ── Step 3: Generate Dex config ──────────────────────────────────

DEX_DIR="${INSTALL_DIR}/config/dex"
DEX_TEMPLATE="${INSTALL_DIR}/config/production/dex-config.template"
DEX_OUT="${DEX_DIR}/config.yaml"

mkdir -p "$DEX_DIR"

if [[ ! -f "$DEX_TEMPLATE" ]]; then
    echo -e "${RED}[ERROR]${NC} dex-config.template not found at ${DEX_TEMPLATE}"
    exit 1
fi

# Hashes contain $ and / which break sed delimiters — use awk instead
awk \
    -v hostname="$HOSTNAME" \
    -v admin_hash="$ADMIN_HASH" \
    -v editor_hash="$EDITOR_HASH" \
    -v viewer_hash="$VIEWER_HASH" \
    '{
        gsub(/\{\{WIP_HOSTNAME\}\}/, hostname)
        gsub(/\{\{ADMIN_HASH\}\}/, admin_hash)
        gsub(/\{\{EDITOR_HASH\}\}/, editor_hash)
        gsub(/\{\{VIEWER_HASH\}\}/, viewer_hash)
        print
    }' "$DEX_TEMPLATE" > "$DEX_OUT"
echo -e "${GREEN}[OK]${NC} config/dex/config.yaml generated"

# ── Done ─────────────────────────────────────────────────────────

echo ""
echo "=========================================="
echo -e "  ${GREEN}Setup complete${NC}"
echo ""
echo "  Next steps:"
echo ""
echo "  1. Review .env (passwords were auto-generated)"
echo "  2. Pull and start:"
echo ""
echo "     podman-compose -f docker-compose.production.yml up -d"
echo "       — or —"
echo "     docker compose -f docker-compose.production.yml up -d"
echo ""
echo "  3. Wait ~45 seconds for health checks"
echo "  4. Open https://${HOSTNAME}:8443"
echo ""
echo "  Login credentials (generated — save these!):"
echo ""
echo "    admin@wip.local   password: ${ADMIN_PASS}   (admin access)"
echo "    editor@wip.local  password: ${EDITOR_PASS}  (write access)"
echo "    viewer@wip.local  password: ${VIEWER_PASS}  (read access)"
echo ""
echo "  To change a password later, edit config/dex/config.yaml"
echo "  and replace the bcrypt hash. Generate a new hash with:"
echo "    python3 -c \"import bcrypt; print(bcrypt.hashpw(b'NEW_PASSWORD', bcrypt.gensalt(10)).decode())\""
echo "  Then restart Dex: podman restart wip-dex"
echo "=========================================="
