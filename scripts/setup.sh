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
CMDLINE_MODULES=""  # Preserve command-line --modules separately
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
CLEAN_DATA="false"

WIP_DATA_DIR="${WIP_DATA_DIR:-$PROJECT_ROOT/data}"

# Secret generation (populated when --prod or --generate-secrets)
GENERATE_SECRETS="false"
ADMIN_EMAIL=""
ACME_STAGING="false"
WIP_API_KEY=""
WIP_POSTGRES_PASSWORD=""
WIP_MINIO_PASSWORD=""
WIP_MONGO_USER=""
WIP_MONGO_PASSWORD=""
WIP_DEX_CLIENT_SECRET=""
WIP_NATS_TOKEN=""

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

# Generate a random hex string
generate_secret() {
    local length=${1:-32}
    openssl rand -hex "$length"
}

# Generate all production secrets
generate_prod_secrets() {
    log_step "Generating production secrets..."

    WIP_API_KEY=$(generate_secret 32)
    WIP_POSTGRES_PASSWORD=$(generate_secret 24)
    WIP_MINIO_PASSWORD=$(generate_secret 24)
    WIP_MONGO_USER="wip_admin"
    WIP_MONGO_PASSWORD=$(generate_secret 24)
    WIP_DEX_CLIENT_SECRET=$(generate_secret 32)
    WIP_NATS_TOKEN=$(generate_secret 32)

    log_info "Generated 6 secrets (API key, Postgres, MinIO, MongoDB, Dex, NATS)"
}

