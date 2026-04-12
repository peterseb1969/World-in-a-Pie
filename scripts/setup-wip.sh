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

# ── Pre-flight: check dependencies ───────────────────────────────

MISSING=()

if ! command -v podman-compose &>/dev/null && ! command -v docker &>/dev/null; then
    MISSING+=("podman-compose or docker")
fi

if ! command -v python3 &>/dev/null; then
    MISSING+=("python3")
fi

if command -v python3 &>/dev/null && ! python3 -c "import bcrypt" &>/dev/null; then
    MISSING+=("python3-bcrypt (pip install bcrypt or apt install python3-bcrypt)")
fi

if ! command -v curl &>/dev/null && ! command -v wget &>/dev/null; then
    MISSING+=("curl or wget (for health checks)")
fi

if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo -e "${RED}[ERROR]${NC} Missing dependencies:"
    for dep in "${MISSING[@]}"; do
        echo "  - ${dep}"
    done
    echo ""
    echo "Install them and re-run this script."
    exit 1
fi

echo -e "${GREEN}[OK]${NC} Dependencies checked"

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

# ── Step 1b: Dex user passwords ──────────────────────────────────
#
# Passwords and hashes are stored in .env so re-running setup doesn't
# invalidate them. Only generated on first run.

bcrypt_hash() {
    python3 -c "import bcrypt; print(bcrypt.hashpw(b'$1', bcrypt.gensalt(10)).decode())"
}

