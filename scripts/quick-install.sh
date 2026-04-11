#!/usr/bin/env bash
# WIP Quick Install — fetch the install kit and set up a WIP instance.
#
# Usage:
#   # From the internet:
#   curl -fsSL https://raw.githubusercontent.com/peterseb1969/World-in-a-Pie/develop/scripts/quick-install.sh \
#     | bash -s -- --yes
#
#   # With a hostname other than localhost:
#   curl -fsSL https://raw.githubusercontent.com/peterseb1969/World-in-a-Pie/develop/scripts/quick-install.sh \
#     | bash -s -- --yes pi.local
#
#   # From a local checkout (smoke test — doesn't need network):
#   bash scripts/quick-install.sh --source /Users/peter/Development/World-in-a-Pie localhost
#
# What it does:
#   1. Checks podman, podman-compose, python3+bcrypt, curl
#   2. Creates an install directory (default: ~/wip-demo)
#   3. Fetches compose files, templates, and setup-wip.sh from source
#   4. Runs setup-wip.sh <hostname>
#   5. Runs start-wip.sh -y (unless --no-start)
#
# What it does NOT do:
#   - Fetch app chunks (WIP core only — drop docker-compose.app.*.yml files
#     manually into the install dir and re-run setup-wip.sh)
#   - Install podman or any other dependency
#   - Hand-hold on the Caddy self-signed cert (browser warning is normal)

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }
log_step()  { echo -e "${BLUE}[STEP]${NC} $1"; }

# ── Defaults ─────────────────────────────────────────────────────

DEFAULT_SOURCE="https://raw.githubusercontent.com/peterseb1969/World-in-a-Pie/develop"
SOURCE="$DEFAULT_SOURCE"
INSTALL_DIR="${HOME}/wip-demo"
HOSTNAME_ARG=""
START_AFTER=true
ASSUME_YES=false

usage() {
    cat <<EOF
Usage: $(basename "$0") [options] [hostname]

Fetch the WIP install kit and bring up a WIP instance.

Options:
  --source URL|PATH   Source for install files. URL for curl, absolute path
                      for local copy. Default: GitHub raw, develop branch.
  --install-dir PATH  Install directory. Default: ~/wip-demo
  --no-start          Don't run start-wip.sh after setup (just prepare files)
  --yes, -y           Skip confirmation prompts (required when piped from curl)
  --help, -h          Show this help

Arguments:
  hostname            Hostname as seen by browsers. Default: localhost

Examples:
  # Piped from curl (use --yes, no interactive prompts)
  curl -fsSL https://raw.githubusercontent.com/peterseb1969/World-in-a-Pie/develop/scripts/quick-install.sh \\
    | bash -s -- --yes

  # With a LAN hostname
  bash quick-install.sh --yes pi-poe-8gb.local

  # Smoke test from a local checkout
  bash quick-install.sh --source /Users/peter/Development/World-in-a-Pie localhost
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --source)      SOURCE="$2"; shift 2 ;;
        --install-dir) INSTALL_DIR="$2"; shift 2 ;;
        --no-start)    START_AFTER=false; shift ;;
        --yes|-y)      ASSUME_YES=true; shift ;;
        --help|-h)     usage ;;
        -*)            log_error "Unknown option: $1"; exit 1 ;;
        *)             HOSTNAME_ARG="$1"; shift ;;
    esac
done

HOSTNAME_ARG="${HOSTNAME_ARG:-localhost}"

# If stdin is not a terminal (e.g. piped from curl), prompts can't work.
# Require --yes in that case.
if [[ ! -t 0 ]] && ! $ASSUME_YES; then
    log_error "Stdin is not a terminal (piped?) — pass --yes to skip prompts."
    log_error "Example: curl ... | bash -s -- --yes"
    exit 1
fi

confirm() {
    # confirm "question" → 0 on yes, 1 on no. Honors ASSUME_YES.
    local prompt="$1"
    if $ASSUME_YES; then
        return 0
    fi
    local answer
    read -rp "${prompt} [y/N]: " answer
    [[ "$answer" =~ ^[Yy]$ ]]
}

echo ""
echo -e "${BOLD}WIP Quick Install${NC}"
echo "Source:      ${SOURCE}"
echo "Install dir: ${INSTALL_DIR}"
echo "Hostname:    ${HOSTNAME_ARG}"
echo "Start after: ${START_AFTER}"
echo ""

# ── Dependency checks ───────────────────────────────────────────

MISSING=()

if ! command -v podman >/dev/null 2>&1; then
    MISSING+=("podman — install from https://podman.io/docs/installation")
fi

if ! command -v podman-compose >/dev/null 2>&1 && ! command -v docker >/dev/null 2>&1; then
    MISSING+=("podman-compose (pip install podman-compose) or docker")
fi

if ! command -v python3 >/dev/null 2>&1; then
    MISSING+=("python3")
elif ! python3 -c "import bcrypt" >/dev/null 2>&1; then
    MISSING+=("python3 bcrypt module (pip3 install bcrypt)")
