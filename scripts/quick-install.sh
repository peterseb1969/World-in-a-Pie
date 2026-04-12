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

# ANSI-C quoted ($'...') so variables hold literal ESC bytes, not "\033" text.
# This keeps them usable in both echo -e and plain cat <<EOF heredocs.
RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[1;33m'
BLUE=$'\033[0;34m'
BOLD=$'\033[1m'
NC=$'\033[0m'

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

Environment:
  ANTHROPIC_API_KEY   If set, enables the RC Console askBar / NL query feature.
                      Must be EXPORTED before the curl pipe (see examples).
                      When unset, the feature is disabled but the app works.

Examples:
  # Piped from curl (use --yes, no interactive prompts)
  curl -fsSL https://raw.githubusercontent.com/peterseb1969/World-in-a-Pie/develop/scripts/quick-install.sh \\
    | bash -s -- --yes

  # With a LAN hostname
  bash quick-install.sh --yes pi-poe-8gb.local

  # With the Anthropic key for NL query (export first — env prefix on curl
  # does NOT propagate to bash)
  export ANTHROPIC_API_KEY=sk-ant-...
  curl -fsSL https://raw.githubusercontent.com/peterseb1969/World-in-a-Pie/develop/scripts/quick-install.sh \\
    | bash -s -- --yes pi-poe-8gb.local

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

    # Stop any running WIP containers from the previous install.
    if [[ -f "${INSTALL_DIR}/docker-compose.production.yml" ]]; then
        log_step "Stopping previous WIP containers"
        # Compose files may reference app chunks that no longer exist — ignore errors.
        (cd "$INSTALL_DIR" && podman-compose -f docker-compose.production.yml down 2>/dev/null) || true
        # Force-remove any strays that survived compose down.
        podman ps -a --filter 'name=wip-' --format '{{.Names}}' | xargs -r podman rm -f 2>/dev/null || true
    fi

    # Remove pods created by podman-compose. Version 1.5+ creates a pod per
    # compose project. Without this, the next install fails with "container
    # name already in use" because the old pod still holds references to
    # the removed containers.
    podman pod ls -q 2>/dev/null | xargs -r podman pod rm -f 2>/dev/null || true

    # Remove Podman volumes from the previous install. These live in Podman's
    # internal storage (~/.local/share/containers/storage/volumes/), NOT in the
    # install directory. Without this step, a fresh install reuses old volumes
    # whose passwords no longer match the newly generated .env — causing silent
    # auth failures (e.g. Postgres rejects the new password because the volume
    # still holds the old one from initdb).
    INSTALL_BASENAME="$(basename "$INSTALL_DIR")"
    OLD_VOLUMES=$(podman volume ls -q | grep "^${INSTALL_BASENAME}_wip-" || true)
    if [[ -n "$OLD_VOLUMES" ]]; then
        log_step "Removing ${INSTALL_BASENAME}_wip-* volumes from previous install"
        echo "$OLD_VOLUMES" | xargs -r podman volume rm 2>/dev/null || true
    fi

    # Remove cached WIP images. Without this, Podman reuses locally cached
    # images even when the registry has a newer build under the same tag.
    # This caused stale code to run after a rebuild+push cycle.
    OLD_IMAGES=$(podman images --format '{{.Repository}}:{{.Tag}}' | grep -E "^gitea\.local:3000/peter/|^ghcr\.io/peterseb1969/" || true)
    if [[ -n "$OLD_IMAGES" ]]; then
        log_step "Removing cached WIP images"
        echo "$OLD_IMAGES" | xargs -r podman rmi -f 2>/dev/null || true
    fi

    rm -rf "$INSTALL_DIR"
fi

# Always check for orphan WIP resources even when the install dir is empty
# or doesn't exist. A previous install can fail AFTER creating containers,
# pods, and volumes but BEFORE writing anything to the install dir — or the
# user may have deleted the dir manually while Podman resources persist.
INSTALL_BASENAME="$(basename "$INSTALL_DIR")"
ORPHAN_CONTAINERS=$(podman ps -a --filter 'name=wip-' --format '{{.Names}}' 2>/dev/null || true)
ORPHAN_PODS=$(podman pod ls -q 2>/dev/null || true)
ORPHAN_VOLUMES=$(podman volume ls -q 2>/dev/null | grep "^${INSTALL_BASENAME}_wip-" || true)

if [[ -n "$ORPHAN_CONTAINERS" || -n "$ORPHAN_PODS" || -n "$ORPHAN_VOLUMES" ]]; then
    log_step "Cleaning orphan WIP resources from a previous install"
    [[ -n "$ORPHAN_CONTAINERS" ]] && echo "$ORPHAN_CONTAINERS" | xargs -r podman rm -f 2>/dev/null || true
    [[ -n "$ORPHAN_PODS" ]] && echo "$ORPHAN_PODS" | xargs -r podman pod rm -f 2>/dev/null || true
    [[ -n "$ORPHAN_VOLUMES" ]] && echo "$ORPHAN_VOLUMES" | xargs -r podman volume rm 2>/dev/null || true
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
    "docker-compose.app.react-console.yml"
    "docker-compose.app.dnd.yml"
    "docker-compose.app.clintrial.yml"
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

# ── Inject ANTHROPIC_API_KEY into .env if caller set it ────────
#
# RC Console's askBar / NL query feature reads ANTHROPIC_API_KEY server-side.
# The feature degrades gracefully when the key is empty, so this is optional.
# Caller passes the key via an exported env var BEFORE running curl | bash:
#
#     export ANTHROPIC_API_KEY=sk-ant-...
#     curl -fsSL .../quick-install.sh | bash -s -- --yes pi.local
#
# (Note: `VAR=val curl ... | bash ...` does NOT work — the env prefix binds
# to curl only, not bash. Export is the correct form.)

if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    ENV_FILE="${INSTALL_DIR}/.env"
    # Strip any existing ANTHROPIC_API_KEY line (re-run safety), then append fresh.
    if grep -q '^ANTHROPIC_API_KEY=' "$ENV_FILE"; then
        grep -v '^ANTHROPIC_API_KEY=' "$ENV_FILE" > "${ENV_FILE}.tmp"
        mv "${ENV_FILE}.tmp" "$ENV_FILE"
    fi
    {
        echo ""
        echo "# Anthropic API key for RC Console NL query (set by quick-install.sh)"
        echo "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}"
    } >> "$ENV_FILE"
    log_info "ANTHROPIC_API_KEY injected — RC Console NL query enabled"
else
    log_warn "ANTHROPIC_API_KEY not set — RC Console NL query will be disabled"
    log_warn "To enable later:"
    log_warn "  1. Edit ${INSTALL_DIR}/.env, add  ANTHROPIC_API_KEY=sk-ant-..."
    log_warn "  2. cd ${INSTALL_DIR}"
    log_warn "  3. podman-compose -f docker-compose.production.yml \\"
    log_warn "       -f docker-compose.app.react-console.yml up -d"
fi

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

# Pull the generated passwords out of .env so we can print them in the final
# banner. The curl-piped UX scrolls the setup-wip.sh output off the top of the
# terminal, so this is often the only place the user sees the credentials.
ADMIN_PASS=$(grep '^WIP_DEX_ADMIN_PASS=' "${INSTALL_DIR}/.env" | cut -d= -f2-)
EDITOR_PASS=$(grep '^WIP_DEX_EDITOR_PASS=' "${INSTALL_DIR}/.env" | cut -d= -f2-)
VIEWER_PASS=$(grep '^WIP_DEX_VIEWER_PASS=' "${INSTALL_DIR}/.env" | cut -d= -f2-)

cat <<DONE
==========================================
  ${BOLD}WIP is (probably) up${NC}

  Main console:       https://${HOSTNAME_ARG}:8443
  React Console:      https://${HOSTNAME_ARG}:8443/apps/rc/
  D&D Compendium:     https://${HOSTNAME_ARG}:8443/apps/dnd/
  ClinTrial Explorer: https://${HOSTNAME_ARG}:8443/apps/clintrial/

  Login credentials (also in ${INSTALL_DIR}/.env):

    admin@wip.local    ${ADMIN_PASS}    (admin access)
    editor@wip.local   ${EDITOR_PASS}    (write access)
    viewer@wip.local   ${VIEWER_PASS}    (read access)

  Notes:
    - Your browser will show a cert warning (Caddy self-signed). Click through.
    - To check container health: podman ps
    - To stop:                    cd ${INSTALL_DIR} && podman-compose down
    - To see logs:                cd ${INSTALL_DIR} && podman-compose logs -f
==========================================
DONE