# Save secrets to files with restrictive permissions
save_secrets() {
    local secrets_dir="$WIP_DATA_DIR/secrets"

    log_step "Saving secrets to $secrets_dir..."
    mkdir -p "$secrets_dir"
    chmod 700 "$secrets_dir"

    # Write individual secret files
    echo -n "$WIP_API_KEY" > "$secrets_dir/api_key"
    echo -n "$WIP_POSTGRES_PASSWORD" > "$secrets_dir/postgres_password"
    echo -n "$WIP_MINIO_PASSWORD" > "$secrets_dir/minio_password"
    echo -n "$WIP_MONGO_PASSWORD" > "$secrets_dir/mongo_password"
    echo -n "$WIP_DEX_CLIENT_SECRET" > "$secrets_dir/dex_client_secret"
    echo -n "$WIP_NATS_TOKEN" > "$secrets_dir/nats_token"

    # Set restrictive permissions
    chmod 600 "$secrets_dir"/*

    # Create human-readable summary (for initial viewing only)
    cat > "$secrets_dir/credentials.txt" << EOF
# WIP Production Credentials
# Generated: $(date '+%Y-%m-%d %H:%M:%S')
#
# IMPORTANT: This file contains sensitive secrets.
# - Store securely or delete after saving to a password manager
# - Never commit to version control
# - Consider encrypting at rest (see docs/security/encryption-at-rest.md)

API Key (X-API-Key header):
  $WIP_API_KEY

MongoDB:
  Username: $WIP_MONGO_USER
  Password: $WIP_MONGO_PASSWORD
  URI: mongodb://$WIP_MONGO_USER:$WIP_MONGO_PASSWORD@wip-mongodb:27017/

PostgreSQL:
  Username: wip
  Password: $WIP_POSTGRES_PASSWORD

MinIO (S3):
  Access Key: wip-minio-root
  Secret Key: $WIP_MINIO_PASSWORD

Dex (OIDC):
  Client ID: wip-console
  Client Secret: $WIP_DEX_CLIENT_SECRET

NATS:
  Token: $WIP_NATS_TOKEN
  URL: nats://TOKEN@wip-nats:4222
EOF
    chmod 600 "$secrets_dir/credentials.txt"

    log_info "Secrets saved with 600 permissions"
    log_warn "Review $secrets_dir/credentials.txt and store securely"
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
  --modules LIST      Comma-separated list of modules
                      (with preset: extends preset; without: replaces preset)
  --add LIST          Add modules to preset (same as --modules with preset)
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
  --clean             Clean NATS/JetStream data before starting (fixes stream conflicts)
  --generate-secrets  Generate random production secrets (implied by --prod)
  --email EMAIL       Admin email for Let's Encrypt certificates
  --acme-staging      Use Let's Encrypt staging (for testing certificates)
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

  # Preset with additional modules (two equivalent ways)
  $(basename "$0") --preset standard --add reporting --hostname wip.local
  $(basename "$0") --preset standard --modules reporting --hostname wip.local

  # Minimal preset plus ingest gateway
  $(basename "$0") --preset core --modules ingest --localhost

  # Production deployment
  $(basename "$0") --preset standard --prod --hostname wip.example.com

  # Clean start (fixes NATS stream conflicts)
  $(basename "$0") --preset standard --hostname wip.local --clean

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
                CMDLINE_MODULES="$2"  # Preserve for merging with preset
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
            --clean)
                CLEAN_DATA="true"
                shift
                ;;
            --generate-secrets)
                GENERATE_SECRETS="true"
                shift
                ;;
            --email)
                ADMIN_EMAIL="$2"
                shift 2
                ;;
            --acme-staging)
                ACME_STAGING="true"
                shift
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

    # --prod implies --generate-secrets
    if [ "$VARIANT" = "prod" ]; then
        GENERATE_SECRETS="true"
        log_info "Production mode: will generate random secrets"
    fi

    # --email implies Let's Encrypt (not internal TLS)
    if [ -n "$ADMIN_EMAIL" ]; then
        log_info "Let's Encrypt mode: certificates for $HOSTNAME (email: $ADMIN_EMAIL)"
        if [ "$ACME_STAGING" = "true" ]; then
            log_warn "Using Let's Encrypt staging environment (certificates won't be trusted)"
        fi
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

        # If --modules was also specified, treat it as additional modules
        if [ -n "$CMDLINE_MODULES" ]; then
            log_info "Extending preset '$PRESET' with additional modules: $CMDLINE_MODULES"
            for mod in ${CMDLINE_MODULES//,/ }; do
                if [[ ! ",$final_modules," =~ ",$mod," ]]; then
                    final_modules="$final_modules,$mod"
                fi
            done
        fi
    else
        final_modules="$MODULES"  # Use command-line modules
    fi

    # Add additional modules (via --add)
    if [ -n "$ADD_MODULES" ]; then
        for mod in ${ADD_MODULES//,/ }; do
            if [[ ! ",$final_modules," =~ ",$mod," ]]; then
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
    if [ "$GENERATE_SECRETS" = "true" ]; then
        echo -e "  ${BOLD}Secrets:${NC}   ${GREEN}Random production secrets will be generated${NC}"
    fi
    if [ -n "$ADMIN_EMAIL" ]; then
        echo -e "  ${BOLD}TLS:${NC}       Let's Encrypt (email: $ADMIN_EMAIL)"
    fi
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
    local mongodb_healthcheck="mongosh --quiet --eval 'db.runCommand({ping:1}).ok'"
    if [ "$PLATFORM" = "pi4" ]; then
        mongodb_image="docker.io/library/mongo:4.4.18"
        mongodb_healthcheck="mongo --quiet --eval 'db.runCommand({ping:1}).ok'"
    fi

    # File storage settings
    local file_storage_enabled="false"
    if has_module "files"; then
        file_storage_enabled="true"
    fi

    # Use generated secrets or defaults
    local api_key="${WIP_API_KEY:-$API_KEY}"
    local mongo_user="${WIP_MONGO_USER:-}"
    local mongo_password="${WIP_MONGO_PASSWORD:-}"
    local minio_password="${WIP_MINIO_PASSWORD:-wip-minio-password}"
    local dex_client_secret="${WIP_DEX_CLIENT_SECRET:-wip-console-secret}"
    local nats_token="${WIP_NATS_TOKEN:-}"
    local postgres_password="${WIP_POSTGRES_PASSWORD:-wip}"

    # Build MongoDB URI
    local mongo_uri="mongodb://wip-mongodb:27017/"
    if [ -n "$mongo_user" ] && [ -n "$mongo_password" ]; then
        mongo_uri="mongodb://${mongo_user}:${mongo_password}@wip-mongodb:27017/"
    fi

    # Build NATS URL
    local nats_url="nats://wip-nats:4222"
    if [ -n "$nats_token" ]; then
        nats_url="nats://${nats_token}@wip-nats:4222"
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
WIP_PROJECT_ROOT=$PROJECT_ROOT
WIP_DATA_DIR=$WIP_DATA_DIR

# =============================================================================
# MONGODB
# =============================================================================
WIP_MONGODB_IMAGE=$mongodb_image
WIP_MONGODB_HEALTHCHECK=$mongodb_healthcheck
WIP_MONGO_USER=$mongo_user
WIP_MONGO_PASSWORD=$mongo_password
WIP_MONGO_URI=$mongo_uri

# =============================================================================
# AUTHENTICATION
# =============================================================================
WIP_AUTH_MODE=$auth_mode
WIP_AUTH_LEGACY_API_KEY=$api_key
API_KEY=$api_key
MASTER_API_KEY=$api_key

# OIDC Settings
WIP_AUTH_JWT_ISSUER_URL=$issuer_url
WIP_AUTH_JWT_JWKS_URI=$jwks_uri
WIP_AUTH_JWT_AUDIENCE=wip-console
WIP_DEX_CLIENT_SECRET=$dex_client_secret

# =============================================================================
# CONSOLE SETTINGS
# =============================================================================
VITE_OIDC_ENABLED=$oidc_enabled
VITE_OIDC_AUTHORITY=$issuer_url
VITE_OIDC_CLIENT_ID=wip-console
VITE_OIDC_REDIRECT_URI=https://${HOSTNAME:-localhost}:${HTTPS_PORT}/auth/callback
VITE_OIDC_PROVIDER_NAME=Dex
VITE_API_BASE_URL=

# Optional module feature flags (must match WIP_MODULES)
VITE_REPORTING_ENABLED=$(has_module "reporting" && echo "true" || echo "false")
VITE_FILES_ENABLED=$(has_module "files" && echo "true" || echo "false")
VITE_INGEST_ENABLED=$(has_module "ingest" && echo "true" || echo "false")

# =============================================================================
# FILE STORAGE (MinIO)
# =============================================================================
WIP_FILE_STORAGE_ENABLED=$file_storage_enabled
WIP_FILE_STORAGE_TYPE=minio
WIP_FILE_STORAGE_ENDPOINT=http://wip-minio:9000
WIP_FILE_STORAGE_ACCESS_KEY=wip-minio-root
WIP_FILE_STORAGE_SECRET_KEY=$minio_password
WIP_FILE_STORAGE_BUCKET=wip-attachments

# =============================================================================
# POSTGRESQL
# =============================================================================
WIP_POSTGRES_PASSWORD=$postgres_password

# =============================================================================
# NATS
# =============================================================================
WIP_NATS_TOKEN=$nats_token
NATS_URL=$nats_url

# =============================================================================
# SERVICE URLs (for inter-service communication)
# =============================================================================
REGISTRY_URL=http://wip-registry-dev:8001
DEF_STORE_URL=http://wip-def-store-dev:8002
TEMPLATE_STORE_URL=http://wip-template-store-dev:8003
DOCUMENT_STORE_URL=http://wip-document-store-dev:8004

# =============================================================================
# HEALTH CHECK SETTINGS
# =============================================================================
WIP_HEALTH_CHECK_INTERVAL=10
WIP_HEALTH_CHECK_TIMEOUT=60

# =============================================================================
# TLS / ACME SETTINGS
# =============================================================================
WIP_ADMIN_EMAIL=$ADMIN_EMAIL
WIP_ACME_STAGING=$ACME_STAGING
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

    # Generate bcrypt password hashes dynamically
    # This ensures hashes always match the documented passwords
    local admin_hash editor_hash viewer_hash
    if command -v htpasswd &> /dev/null; then
        log_info "Generating password hashes..."
        # htpasswd -nbBC 10 generates bcrypt hash, we extract just the hash part
        # sed converts $2y (htpasswd) to $2a (Dex expects $2a)
        admin_hash=$(htpasswd -nbBC 10 "" "admin123" 2>/dev/null | tr -d ':\n' | sed 's/\$2y/\$2a/')
        editor_hash=$(htpasswd -nbBC 10 "" "editor123" 2>/dev/null | tr -d ':\n' | sed 's/\$2y/\$2a/')
        viewer_hash=$(htpasswd -nbBC 10 "" "viewer123" 2>/dev/null | tr -d ':\n' | sed 's/\$2y/\$2a/')
    else
        log_warn "htpasswd not found - using pre-generated hashes"
        log_warn "Install apache2-utils (Linux) or run 'brew install httpd' (Mac) for dynamic hash generation"
        # Fallback hashes - verified correct for these passwords
        # Generated with: htpasswd -nbBC 10 "" "password" | sed 's/$2y/$2a/'
        admin_hash='$2a$10$8lJl/57PSwRj/6tDGsrUzOJZEIliaG4HJlL66q.mIfJjNzHLI6qJe'
        editor_hash='$2a$10$EAOJokg1r0OmVhltE4gNtu2F/fRr0DePUOrBRdp01kR0qiwjNtwcm'
        viewer_hash='$2a$10$2VIGAKxj5VFmlIxqLnfAkOfIKudQks/3BDy4QaJ1k94qW6eFhYfGC'
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
    hash: $admin_hash
    username: admin
    userID: admin-001
  - email: editor@wip.local
    hash: $editor_hash
    username: editor
    userID: editor-001
  - email: viewer@wip.local
    hash: $viewer_hash
    username: viewer
    userID: viewer-001
EOF

    log_info "Generated Dex config"
    log_info "Test users: admin@wip.local/admin123, editor@wip.local/editor123, viewer@wip.local/viewer123"
}

generate_console_nginx_config() {
    log_step "Generating Console nginx configuration..."
    mkdir -p "$PROJECT_ROOT/config/console"

    # Base nginx config
    cat > "$PROJECT_ROOT/config/console/nginx.conf" << 'EOF'
server {
    listen 80;
    server_name localhost;
    root /usr/share/nginx/html;
    index index.html;

    # Gzip compression
    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml application/xml+rss text/javascript;

    # Handle SPA routing - serve index.html for all routes
    location / {
        try_files $uri $uri/ /index.html;
    }

    # Proxy API requests to Def-Store backend
    location /api/def-store/ {
        proxy_pass http://wip-def-store:8002/api/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Proxy API requests to Template-Store backend
    location /api/template-store/ {
        proxy_pass http://wip-template-store:8003/api/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Proxy API requests to Document-Store backend
    location /api/document-store/ {
        proxy_pass http://wip-document-store:8004/api/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
EOF

    # Add Dex proxy only if OIDC is enabled
    if has_module "oidc"; then
        cat >> "$PROJECT_ROOT/config/console/nginx.conf" << 'EOF'

    # Proxy Dex OIDC requests to avoid CORS issues
    location /dex/ {
        proxy_pass http://wip-dex:5556/dex/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
EOF
    fi

    # Close config with cache settings
    cat >> "$PROJECT_ROOT/config/console/nginx.conf" << 'EOF'

    # Cache static assets
    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
EOF

    log_info "Generated console nginx config (OIDC: $(has_module oidc && echo 'enabled' || echo 'disabled'))"
}

generate_caddy_config() {
    if ! has_module "oidc"; then
        log_debug "Skipping Caddy config (oidc module not active)"
        return
    fi

    log_step "Generating Caddy configuration..."
    mkdir -p "$PROJECT_ROOT/config/caddy"

    # Service name suffix based on variant (dev containers have -dev suffix)
    local svc_suffix=""
    [ "$VARIANT" = "dev" ] && svc_suffix="-dev"

    # Console port: dev uses 3000 (Vite), prod uses 80 (nginx)
    local console_port=80
    [ "$VARIANT" = "dev" ] && console_port=3000

    # Caddy listens on standard ports inside container (443 for HTTPS)
    # Port mapping in docker-compose exposes 443 as HTTPS_PORT externally
    local host_patterns=""
    if [ "$LOCALHOST_MODE" = "true" ]; then
        host_patterns="localhost"
    else
        host_patterns="${HOSTNAME}, localhost"
    fi

    # Determine TLS mode
    local tls_config="tls internal"
    local global_options="{
    auto_https disable_redirects
}"
    if [ -n "$ADMIN_EMAIL" ]; then
        # Use Let's Encrypt
        tls_config=""  # Let Caddy auto-obtain certificate
        if [ "$ACME_STAGING" = "true" ]; then
            global_options="{
    email $ADMIN_EMAIL
    acme_ca https://acme-staging-v02.api.letsencrypt.org/directory
}"
        else
            global_options="{
    email $ADMIN_EMAIL
}"
        fi
        log_info "TLS: Let's Encrypt (email: $ADMIN_EMAIL)"
    else
        log_info "TLS: Self-signed internal certificates"
    fi

    # Build security headers
    local security_headers=""
    if [ "$VARIANT" = "prod" ]; then
        security_headers="
    # Security headers (production)
    header {
        Strict-Transport-Security \"max-age=31536000; includeSubDomains\"
        X-Content-Type-Options \"nosniff\"
        X-Frame-Options \"SAMEORIGIN\"
        Referrer-Policy \"strict-origin-when-cross-origin\"
    }"
    fi

    cat > "$PROJECT_ROOT/config/caddy/Caddyfile" << EOF
# Caddy Configuration
# Generated by setup.sh - $(date)
#
# Provides HTTPS termination and reverse proxy for WIP services.
$([ -n "$ADMIN_EMAIL" ] && echo "# TLS: Let's Encrypt" || echo "# TLS: Self-signed internal certificates")

$global_options

$host_patterns {
    $tls_config
$security_headers

    # Dex OIDC provider (keep /dex prefix - Dex expects it)
    handle /dex/* {
        reverse_proxy wip-dex:5556
    }

    # API services (use handle, not handle_path - services expect full path)
    handle /api/registry/* {
        reverse_proxy wip-registry${svc_suffix}:8001
    }

    handle /api/def-store/* {
        reverse_proxy wip-def-store${svc_suffix}:8002
    }

    handle /api/template-store/* {
        reverse_proxy wip-template-store${svc_suffix}:8003
    }

    handle /api/document-store/* {
        reverse_proxy wip-document-store${svc_suffix}:8004
    }

    handle /api/reporting-sync/* {
        reverse_proxy wip-reporting-sync${svc_suffix}:8005
    }

    handle /api/ingest-gateway/* {
        reverse_proxy wip-ingest-gateway${svc_suffix}:8006
    }

    # WIP Console (default)
    # Dev mode uses port 3000 (Vite), prod mode uses port 80 (nginx)
    handle {
        reverse_proxy wip-console${svc_suffix}:${console_port}
    }
}
EOF

    log_info "Generated Caddy config"
}

generate_nats_config() {
    # Only generate NATS config with auth when token is set
    if [ -z "$WIP_NATS_TOKEN" ]; then
        log_debug "Skipping NATS config (no token - using default config)"
        return
    fi

    log_step "Generating NATS configuration with token auth..."
    mkdir -p "$PROJECT_ROOT/config/nats"

    cat > "$PROJECT_ROOT/config/nats/nats.conf" << EOF
# NATS Configuration
# Generated by setup.sh - $(date)
#
# Token-based authorization enabled for production mode.

port: 4222
http_port: 8222

jetstream {
    store_dir: /data
}

authorization {
    token: "$WIP_NATS_TOKEN"
}
EOF

    log_info "Generated NATS config with token authentication"
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

    # Fix directory ownership for rootless Podman on Linux
    # Containers run as specific UIDs; podman unshare sets ownership
    # correctly within the user namespace mapping
    if [[ "$(uname)" != "Darwin" ]]; then
        # MongoDB runs as UID 999 inside container
        log_info "Setting MongoDB directory ownership for rootless Podman..."
        podman unshare chown 999:999 "$WIP_DATA_DIR/mongodb" 2>/dev/null || true

        # Dex runs as UID 1001 inside container
        if has_module "oidc"; then
            log_info "Setting Dex directory ownership for rootless Podman..."
            podman unshare chown 1001:1001 "$WIP_DATA_DIR/dex" 2>/dev/null || true
        fi
    fi

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

ensure_linger() {
    # Enable systemd linger for rootless Podman on Linux
    # Without linger, containers die when user logs out (SSH disconnect, etc.)
    # See: docs/troubleshooting/containers-die-after-logout.md

    if [[ "$(uname)" == "Darwin" ]]; then
        return  # Not needed on macOS
    fi

    local current_user
    current_user=$(whoami)

    if command -v loginctl &>/dev/null; then
        local linger_status
        linger_status=$(loginctl show-user "$current_user" 2>/dev/null | grep -oP 'Linger=\K.*' || echo "unknown")

        if [[ "$linger_status" == "no" ]]; then
            log_warn "Linger not enabled - containers will die on logout!"
            log_info "Enabling linger for user $current_user..."
            if sudo loginctl enable-linger "$current_user"; then
                log_info "Linger enabled - containers will persist across logouts"
            else
                log_warn "Failed to enable linger. Run manually: sudo loginctl enable-linger $current_user"
            fi
        elif [[ "$linger_status" == "yes" ]]; then
            log_info "Linger already enabled for $current_user"
        fi
    fi
}

check_nats_conflicts() {
    # Check for stale NATS JetStream data that could cause stream conflicts
    # The main issue is any stream with a broad wildcard like "wip.>" that
    # overlaps with more specific streams like "wip.ingest.>" or "wip.documents.>"

    local streams_dir="$WIP_DATA_DIR/nats/jetstream/\$G/streams"
    local found_conflict=false
    local conflict_streams=""

    if [ -d "$streams_dir" ]; then
        # Check all streams for wildcard subjects that could conflict
        for stream_dir in "$streams_dir"/*/; do
            [ -d "$stream_dir" ] || continue
            local meta_file="${stream_dir}meta.inf"
            local stream_name=$(basename "$stream_dir")

            if [ -f "$meta_file" ]; then
                # Check for broad wildcards: "wip.>" or "wip.\u003e" (JSON encoded >)
                # These conflict with any more specific wip.* subjects
                if grep -qE '"wip\.(>|\\u003e)"' "$meta_file" 2>/dev/null; then
                    found_conflict=true
                    conflict_streams="$conflict_streams $stream_name(wip.>)"
                fi

                # Also check for "ingest.>" pattern that might conflict
                if grep -qE '"ingest\.(>|\\u003e)"' "$meta_file" 2>/dev/null; then
                    found_conflict=true
                    conflict_streams="$conflict_streams $stream_name(ingest.>)"
                fi
            fi
        done
    fi

    if [ "$found_conflict" = "true" ]; then
        log_warn "Detected NATS JetStream streams with conflicting wildcards!"
        log_warn "Problematic streams:$conflict_streams"

        if [ "$CLEAN_DATA" = "true" ]; then
            log_info "Will clean NATS data (--clean flag set)"
        else
            echo ""
            echo "These streams use broad wildcards that conflict with new streams."
            echo "This causes 'subjects overlap with an existing stream' errors."
            echo ""
            echo "Either:"
            echo "  1. Re-run with --clean flag to clear NATS data"
            echo "  2. Manually remove: rm -rf $WIP_DATA_DIR/nats/jetstream"
            echo ""
            read -p "  Clean NATS data now? [Y/n] " -n 1 -r response
            echo ""
            if [ -z "$response" ] || [[ "$response" =~ ^[Yy]$ ]]; then
                CLEAN_DATA="true"
            else
                log_error "Cannot proceed with conflicting NATS streams. Aborting."
                exit 1
            fi
        fi
    fi
}