# Check if passwords already exist in .env
EXISTING_ADMIN_HASH=$(grep '^WIP_DEX_ADMIN_HASH=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)

if [[ -n "$EXISTING_ADMIN_HASH" ]]; then
    # Re-read stored passwords and hashes
    ADMIN_PASS=$(grep '^WIP_DEX_ADMIN_PASS=' "$ENV_FILE" | cut -d= -f2- || true)
    EDITOR_PASS=$(grep '^WIP_DEX_EDITOR_PASS=' "$ENV_FILE" | cut -d= -f2- || true)
    VIEWER_PASS=$(grep '^WIP_DEX_VIEWER_PASS=' "$ENV_FILE" | cut -d= -f2- || true)
    ADMIN_HASH=$(grep '^WIP_DEX_ADMIN_HASH=' "$ENV_FILE" | cut -d= -f2- || true)
    EDITOR_HASH=$(grep '^WIP_DEX_EDITOR_HASH=' "$ENV_FILE" | cut -d= -f2- || true)
    VIEWER_HASH=$(grep '^WIP_DEX_VIEWER_HASH=' "$ENV_FILE" | cut -d= -f2- || true)
    echo -e "${GREEN}[OK]${NC} Dex user passwords loaded from .env"
else
    # Generate new passwords and hashes
    ADMIN_PASS=$(head -c 12 /dev/urandom | base64 | tr -d '+/=' | head -c 12)
    EDITOR_PASS=$(head -c 12 /dev/urandom | base64 | tr -d '+/=' | head -c 12)
    VIEWER_PASS=$(head -c 12 /dev/urandom | base64 | tr -d '+/=' | head -c 12)

    ADMIN_HASH=$(bcrypt_hash "$ADMIN_PASS")
    EDITOR_HASH=$(bcrypt_hash "$EDITOR_PASS")
    VIEWER_HASH=$(bcrypt_hash "$VIEWER_PASS")

    # Persist to .env
    cat >> "$ENV_FILE" <<DEXPASSWORDS

# =============================================================================
# DEX USER CREDENTIALS (auto-generated — do not edit hashes manually)
# =============================================================================
WIP_DEX_ADMIN_PASS=${ADMIN_PASS}
WIP_DEX_ADMIN_HASH=${ADMIN_HASH}
WIP_DEX_EDITOR_PASS=${EDITOR_PASS}
WIP_DEX_EDITOR_HASH=${EDITOR_HASH}
WIP_DEX_VIEWER_PASS=${VIEWER_PASS}
WIP_DEX_VIEWER_HASH=${VIEWER_HASH}
DEXPASSWORDS
    echo -e "${GREEN}[OK]${NC} Dex user passwords generated and saved to .env"
fi

# ── Step 2: Generate Caddy config ────────────────────────────────

CADDY_DIR="${INSTALL_DIR}/config/caddy"
CADDY_TEMPLATE="${INSTALL_DIR}/config/production/Caddyfile.template"
CADDY_OUT="${CADDY_DIR}/Caddyfile"

mkdir -p "$CADDY_DIR"

if [[ ! -f "$CADDY_TEMPLATE" ]]; then
    echo -e "${RED}[ERROR]${NC} Caddyfile.template not found at ${CADDY_TEMPLATE}"
    exit 1
fi

# ── Step 2a: Scan for app compose chunks ─────────────────────────

APP_ROUTES=""
DEX_CLIENTS=""
APP_NAMES=()
APP_FILES=()

for chunk in "${INSTALL_DIR}"/docker-compose.app.*.yml; do
    [[ -f "$chunk" ]] || continue

    # Extract labels from the compose chunk
    app_name=$(grep 'wip.app.name:' "$chunk" | head -1 | sed 's/.*wip.app.name: *"\(.*\)"/\1/')
    app_route=$(grep 'wip.app.route:' "$chunk" | head -1 | sed 's/.*wip.app.route: *"\(.*\)"/\1/')
    app_port=$(grep 'wip.app.port:' "$chunk" | head -1 | sed 's/.*wip.app.port: *"\(.*\)"/\1/')
    app_oidc=$(grep 'wip.app.oidc:' "$chunk" | head -1 | grep -c '"true"' || true)
    container=$(grep 'container_name:' "$chunk" | head -1 | sed 's/.*container_name: *//')

    if [[ -z "$app_route" || -z "$app_port" || -z "$container" ]]; then
        echo -e "${YELLOW}[WARN]${NC} Skipping ${chunk##*/}: missing wip.app labels"
        continue
    fi

    APP_NAMES+=("${app_name:-${chunk##*/}}")
    APP_FILES+=("${chunk##*/}")

    # Generate Caddy route block (one line per entry, expanded later)
    APP_ROUTES+="ROUTE:${app_route}:${container}:${app_port}
"

    # Collect Dex client info if OIDC is enabled
    if [[ "$app_oidc" -gt 0 ]]; then
        client_id=$(grep 'wip.app.oidc.client_id:' "$chunk" | head -1 | sed 's/.*wip.app.oidc.client_id: *"\(.*\)"/\1/')
        client_secret=$(grep 'wip.app.oidc.client_secret:' "$chunk" | head -1 | sed 's/.*wip.app.oidc.client_secret: *"\(.*\)"/\1/')
        if [[ -n "$client_id" && -n "$client_secret" ]]; then
            DEX_CLIENTS+="OIDC:${client_id}:${app_name}:${client_secret}:${app_route}:${app_port}
"
        fi
    fi
done

if [[ ${#APP_NAMES[@]} -gt 0 ]]; then
    echo -e "${GREEN}[OK]${NC} Found ${#APP_NAMES[@]} app(s):"
    for i in "${!APP_NAMES[@]}"; do
        echo "     - ${APP_NAMES[$i]} (${APP_FILES[$i]})"
    done
else
    echo -e "${GREEN}[OK]${NC} No app compose chunks found (WIP core only)"
fi

# ── Step 2b: Generate Caddy config ──────────────────────────────

sed "s/{{WIP_HOSTNAME}}/${HOSTNAME}/g" "$CADDY_TEMPLATE" > "$CADDY_OUT"

# Insert app routes at the marker
tmp="${CADDY_OUT}.tmp"
if [[ -n "$APP_ROUTES" ]]; then
    # Build the Caddy route blocks and replace the marker
    {
        while IFS= read -r line; do
            if [[ "$line" == *'{{APP_ROUTES}}'* ]]; then
                # Expand each ROUTE: entry into a Caddy handle block
                while IFS=: read -r _ route container port; do
                    [[ -z "$route" ]] && continue
                    # Redirect /apps/foo → /apps/foo/ (trailing slash).
                    # Without this, Caddy's handle /apps/foo/* glob doesn't
                    # match the bare path, and it falls through to the
                    # default handler (Vue Console).
                    echo "    handle ${route} {"
                    echo "        redir ${route}/ permanent"
                    echo "    }"
                    echo "    handle ${route}/* {"
                    echo "        reverse_proxy ${container}:${port}"
                    echo "    }"
                done <<< "$APP_ROUTES"
            else
                echo "$line"
            fi
        done < "$CADDY_OUT"
    } > "$tmp" && mv "$tmp" "$CADDY_OUT"
else
    # No apps — remove the marker line
    grep -v '{{APP_ROUTES}}' "$CADDY_OUT" > "$tmp" && mv "$tmp" "$CADDY_OUT"
fi
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

# Insert app OIDC clients into the staticClients section of Dex config
if [[ -n "$DEX_CLIENTS" ]]; then
    # Write client entries to a temp file
    clients_tmp="${DEX_OUT}.clients"
    > "$clients_tmp"
    while IFS=: read -r _ client_id app_name client_secret app_route app_port; do
        [[ -z "$client_id" ]] && continue
        cat >> "$clients_tmp" <<DEXCLIENT
  - id: ${client_id}
    name: ${app_name}
    secret: ${client_secret}
    redirectURIs:
      - https://${HOSTNAME}:8443${app_route}/auth/callback
      - https://${HOSTNAME}:8443/auth/callback
      - http://localhost:${app_port}/auth/callback
DEXCLIENT
    done <<< "$DEX_CLIENTS"

    # Insert the clients file content before the "connectors:" line
    tmp="${DEX_OUT}.tmp"
    while IFS= read -r line; do
        if [[ "$line" == "connectors:"* ]]; then
            cat "$clients_tmp"
            echo ""
        fi
        echo "$line"
    done < "$DEX_OUT" > "$tmp" && mv "$tmp" "$DEX_OUT"
    rm -f "$clients_tmp"
fi
echo -e "${GREEN}[OK]${NC} config/dex/config.yaml generated"

# ── Step 4: Generate start-wip.sh ────────────────────────────────

START_SCRIPT="${INSTALL_DIR}/start-wip.sh"
cat > "$START_SCRIPT" <<'STARTEOF'
#!/usr/bin/env bash
# Start WIP core + discovered apps.
# Generated by setup-wip.sh — re-run setup to regenerate.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTO_YES=false
[[ "${1:-}" == "-y" ]] && AUTO_YES=true

# Detect compose command
if command -v podman-compose &>/dev/null; then
    COMPOSE="podman-compose"
elif command -v docker &>/dev/null; then
    COMPOSE="docker compose"
else
    echo "ERROR: No compose command found (podman-compose or docker)"
    exit 1
fi

# Core compose file
COMPOSE_FILES="-f ${SCRIPT_DIR}/docker-compose.production.yml"

# Discover app chunks (skip .disabled)
APPS=()
APP_FILES=()
for chunk in "${SCRIPT_DIR}"/docker-compose.app.*.yml; do
    [[ -f "$chunk" ]] || continue
    name=$(grep 'wip.app.name:' "$chunk" 2>/dev/null | head -1 | sed 's/.*wip.app.name: *"\(.*\)"/\1/')
    route=$(grep 'wip.app.route:' "$chunk" 2>/dev/null | head -1 | sed 's/.*wip.app.route: *"\(.*\)"/\1/')
    APPS+=("${name:-${chunk##*/}} (${route:-?})")
    APP_FILES+=("$chunk")
done

# Prompt for app approval
APPROVED_FILES=()
if [[ ${#APPS[@]} -gt 0 ]]; then
    echo ""
    echo "Found ${#APPS[@]} app(s):"
    for i in "${!APPS[@]}"; do
        echo "  [$((i+1))] ${APPS[$i]}  — ${APP_FILES[$i]##*/}"
    done
    echo ""

    if $AUTO_YES; then
        APPROVED_FILES=("${APP_FILES[@]}")
        echo "Starting all apps (-y flag)."
    else
        read -rp "Start all? [Y/n/select]: " choice
        case "${choice,,}" in
            ""|y|yes)
                APPROVED_FILES=("${APP_FILES[@]}")
                ;;
            n|no)
                echo "Starting WIP core only."
                ;;
            s|select)
                for i in "${!APPS[@]}"; do
                    read -rp "  Start ${APPS[$i]}? [Y/n]: " app_choice
                    case "${app_choice,,}" in
                        ""|y|yes) APPROVED_FILES+=("${APP_FILES[$i]}") ;;
                    esac
                done
                ;;
            *)
                echo "Unknown choice. Starting WIP core only."
                ;;
        esac
    fi
    echo ""
fi

# Build compose command
for f in "${APPROVED_FILES[@]}"; do
    COMPOSE_FILES="$COMPOSE_FILES -f $f"
done

# Always pull latest images before starting. Without this, Podman reuses
# locally cached images even when the remote registry has a newer digest
# for the same tag — causing stale code to run after a rebuild+push.
echo "Pulling latest images..."
$COMPOSE $COMPOSE_FILES pull --ignore-buildable 2>/dev/null || $COMPOSE $COMPOSE_FILES pull 2>/dev/null || true

echo "Starting: $COMPOSE $COMPOSE_FILES up -d"
$COMPOSE $COMPOSE_FILES up -d
STARTEOF
chmod +x "$START_SCRIPT"
echo -e "${GREEN}[OK]${NC} start-wip.sh generated"

# ── Done ─────────────────────────────────────────────────────────

echo ""
echo "=========================================="
echo -e "  ${GREEN}Setup complete${NC}"
echo ""
echo "  Next steps:"
echo ""
echo "  1. Review .env (passwords were auto-generated)"
echo "  2. Start WIP:"
echo ""
echo "     ./start-wip.sh        # interactive — choose which apps to start"
echo "     ./start-wip.sh -y     # start everything"
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
if [[ ${#APP_NAMES[@]} -gt 0 ]]; then
    echo ""
    echo "  Apps detected:"
    for i in "${!APP_NAMES[@]}"; do
        echo "    ${APP_NAMES[$i]}  →  https://${HOSTNAME}:8443$(grep 'wip.app.route:' "${INSTALL_DIR}/${APP_FILES[$i]}" | head -1 | sed 's/.*wip.app.route: *"\(.*\)"/\1/')/"
    done
    echo ""
    echo "  To disable an app: mv docker-compose.app.<name>.yml docker-compose.app.<name>.yml.disabled"
fi
echo "=========================================="
