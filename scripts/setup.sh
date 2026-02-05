#!/bin/bash
# WIP Unified Setup Script
#
# Modular deployment system with presets and composable modules.
#
# Presets (sensible defaults):
#   core      - Minimal: MongoDB, NATS, API-key auth only
#   standard  - Most common: + OIDC authentication
#   analytics - With reporting: + PostgreSQL, Reporting-Sync
#   full      - Everything: + MinIO, Ingest-Gateway
#
# Modules (composable):
#   oidc      - Dex + Caddy for user authentication
#   reporting - PostgreSQL + Reporting-Sync for SQL analytics
#   files     - MinIO for binary file storage
#   ingest    - Ingest-Gateway for streaming ingestion
#   dev-tools - Mongo Express (dev variant only)
#
# Usage:
#   ./scripts/setup.sh --preset standard --hostname wip.local
#   ./scripts/setup.sh --preset core --localhost
#   ./scripts/setup.sh --modules oidc,reporting --hostname wip.local
#   ./scripts/setup.sh --preset standard --add files
#   ./scripts/setup.sh --help

set -e

trap 'log_error "Script failed at line $LINENO. Command: $BASH_COMMAND"' ERR

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Timing
START_TIME=$(date +%s)
START_TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
LOG_FILE=""

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; log_to_file "INFO" "$1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; log_to_file "WARN" "$1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; log_to_file "ERROR" "$1"; }
log_step() { echo -e "${BLUE}[STEP]${NC} $1"; log_to_file "STEP" "$1"; }
log_debug() { [ "$DEBUG" = "true" ] && echo -e "${CYAN}[DEBUG]${NC} $1" || true; }

# Log to file with elapsed time
log_to_file() {
    [ -z "$LOG_FILE" ] && return
    local level="$1"
    local message="$2"
    local elapsed=$(($(date +%s) - START_TIME))
    echo "[+${elapsed}s] [$level] $message" >> "$LOG_FILE"
}

log_milestone() {
    local message="$1"
    local elapsed=$(($(date +%s) - START_TIME))
    log_info "$message (+${elapsed}s)"
}

# Script location
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Configuration defaults
PRESET=""
MODULES=""
ADD_MODULES=""
REMOVE_MODULES=""
VARIANT="dev"
NETWORK="remote"
HOSTNAME=""
PLATFORM=""
HTTPS_PORT="8443"
HTTP_PORT="8080"
API_KEY="dev_master_key_for_testing"
DEBUG="false"
LOCALHOST_MODE="false"
SKIP_CONFIRM="false"
CONFIG_FILE=""
SAVE_CONFIG=""

WIP_DATA_DIR="${WIP_DATA_DIR:-$PROJECT_ROOT/data}"

# Available modules
AVAILABLE_MODULES="oidc reporting files ingest dev-tools"

# Module description lookup (bash 3.2 compatible)
get_module_desc() {
    case "$1" in
        oidc)      echo "User authentication via Dex + Caddy (HTTPS)" ;;
        reporting) echo "SQL analytics via PostgreSQL + Reporting-Sync" ;;
        files)     echo "Binary file storage via MinIO (S3-compatible)" ;;
        ingest)    echo "Streaming data ingestion via NATS" ;;
        dev-tools) echo "Database inspection via Mongo Express" ;;
        *)         echo "" ;;
    esac
}

# Preset description lookup (bash 3.2 compatible)
get_preset_desc() {
    case "$1" in
        core)      echo "Minimal deployment - API keys only, no OIDC" ;;
        standard)  echo "Recommended - OIDC authentication for multi-user access" ;;
        analytics) echo "Standard + SQL reporting for BI dashboards" ;;
        full)      echo "All features enabled - complete deployment" ;;
        *)         echo "" ;;
    esac
}

# Detect platform
detect_platform() {
    if [[ "$(uname)" == "Darwin" ]]; then
        echo "default"
    elif [[ -f /proc/device-tree/model ]] && grep -qi "raspberry" /proc/device-tree/model 2>/dev/null; then
        if grep -qi "pi 5" /proc/device-tree/model 2>/dev/null; then
            echo "default"  # Pi 5 supports MongoDB 7
        else
            # Pi 4 or older
            echo "pi4"
        fi
    else
        echo "default"
    fi
}