clean_nats_data() {
    if [ "$CLEAN_DATA" != "true" ]; then
        return
    fi

    log_step "Cleaning NATS JetStream data (prevents stream conflicts)..."

    # Stop NATS and dependent services if running
    podman stop wip-ingest-gateway-dev wip-reporting-sync-dev wip-nats 2>/dev/null || true

    # Remove JetStream data directory
    if [ -d "$WIP_DATA_DIR/nats/jetstream" ]; then
        rm -rf "$WIP_DATA_DIR/nats/jetstream"
        log_info "Removed stale JetStream data"
    fi

    log_info "NATS data cleaned"
}

verify_compose_files() {
    log_step "Verifying docker-compose files..."

    local missing=()

    # Check base.yml
    if [ ! -f "$PROJECT_ROOT/docker-compose/base.yml" ]; then
        missing+=("docker-compose/base.yml")
    fi

    # Modules with infrastructure overlays (not all modules have them)
    # - oidc: adds Dex + Caddy
    # - reporting: adds PostgreSQL
    # - files: adds MinIO
    # - dev-tools: adds Mongo Express
    # - ingest: NO overlay (service started separately)
    local overlay_modules="oidc reporting files dev-tools"

    # Check module files only for those that have overlays
    for mod in ${ACTIVE_MODULES//,/ }; do
        if [[ " $overlay_modules " =~ " $mod " ]]; then
            local mod_file="$PROJECT_ROOT/docker-compose/modules/${mod}.yml"
            if [ ! -f "$mod_file" ]; then
                missing+=("docker-compose/modules/${mod}.yml")
            fi
        fi
    done

    # Check platform file
    if [ -n "$PLATFORM" ] && [ "$PLATFORM" != "default" ]; then
        local platform_file="$PROJECT_ROOT/docker-compose/platforms/${PLATFORM}.yml"
        if [ ! -f "$platform_file" ]; then
            missing+=("docker-compose/platforms/${PLATFORM}.yml")
        fi
    fi

    if [ ${#missing[@]} -gt 0 ]; then
        log_error "Missing docker-compose files:"
        for f in "${missing[@]}"; do
            echo "  - $f"
        done
        echo ""
        echo "Make sure you have the full repository cloned."
        echo "Required directory structure:"
        echo "  $PROJECT_ROOT/"
        echo "    docker-compose/"
        echo "      base.yml"
        echo "      modules/"
        echo "        oidc.yml, reporting.yml, files.yml, etc."
        echo "      platforms/"
        echo "        pi4.yml, etc."
        exit 1
    fi

    log_info "All required compose files found"
}

start_infrastructure() {
    log_step "Starting infrastructure..."

    # Use absolute paths for all compose files (podman-compose resolves relative to cwd)
    local compose_files="-f $PROJECT_ROOT/docker-compose/base.yml"

    # Add platform overlay
    if [ -f "$PROJECT_ROOT/docker-compose/platforms/${PLATFORM}.yml" ]; then
        compose_files="$compose_files -f $PROJECT_ROOT/docker-compose/platforms/${PLATFORM}.yml"
    fi

    # Add module overlays (only for modules that have infrastructure overlays)
    for mod in ${ACTIVE_MODULES//,/ }; do
        local mod_file="$PROJECT_ROOT/docker-compose/modules/${mod}.yml"
        if [ -f "$mod_file" ]; then
            compose_files="$compose_files -f $mod_file"
        else
            log_debug "No overlay for module: $mod (started as service)"
        fi
    done

    # Add NATS auth overlay when token is configured (production mode)
    if [ -n "$WIP_NATS_TOKEN" ]; then
        local nats_auth_file="$PROJECT_ROOT/docker-compose/modules/nats-auth.yml"
        if [ -f "$nats_auth_file" ]; then
            compose_files="$compose_files -f $nats_auth_file"
            log_info "NATS token authentication enabled"
        fi
    fi

    log_debug "Compose files: $compose_files"

    # Start infrastructure (use absolute path for env file too)
    podman-compose --env-file "$PROJECT_ROOT/.env" $compose_files up -d

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
    local compose_file="docker-compose.dev.yml"
    local build_flag=""
    if [ "$VARIANT" = "prod" ]; then
        compose_file="docker-compose.yml"
        build_flag="--build"  # Rebuild to pick up latest source code
    fi
    podman-compose --env-file "$PROJECT_ROOT/.env" -f "$compose_file" up -d $build_flag

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
    local compose_file="docker-compose.dev.yml"
    local build_flag=""
    if [ "$VARIANT" = "prod" ]; then
        compose_file="docker-compose.yml"
        build_flag="--build"  # Rebuild to pick up latest source code
    fi
    podman-compose --env-file "$PROJECT_ROOT/.env" -f "$compose_file" up -d $build_flag

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
    local compose_file="docker-compose.dev.yml"
    local build_flag=""
    if [ "$VARIANT" = "prod" ]; then
        compose_file="docker-compose.yml"
        build_flag="--build"  # Rebuild to pick up latest source code
    fi
    podman-compose --env-file "$PROJECT_ROOT/.env" -f "$compose_file" up -d $build_flag
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
    if [ "$GENERATE_SECRETS" = "true" ]; then
        echo ""
        echo "  PRODUCTION SECRETS:"
        echo "  ${YELLOW}Credentials:  ${WIP_DATA_DIR#$PROJECT_ROOT/}/secrets/credentials.txt${NC}"
        echo "  ${YELLOW}Store this file securely and consider deleting it!${NC}"
    fi
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
    ensure_linger
    verify_compose_files
    check_nats_conflicts
    clean_nats_data

    # Generate secrets for production mode
    if [ "$GENERATE_SECRETS" = "true" ]; then
        generate_prod_secrets
        save_secrets
        # Update global API_KEY for namespace initialization
        API_KEY="$WIP_API_KEY"
    fi

    generate_env_file
    generate_dex_config
    generate_console_nginx_config
    generate_caddy_config
    generate_nats_config

    start_infrastructure
    start_services

    print_status
    finalize_log
}

main "$@"