fi

if ! command -v curl >/dev/null 2>&1; then
    MISSING+=("curl")
fi

if [[ ${#MISSING[@]} -gt 0 ]]; then
    log_error "Missing dependencies:"
    for dep in "${MISSING[@]}"; do
        echo "  - ${dep}"
    done
    echo ""
    log_error "Install them and re-run this script."
    exit 1
fi
log_info "Dependency commands found"

# Podman must be functional (machine started on macOS, daemon on Linux).
if ! podman info >/dev/null 2>&1; then
    log_error "Podman is installed but not functional."
    if [[ "$(uname)" == "Darwin" ]]; then
        log_error "On macOS, start the Podman machine first:"
        log_error "  podman machine init     # first time only"
        log_error "  podman machine start"
    fi
    exit 1
fi
log_info "Podman is functional"

# ── Port conflict check ────────────────────────────────────────

CONFLICT_PORTS=()
for port in 8443 27017 5432 9000 9001 4222 8222 8001 8002 8003 8004 8005 8006; do
    if command -v lsof >/dev/null 2>&1 && lsof -ti:"$port" >/dev/null 2>&1; then
        CONFLICT_PORTS+=("$port")
    fi
done

if [[ ${#CONFLICT_PORTS[@]} -gt 0 ]]; then
    log_warn "The following ports are already in use: ${CONFLICT_PORTS[*]}"
    log_warn "This usually means another WIP instance (or a collision) is already running."
    log_warn "Starting a second instance with the same container names will fail."
    if ! confirm "Continue anyway?"; then
        log_error "Aborted."
        exit 1
    fi
fi

# ── Prepare install directory ──────────────────────────────────

if [[ -d "$INSTALL_DIR" ]] && [[ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null || true)" ]]; then
    log_warn "Install directory ${INSTALL_DIR} is not empty."
    if ! confirm "Remove existing contents and continue?"; then
        log_error "Aborted."
        exit 1
    fi
    rm -rf "$INSTALL_DIR"
fi

mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"
log_info "Install directory: ${INSTALL_DIR}"

# ── Fetch install kit files ────────────────────────────────────

# Determine source mode: local path vs URL.
SOURCE_MODE="url"
if [[ "$SOURCE" == /* ]]; then
    SOURCE_MODE="local"
    if [[ ! -d "$SOURCE" ]]; then
        log_error "Local source directory does not exist: ${SOURCE}"
        exit 1
    fi
fi

fetch_file() {
    local rel="$1"
    local dest="${INSTALL_DIR}/${rel}"
    mkdir -p "$(dirname "$dest")"

    if [[ "$SOURCE_MODE" == "local" ]]; then
        if [[ ! -f "${SOURCE}/${rel}" ]]; then
            log_error "File not found in local source: ${SOURCE}/${rel}"
            return 1
        fi
        cp "${SOURCE}/${rel}" "$dest"
    else
        local url="${SOURCE}/${rel}"
        if ! curl -fsSL "$url" -o "$dest"; then
            log_error "Failed to fetch: ${url}"
            return 1
        fi
    fi
}

log_step "Fetching install kit files"

FILES=(
    "docker-compose.production.yml"
    ".env.production.example"
    "scripts/setup-wip.sh"
    "config/production/Caddyfile.template"
    "config/production/dex-config.template"
)

for f in "${FILES[@]}"; do
    fetch_file "$f"
done

chmod +x "${INSTALL_DIR}/scripts/setup-wip.sh"
log_info "Install kit fetched"

# ── Run setup-wip.sh ────────────────────────────────────────────

log_step "Running setup-wip.sh ${HOSTNAME_ARG}"
echo ""
bash "${INSTALL_DIR}/scripts/setup-wip.sh" "$HOSTNAME_ARG"

# ── Optionally start ───────────────────────────────────────────

if ! $START_AFTER; then
    echo ""
    log_info "Setup complete. To start WIP:"
    echo "    cd ${INSTALL_DIR}"
    echo "    ./start-wip.sh -y"
    exit 0
fi

echo ""
log_step "Starting WIP"
log_warn "First run pulls ~14 images (several GB). On typical WiFi this takes 10-20 minutes."
log_warn "Watch for errors — if a pull fails, re-run ./start-wip.sh -y to resume."
echo ""

cd "$INSTALL_DIR"
./start-wip.sh -y

echo ""
log_info "WIP containers started. Waiting 45s for health checks..."
sleep 45
echo ""

cat <<DONE
==========================================
  ${BOLD}WIP is (probably) up${NC}

  Open:  https://${HOSTNAME_ARG}:8443
  Login credentials: see the output of setup-wip.sh above.

  Notes:
    - Your browser will show a cert warning (Caddy self-signed). Click through.
    - To check container health: podman ps
    - To stop:                    cd ${INSTALL_DIR} && podman-compose down
    - To see logs:                cd ${INSTALL_DIR} && podman-compose logs -f
==========================================
DONE