show_help() {
    cat << EOF
WIP Unified Setup Script - Modular Deployment System

PRESETS (sensible defaults):
  core       Minimal deployment, API-key auth only
  standard   OIDC authentication (recommended for most users)
  analytics  Standard + PostgreSQL reporting
  full       All features enabled

MODULES (composable):
  oidc       User authentication via Dex + Caddy
  reporting  PostgreSQL + Reporting-Sync for SQL analytics
  files      MinIO for binary file attachments
  ingest     Ingest-Gateway for streaming data ingestion
  dev-tools  Mongo Express for database inspection (dev only)

OPTIONS:
  --preset NAME       Use a preset configuration
  --modules LIST      Comma-separated list of modules (instead of preset)
  --add LIST          Add modules to preset
  --remove LIST       Remove modules from preset
  --prod              Production variant (stricter settings)
  --localhost         Local-only access (default: remote/network)
  --hostname NAME     Hostname for network access (required unless --localhost)
  --platform NAME     Platform override: default, pi4 (auto-detected)
  --https-port PORT   HTTPS port (default: 8443)
  --http-port PORT    HTTP port (default: 8080)
  --data-dir DIR      Data storage directory (default: ./data)
  --config FILE       Load configuration from file (for unattended installs)
  --save-config FILE  Save configuration to file and exit (don't install)
  -y, --yes           Skip confirmation prompt
  --debug             Enable debug output
  --help              Show this help

EXAMPLES:
  # Standard deployment (most common)
  $(basename "$0") --preset standard --hostname wip.local

  # Minimal local development
  $(basename "$0") --preset core --localhost

  # Analytics with SQL reporting
  $(basename "$0") --preset analytics --hostname wip.local

  # Full deployment with everything
  $(basename "$0") --preset full --hostname wip.local

  # Custom module combination
  $(basename "$0") --modules oidc,files --hostname wip.local

  # Preset with additional module
  $(basename "$0") --preset standard --add reporting --hostname wip.local

  # Production deployment
  $(basename "$0") --preset standard --prod --hostname wip.example.com

  # Save configuration for distribution
  $(basename "$0") --preset standard --hostname wip.local --save-config my-setup.conf

  # Load saved configuration (unattended)
  $(basename "$0") --config my-setup.conf -y

EOF
}

# Load configuration from a saved file
load_config() {
    local config_file="$1"

    if [ ! -f "$config_file" ]; then
        log_error "Configuration file not found: $config_file"
        exit 1
    fi

    log_info "Loading configuration from: $config_file"

    # Source the config file (it's just shell variables)
    # shellcheck source=/dev/null
    source "$config_file"

    # Map WIP_ prefixed vars back to script variables if present
    [ -n "${WIP_PRESET:-}" ] && PRESET="$WIP_PRESET"
    [ -n "${WIP_MODULES:-}" ] && MODULES="$WIP_MODULES"
    [ -n "${WIP_ADD_MODULES:-}" ] && ADD_MODULES="$WIP_ADD_MODULES"
    [ -n "${WIP_REMOVE_MODULES:-}" ] && REMOVE_MODULES="$WIP_REMOVE_MODULES"
    [ -n "${WIP_VARIANT:-}" ] && VARIANT="$WIP_VARIANT"
    [ -n "${WIP_HOSTNAME:-}" ] && HOSTNAME="$WIP_HOSTNAME"
    [ -n "${WIP_LOCALHOST_MODE:-}" ] && LOCALHOST_MODE="$WIP_LOCALHOST_MODE"
    [ -n "${WIP_HTTPS_PORT:-}" ] && HTTPS_PORT="$WIP_HTTPS_PORT"
    [ -n "${WIP_HTTP_PORT:-}" ] && HTTP_PORT="$WIP_HTTP_PORT"
    [ -n "${WIP_PLATFORM:-}" ] && PLATFORM="$WIP_PLATFORM"
    [ -n "${WIP_API_KEY:-}" ] && API_KEY="$WIP_API_KEY"
    [ -n "${WIP_DATA_DIR:-}" ] && WIP_DATA_DIR="$WIP_DATA_DIR"

    log_info "Configuration loaded successfully"
}

# Save current configuration to a file
save_config() {
    local config_file="$1"
    local config_dir
    config_dir=$(dirname "$config_file")

    # Create directory if needed
    if [ "$config_dir" != "." ] && [ ! -d "$config_dir" ]; then
        mkdir -p "$config_dir"
    fi

    log_info "Saving configuration to: $config_file"

    cat > "$config_file" << EOF
# WIP Installation Configuration
# Generated: $(date '+%Y-%m-%d %H:%M:%S')
#
# Use with: ./scripts/setup.sh --config $config_file -y
#

# Deployment preset (core, standard, analytics, full)
WIP_PRESET="$PRESET"

# Explicit modules (if not using preset)
WIP_MODULES="$MODULES"

# Module modifications
WIP_ADD_MODULES="$ADD_MODULES"
WIP_REMOVE_MODULES="$REMOVE_MODULES"

# Final active modules (computed)
WIP_ACTIVE_MODULES="$ACTIVE_MODULES"

# Variant: dev or prod
WIP_VARIANT="$VARIANT"

# Network configuration
WIP_HOSTNAME="$HOSTNAME"
WIP_LOCALHOST_MODE="$LOCALHOST_MODE"
WIP_HTTPS_PORT="$HTTPS_PORT"
WIP_HTTP_PORT="$HTTP_PORT"

# Platform (default, pi4)
WIP_PLATFORM="$PLATFORM"

# Data directory
WIP_DATA_DIR="$WIP_DATA_DIR"

# API Key (consider using env var or secrets manager in production)
WIP_API_KEY="$API_KEY"
EOF

    log_info "Configuration saved to: $config_file"
    echo ""
    echo "To deploy using this configuration:"
    echo "  ./scripts/setup.sh --config $config_file -y"
    echo ""
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --preset)
                PRESET="$2"
                shift 2
                ;;
            --modules)
                MODULES="$2"
                shift 2
                ;;
            --add)
                ADD_MODULES="$2"
                shift 2
                ;;
            --remove)
                REMOVE_MODULES="$2"
                shift 2
                ;;
            --prod)
                VARIANT="prod"
                shift
                ;;
            --localhost)
                LOCALHOST_MODE="true"
                NETWORK="localhost"
                shift
                ;;
            --hostname)
                HOSTNAME="$2"
                shift 2
                ;;
            --platform)
                PLATFORM="$2"
                shift 2
                ;;
            --https-port)
                HTTPS_PORT="$2"
                shift 2
                ;;
            --http-port)
                HTTP_PORT="$2"
                shift 2
                ;;
            --data-dir)
                WIP_DATA_DIR="$2"
                shift 2
                ;;
            --config)
                CONFIG_FILE="$2"
                shift 2
                ;;
            --save-config)
                SAVE_CONFIG="$2"
                shift 2
                ;;
            --debug)
                DEBUG="true"
                shift
                ;;
            -y|--yes)
                SKIP_CONFIRM="true"
                shift
                ;;
            --help)
                show_help
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                show_help
                exit 1
                ;;
        esac
    done
}

validate_config() {
    # Must have either preset or modules
    if [ -z "$PRESET" ] && [ -z "$MODULES" ]; then
        log_error "Must specify either --preset or --modules"
        echo "Available presets: core, standard, analytics, full"
        exit 1
    fi

    # Validate preset
    if [ -n "$PRESET" ]; then
        local preset_file="$PROJECT_ROOT/config/presets/${PRESET}.conf"
        if [ ! -f "$preset_file" ]; then
            log_error "Unknown preset: $PRESET"
            echo "Available presets: core, standard, analytics, full"
            exit 1
        fi
    fi

    # Validate modules
    if [ -n "$MODULES" ]; then
        for mod in ${MODULES//,/ }; do
            if [[ ! " $AVAILABLE_MODULES " =~ " $mod " ]]; then
                log_error "Unknown module: $mod"
                echo "Available modules: $AVAILABLE_MODULES"
                exit 1
            fi
        done
    fi

    # Network validation
    if [ "$LOCALHOST_MODE" != "true" ] && [ -z "$HOSTNAME" ]; then
        log_error "Network mode requires --hostname (or use --localhost for local-only)"
        exit 1
    fi

    # Auto-detect platform if not specified
    if [ -z "$PLATFORM" ]; then
        PLATFORM=$(detect_platform)
        log_info "Auto-detected platform: $PLATFORM"
    fi

    # Dev-tools only in dev variant
    if [ "$VARIANT" = "prod" ] && [[ "$MODULES" == *"dev-tools"* || "$ADD_MODULES" == *"dev-tools"* ]]; then
        log_warn "dev-tools module is not available in production variant, removing"
        MODULES="${MODULES//dev-tools/}"
        ADD_MODULES="${ADD_MODULES//dev-tools/}"
    fi
}

load_preset() {
    if [ -n "$PRESET" ]; then
        local preset_file="$PROJECT_ROOT/config/presets/${PRESET}.conf"
        log_step "Loading preset: $PRESET"
        source "$preset_file"
        log_info "  $PRESET_DESCRIPTION"
    fi
}

compute_modules() {
    # Start with preset modules or explicit modules
    local final_modules=""

    if [ -n "$PRESET" ]; then
        final_modules="$MODULES"  # MODULES is set by preset file
    else
        final_modules="$MODULES"  # Use command-line modules
    fi

    # Add additional modules
    if [ -n "$ADD_MODULES" ]; then
        for mod in ${ADD_MODULES//,/ }; do
            if [[ ! " $final_modules " =~ " $mod " ]]; then
                final_modules="$final_modules,$mod"
            fi
        done
    fi

    # Remove modules
    if [ -n "$REMOVE_MODULES" ]; then
        for mod in ${REMOVE_MODULES//,/ }; do
            final_modules="${final_modules//$mod/}"
        done
    fi

    # Clean up commas
    final_modules=$(echo "$final_modules" | tr ',' '\n' | grep -v '^$' | sort -u | tr '\n' ',' | sed 's/,$//')

    # Add dev-tools in dev variant
    if [ "$VARIANT" = "dev" ]; then
        if [[ ! " $final_modules " =~ " dev-tools " ]]; then
            if [ -n "$final_modules" ]; then
                final_modules="$final_modules,dev-tools"
            else
                final_modules="dev-tools"
            fi
        fi
    fi

    ACTIVE_MODULES="$final_modules"
    log_info "Active modules: ${ACTIVE_MODULES:-none (base only)}"
}

has_module() {
    [[ ",$ACTIVE_MODULES," == *",$1,"* ]]
}

init_log_file() {
    mkdir -p "$PROJECT_ROOT/logs"
    LOG_FILE="$PROJECT_ROOT/logs/setup-$(date '+%Y%m%d-%H%M%S').log"

    cat > "$LOG_FILE" << EOF
================================================================================
WIP Setup Log
Started: $START_TIMESTAMP
================================================================================

Configuration:
  Preset:   ${PRESET:-custom}
  Modules:  ${ACTIVE_MODULES:-none}
  Variant:  $VARIANT
  Platform: $PLATFORM
  Network:  $NETWORK
  Hostname: ${HOSTNAME:-localhost}
  Data Dir: $WIP_DATA_DIR

================================================================================
Installation Log:
================================================================================
EOF
    log_info "Log file: $LOG_FILE"
}

show_confirmation() {
    if [ "$SKIP_CONFIRM" = "true" ]; then
        log_info "Skipping confirmation (--yes flag)"
        return 0
    fi

    echo ""
    echo -e "${BOLD}╔══════════════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}║                         WIP Deployment Summary                               ║${NC}"
    echo -e "${BOLD}╚══════════════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    # Preset info
    if [ -n "$PRESET" ]; then
        echo -e "  ${BOLD}Preset:${NC}    $PRESET"
        echo -e "             $(get_preset_desc "$PRESET")"
    else
        echo -e "  ${BOLD}Preset:${NC}    custom (module selection)"
    fi
    echo ""

    # Variant and platform
    echo -e "  ${BOLD}Variant:${NC}   $VARIANT"
    echo -e "  ${BOLD}Platform:${NC}  $PLATFORM (MongoDB $([ "$PLATFORM" = "pi4" ] && echo "4.4" || echo "7"))"
    echo ""

    # Network
    if [ "$LOCALHOST_MODE" = "true" ]; then
        echo -e "  ${BOLD}Network:${NC}   localhost only"
    else
        echo -e "  ${BOLD}Network:${NC}   remote (https://${HOSTNAME}:${HTTPS_PORT})"
    fi
    echo ""

    # Core services (always included)
    echo -e "  ${BOLD}Core Services:${NC}"
    echo "    ✓ MongoDB          Document store"
    echo "    ✓ NATS             Message queue for events"
    echo "    ✓ Registry         ID management"
    echo "    ✓ Def-Store        Terminology management"
    echo "    ✓ Template-Store   Schema management"
    echo "    ✓ Document-Store   Document storage + validation"
    echo "    ✓ WIP Console      Admin web interface"
    echo ""

    # Active modules
    if [ -n "$ACTIVE_MODULES" ]; then
        echo -e "  ${BOLD}Active Modules:${NC}"
        for mod in ${ACTIVE_MODULES//,/ }; do
            echo -e "    ${GREEN}✓${NC} ${mod}$(printf '%*s' $((14 - ${#mod})) '')$(get_module_desc "$mod")"
        done
        echo ""
    fi

    # Inactive modules
    local inactive=""
    for mod in $AVAILABLE_MODULES; do
        if ! has_module "$mod"; then
            inactive="$inactive $mod"
        fi
    done
    if [ -n "$inactive" ]; then
        echo -e "  ${BOLD}Not Included:${NC}"
        for mod in $inactive; do
            echo -e "    ${YELLOW}○${NC} ${mod}$(printf '%*s' $((14 - ${#mod})) '')$(get_module_desc "$mod")"
        done
        echo ""
    fi

    # Data directory
    echo -e "  ${BOLD}Data Dir:${NC}  $WIP_DATA_DIR"
    echo ""

    echo -e "${BOLD}══════════════════════════════════════════════════════════════════════════════${NC}"
    echo ""

    # Confirmation prompt
    read -p "  Proceed with installation? [Y/n] " -n 1 -r response
    echo ""

    # Default to yes if empty (just Enter pressed)
    if [ -z "$response" ] || [[ "$response" =~ ^[Yy]$ ]]; then
        echo ""
        log_info "Installation confirmed by user"
        return 0
    else
        echo ""
        log_info "Installation cancelled by user"
        echo "Installation cancelled."
        exit 0
    fi
}

finalize_log() {
    local end_time=$(date +%s)
    local total_elapsed=$((end_time - START_TIME))
    local end_timestamp=$(date '+%Y-%m-%d %H:%M:%S')

    cat >> "$LOG_FILE" << EOF

================================================================================
Installation Complete
================================================================================
Ended:    $end_timestamp
Duration: ${total_elapsed}s
Status:   SUCCESS
================================================================================
EOF

    echo ""
    log_info "Total installation time: ${total_elapsed}s"
    log_info "Full log saved to: $LOG_FILE"
}

generate_env_file() {
    log_step "Generating .env file..."

    # Determine auth mode
    local auth_mode="api_key_only"
    local oidc_enabled="false"
    if has_module "oidc"; then
        auth_mode="dual"
        oidc_enabled="true"
    fi

    # Determine issuer URL
    local issuer_url=""
    local jwks_uri=""
    if has_module "oidc"; then
        if [ "$LOCALHOST_MODE" = "true" ]; then
            issuer_url="http://localhost:5556/dex"
            jwks_uri="http://wip-dex:5556/dex/keys"
        else
            issuer_url="https://${HOSTNAME}:${HTTPS_PORT}/dex"
            jwks_uri="http://wip-dex:5556/dex/keys"
        fi
    fi

    # MongoDB settings based on platform
    local mongodb_image="docker.io/library/mongo:7"
    local mongodb_healthcheck='echo '"'"'db.runCommand("ping").ok'"'"' | mongosh localhost:27017/test --quiet'
    if [ "$PLATFORM" = "pi4" ]; then
        mongodb_image="docker.io/library/mongo:4.4.18"
        mongodb_healthcheck='echo '"'"'db.runCommand("ping").ok'"'"' | mongo localhost:27017/test --quiet'
    fi

    # File storage settings
    local file_storage_enabled="false"
    if has_module "files"; then
        file_storage_enabled="true"
    fi

    cat > "$PROJECT_ROOT/.env" << EOF
# WIP Environment Configuration
# Generated by setup.sh - $(date)
# Preset: ${PRESET:-custom} | Modules: ${ACTIVE_MODULES:-none} | Variant: $VARIANT

# =============================================================================
# DEPLOYMENT SETTINGS
# =============================================================================
WIP_PRESET=${PRESET:-custom}
WIP_MODULES=$ACTIVE_MODULES
WIP_VARIANT=$VARIANT
WIP_PLATFORM=$PLATFORM
WIP_NETWORK_MODE=$NETWORK
WIP_HOSTNAME=${HOSTNAME:-localhost}

# =============================================================================
# DATA STORAGE
# =============================================================================
WIP_DATA_DIR=$WIP_DATA_DIR

# =============================================================================
# MONGODB
# =============================================================================
WIP_MONGODB_IMAGE=$mongodb_image
WIP_MONGODB_HEALTHCHECK=$mongodb_healthcheck

# =============================================================================
# AUTHENTICATION
# =============================================================================
WIP_AUTH_MODE=$auth_mode
WIP_AUTH_LEGACY_API_KEY=$API_KEY
API_KEY=$API_KEY
MASTER_API_KEY=$API_KEY

# OIDC Settings
WIP_AUTH_JWT_ISSUER_URL=$issuer_url
WIP_AUTH_JWT_JWKS_URI=$jwks_uri
WIP_AUTH_JWT_AUDIENCE=wip-console

# =============================================================================
# CONSOLE SETTINGS
# =============================================================================
VITE_OIDC_ENABLED=$oidc_enabled
VITE_OIDC_AUTHORITY=$issuer_url
VITE_OIDC_CLIENT_ID=wip-console
VITE_OIDC_REDIRECT_URI=https://${HOSTNAME:-localhost}:${HTTPS_PORT}/auth/callback
VITE_OIDC_PROVIDER_NAME=Dex
VITE_API_BASE_URL=

# =============================================================================
# FILE STORAGE (MinIO)
# =============================================================================
WIP_FILE_STORAGE_ENABLED=$file_storage_enabled
WIP_FILE_STORAGE_TYPE=minio
WIP_FILE_STORAGE_ENDPOINT=http://wip-minio:9000
WIP_FILE_STORAGE_ACCESS_KEY=wip-minio-root
WIP_FILE_STORAGE_SECRET_KEY=wip-minio-password
WIP_FILE_STORAGE_BUCKET=wip-attachments

# =============================================================================
# SERVICE URLs (for inter-service communication)
# =============================================================================
REGISTRY_URL=http://wip-registry-dev:8001
DEF_STORE_URL=http://wip-def-store-dev:8002
TEMPLATE_STORE_URL=http://wip-template-store-dev:8003
DOCUMENT_STORE_URL=http://wip-document-store-dev:8004
NATS_URL=nats://wip-nats:4222

# =============================================================================
# HEALTH CHECK SETTINGS
# =============================================================================
WIP_HEALTH_CHECK_INTERVAL=10
WIP_HEALTH_CHECK_TIMEOUT=60
EOF

    log_info "Generated .env file"
}

generate_dex_config() {
    if ! has_module "oidc"; then
        log_debug "Skipping Dex config (oidc module not active)"
        return
    fi

    log_step "Generating Dex configuration..."
    mkdir -p "$PROJECT_ROOT/config/dex"

    local issuer_url=""
    if [ "$LOCALHOST_MODE" = "true" ]; then
        issuer_url="http://localhost:5556/dex"
    else
        issuer_url="https://${HOSTNAME}:${HTTPS_PORT}/dex"
    fi

    # Build allowed origins
    local origins="    - https://${HOSTNAME:-localhost}:${HTTPS_PORT}"
    if [ "$LOCALHOST_MODE" != "true" ]; then
        origins="$origins
    - https://localhost:${HTTPS_PORT}"
    fi

    # Build redirect URIs
    local redirect_uris="    - https://${HOSTNAME:-localhost}:${HTTPS_PORT}/auth/callback"
    if [ "$LOCALHOST_MODE" != "true" ]; then
        redirect_uris="$redirect_uris
    - https://localhost:${HTTPS_PORT}/auth/callback"
    fi

    cat > "$PROJECT_ROOT/config/dex/config.yaml" << EOF
# Dex Configuration
# Generated by setup.sh - $(date)

issuer: $issuer_url

storage:
  type: sqlite3
  config:
    file: /data/dex.db

web:
  http: 0.0.0.0:5556
  allowedOrigins:
$origins

oauth2:
  skipApprovalScreen: true
  passwordConnector: local

staticClients:
  - id: wip-console
    name: WIP Console
    secret: wip-console-secret
    redirectURIs:
$redirect_uris

connectors: []

enablePasswordDB: true

staticPasswords:
  - email: admin@wip.local
    hash: \$2a\$10\$2b2cU8CPhOTaGrs1HRQuAueS7JTT5ZHsHSzYiFPm1leZck7Mc8T4W
    username: admin
    userID: admin-001
  - email: editor@wip.local
    hash: \$2a\$10\$sxn7dBHneLwumGPUqs3GR.tXMYxRc1Go2RJVhkeHDLSw.dqPvexEq
    username: editor
    userID: editor-001
  - email: viewer@wip.local
    hash: \$2a\$10\$pLjmKbsBceJbQzPjJ5OmbuGSzE8A.FH5T3sLdAJ6H/Ya3gens0vTu
    username: viewer
    userID: viewer-001
EOF

    log_info "Generated Dex config"
}

generate_caddy_config() {
    if ! has_module "oidc"; then
        log_debug "Skipping Caddy config (oidc module not active)"
        return
    fi

    log_step "Generating Caddy configuration..."
    mkdir -p "$PROJECT_ROOT/config/caddy"

    # Caddy listens on standard ports inside container (443 for HTTPS)
    # Port mapping in docker-compose exposes 443 as HTTPS_PORT externally
    local host_patterns=""
    if [ "$LOCALHOST_MODE" = "true" ]; then
        host_patterns="localhost"
    else
        host_patterns="${HOSTNAME}, localhost"
    fi

    cat > "$PROJECT_ROOT/config/caddy/Caddyfile" << EOF
# Caddy Configuration
# Generated by setup.sh - $(date)
#
# Provides HTTPS termination and reverse proxy for WIP services.
# Uses self-signed certificate for local network access.

{
    auto_https disable_redirects
}

$host_patterns {
    tls internal

    # Dex OIDC provider (keep /dex prefix - Dex expects it)
    handle /dex/* {
        reverse_proxy wip-dex:5556
    }

    # API services
    handle_path /api/registry/* {
        reverse_proxy wip-registry-dev:8001
    }

    handle_path /api/def-store/* {
        reverse_proxy wip-def-store-dev:8002
    }

    handle_path /api/template-store/* {
        reverse_proxy wip-template-store-dev:8003
    }

    handle_path /api/document-store/* {
        reverse_proxy wip-document-store-dev:8004
    }

    handle_path /api/reporting-sync/* {
        reverse_proxy wip-reporting-sync-dev:8005
    }

    handle_path /api/ingest-gateway/* {
        reverse_proxy wip-ingest-gateway-dev:8006
    }

    # WIP Console (default)
    handle {
        reverse_proxy wip-console-dev:3000
    }
}
EOF

    log_info "Generated Caddy config"
}

check_dependencies() {
    log_step "Checking dependencies..."

    local missing=()

    if ! command -v podman &> /dev/null; then
        missing+=("podman")
    else
        echo "  podman: $(podman --version | head -1)"
    fi

    if ! command -v podman-compose &> /dev/null; then
        missing+=("podman-compose")
    else
        echo "  podman-compose: installed"
    fi

    if [ ${#missing[@]} -gt 0 ]; then
        log_error "Missing dependencies: ${missing[*]}"
        exit 1
    fi
}

ensure_data_dirs() {
    log_step "Ensuring data directories..."
    mkdir -p "$WIP_DATA_DIR"/{mongodb,nats,dex,caddy/data,caddy/config}

    if has_module "reporting"; then
        mkdir -p "$WIP_DATA_DIR/postgres"
    fi

    if has_module "files"; then
        mkdir -p "$WIP_DATA_DIR/minio"
    fi

    log_info "Data directory: $WIP_DATA_DIR"
}

ensure_network() {
    log_step "Ensuring Docker network..."
    if ! podman network exists wip-network 2>/dev/null; then
        podman network create wip-network
        log_info "Created wip-network"
    else
        log_info "Network wip-network already exists"
    fi
}

start_infrastructure() {
    log_step "Starting infrastructure..."

    cd "$PROJECT_ROOT"

    # Build compose command with base + modules
    local compose_files="-f docker-compose/base.yml"

    # Add platform overlay
    if [ -f "docker-compose/platforms/${PLATFORM}.yml" ]; then
        compose_files="$compose_files -f docker-compose/platforms/${PLATFORM}.yml"
    fi

    # Add module overlays
    for mod in ${ACTIVE_MODULES//,/ }; do
        local mod_file="docker-compose/modules/${mod}.yml"
        if [ -f "$mod_file" ]; then
            compose_files="$compose_files -f $mod_file"
        fi
    done

    log_debug "Compose files: $compose_files"

    # Start infrastructure
    podman-compose --env-file .env $compose_files up -d

    # Wait for MongoDB
    log_info "Waiting for MongoDB..."
    local retries=30
    while [ $retries -gt 0 ]; do
        if podman exec wip-mongodb mongosh --eval "db.runCommand('ping')" &>/dev/null || \
           podman exec wip-mongodb mongo --eval "db.runCommand('ping')" &>/dev/null; then
            log_milestone "MongoDB ready"
            break
        fi
        sleep 2
        retries=$((retries - 1))
    done

    if [ $retries -eq 0 ]; then
        log_error "MongoDB failed to start"
        exit 1
    fi

    # Wait for NATS
    log_info "Waiting for NATS..."
    sleep 3
    if curl -s http://localhost:8222/varz &>/dev/null; then
        log_milestone "NATS ready"
    else
        log_warn "NATS monitoring not responding (may still be starting)"
    fi

    # Wait for PostgreSQL if enabled
    if has_module "reporting"; then
        log_info "Waiting for PostgreSQL..."
        retries=30
        while [ $retries -gt 0 ]; do
            if podman exec wip-postgres pg_isready -U wip &>/dev/null; then
                log_milestone "PostgreSQL ready"
                break
            fi
            sleep 2
            retries=$((retries - 1))
        done
    fi

    # Wait for Dex if enabled
    if has_module "oidc"; then
        log_info "Waiting for Dex..."
        retries=15
        while [ $retries -gt 0 ]; do
            if curl -s http://localhost:5556/dex/healthz &>/dev/null; then
                log_milestone "Dex ready"
                break
            fi
            sleep 2
            retries=$((retries - 1))
        done
    fi

    # Wait for MinIO if enabled
    if has_module "files"; then
        log_info "Waiting for MinIO..."
        retries=15
        while [ $retries -gt 0 ]; do
            if curl -s http://localhost:9000/minio/health/ready &>/dev/null; then
                log_milestone "MinIO ready"
                break
            fi
            sleep 2
            retries=$((retries - 1))
        done
    fi

    # Wait for Mongo Express if enabled
    if has_module "dev-tools"; then
        log_info "Waiting for Mongo Express..."
        sleep 5
        log_milestone "Mongo Express started"
    fi
}

start_service() {
    local name=$1
    local dir=$2
    local port=$3

    log_info "Starting $name..."
    cd "$PROJECT_ROOT/components/$dir"
    podman-compose --env-file "$PROJECT_ROOT/.env" -f docker-compose.dev.yml up -d

    # Wait for health
    local retries=30
    while [ $retries -gt 0 ]; do
        if curl -s "http://localhost:$port/health" 2>/dev/null | grep -q "healthy"; then
            log_milestone "$name ready (port $port)"
            return 0
        fi
        sleep 2
        retries=$((retries - 1))
    done
    log_warn "$name may still be starting"
}

start_services() {
    log_step "Starting Registry and initializing namespaces..."

    cd "$PROJECT_ROOT/components/registry"
    podman-compose --env-file "$PROJECT_ROOT/.env" -f docker-compose.dev.yml up -d

    # Wait for Registry
    local retries=30
    while [ $retries -gt 0 ]; do
        if curl -s http://localhost:8001/health 2>/dev/null | grep -q "healthy"; then
            log_info "  Registry ready"
            break
        fi
        sleep 2
        retries=$((retries - 1))
    done

    # Initialize namespaces
    log_info "Initializing WIP namespaces..."
    local init_result=$(curl -s -X POST http://localhost:8001/api/registry/namespaces/initialize-wip \
        -H "X-API-Key: $API_KEY" 2>/dev/null || echo "failed")

    if echo "$init_result" | grep -q "created\|exists"; then
        log_info "  Namespaces initialized"
    else
        log_warn "  Namespace initialization response: $init_result"
    fi
    echo ""

    log_step "Starting application services..."
    start_service "Def-Store" "def-store" "8002"
    start_service "Template-Store" "template-store" "8003"
    start_service "Document-Store" "document-store" "8004"

    # Conditional services
    if has_module "reporting"; then
        start_service "Reporting-Sync" "reporting-sync" "8005"
    fi

    if has_module "ingest"; then
        start_service "Ingest-Gateway" "ingest-gateway" "8006"
    fi
    echo ""

    log_step "Starting WIP Console..."
    cd "$PROJECT_ROOT/ui/wip-console"
    podman-compose --env-file "$PROJECT_ROOT/.env" -f docker-compose.dev.yml up -d
    log_info "Waiting for Console to start..."
    sleep 8
}

print_status() {
    echo ""
    log_step "Deployment complete!"
    echo ""
    echo "=========================================="
    echo "  Configuration"
    echo "=========================================="
    echo "  Preset:   ${PRESET:-custom}"
    echo "  Modules:  ${ACTIVE_MODULES:-none}"
    echo "  Variant:  $VARIANT"
    echo "  Platform: $PLATFORM"
    echo "  Network:  $NETWORK"
    [ -n "$HOSTNAME" ] && echo "  Hostname: $HOSTNAME"
    echo ""
    echo "=========================================="
    echo "  Access URLs"
    echo "=========================================="

    if has_module "oidc"; then
        if [ "$LOCALHOST_MODE" = "true" ]; then
            echo "  Console:  https://localhost:${HTTPS_PORT}"
        else
            echo "  Console:  https://${HOSTNAME}:${HTTPS_PORT}"
            echo "            https://localhost:${HTTPS_PORT} (local)"
        fi
        echo ""
        echo "  Login:    admin@wip.local / admin123"
        echo "            editor@wip.local / editor123"
        echo "            viewer@wip.local / viewer123"
    else
        echo "  Console:  http://localhost:3000"
        echo ""
        echo "  Auth:     API Key only"
        echo "  API Key:  $API_KEY"
    fi

    echo ""
    echo "=========================================="
    echo "  Service Ports"
    echo "=========================================="
    echo "  Registry:       http://localhost:8001"
    echo "  Def-Store:      http://localhost:8002"
    echo "  Template-Store: http://localhost:8003"
    echo "  Document-Store: http://localhost:8004"

    if has_module "reporting"; then
        echo "  Reporting-Sync: http://localhost:8005"
        echo "  PostgreSQL:     localhost:5432"
    fi

    if has_module "ingest"; then
        echo "  Ingest-Gateway: http://localhost:8006"
    fi

    if has_module "dev-tools"; then
        echo "  Mongo Express:  http://localhost:8081 (admin/admin)"
    fi

    if has_module "files"; then
        echo "  MinIO Console:  http://localhost:9001"
    fi

    echo "  NATS Monitor:   http://localhost:8222"
    echo ""
    echo "=========================================="
    echo "  Files"
    echo "=========================================="
    echo "  Config saved:   config/last-install.conf"
    [ -n "$LOG_FILE" ] && echo "  Install log:    ${LOG_FILE#$PROJECT_ROOT/}"
    echo ""
    echo "  Re-run this installation:"
    echo "    ./scripts/setup.sh --config config/last-install.conf -y"
    echo ""
    echo "=========================================="
    echo ""
}

# Main execution
main() {
    echo ""
    echo -e "${BOLD}=======================================${NC}"
    echo -e "${BOLD}  WIP Setup Script${NC}"
    echo -e "${BOLD}=======================================${NC}"
    echo ""

    parse_args "$@"

    # Load config file if specified (command-line args take precedence)
    if [ -n "$CONFIG_FILE" ]; then
        load_config "$CONFIG_FILE"
    fi

    validate_config
    load_preset
    compute_modules

    # If --save-config was specified, save and exit without installing
    if [ -n "$SAVE_CONFIG" ]; then
        save_config "$SAVE_CONFIG"
        exit 0
    fi

    # Show summary and get confirmation
    show_confirmation

    # Save configuration for future reference
    mkdir -p "$PROJECT_ROOT/config"
    save_config "$PROJECT_ROOT/config/last-install.conf" >/dev/null 2>&1 || true

    # Initialize log file after confirmation
    init_log_file

    check_dependencies
    ensure_data_dirs
    ensure_network

    generate_env_file
    generate_dex_config
    generate_caddy_config

    start_infrastructure
    start_services

    print_status
    finalize_log
}

main "$@"
