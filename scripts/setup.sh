#!/bin/bash
# WIP Unified Setup Script
#
# Configurable deployment for Mac, Raspberry Pi, or any Linux system.
# Separates two independent concerns:
#   1. Network Configuration - How users access the system
#   2. Hardware Profile - What services run
#
# Usage:
#   ./scripts/setup.sh --profile mac --network localhost
#   ./scripts/setup.sh --profile pi-standard --network remote --hostname wip-pi.local
#   ./scripts/setup.sh --profile pi-minimal --network localhost
#   ./scripts/setup.sh --help
#
# Network Modes:
#   localhost - Only accessible from local machine (default for mac profile)
#   remote    - Accessible from network (requires --hostname, default for pi profiles)
#              Localhost access is redirected to hostname automatically.
#
# Hardware Profiles:
#   mac         - Mac development with full stack + Mongo Express
#   pi-minimal  - Pi with limited resources, API keys only
#   pi-standard - Pi 4 (2-4GB) with full stack, no Mongo Express
#   pi-large    - Pi 5 8GB+ with full stack + Mongo Express
#   dev-minimal - Any platform, API keys only (no OIDC)

set -e

# Error handling - show where script failed
trap 'log_error "Script failed at line $LINENO. Command: $BASH_COMMAND"' ERR

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step() { echo -e "${BLUE}[STEP]${NC} $1"; }
log_debug() { [ "$DEBUG" = "true" ] && echo -e "${CYAN}[DEBUG]${NC} $1" || true; }

# Script location and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Default configuration
PROFILE=""
NETWORK=""
HOSTNAME=""
HTTPS_PORT="8443"
HTTP_PORT="8080"
API_KEY="dev_master_key_for_testing"
DEBUG="false"

# Storage directory
WIP_DATA_DIR="${WIP_DATA_DIR:-$PROJECT_ROOT/data}"

# Detect platform
detect_platform() {
    if [[ "$(uname)" == "Darwin" ]]; then
        echo "mac"
    elif [[ -f /proc/device-tree/model ]] && grep -qi "raspberry" /proc/device-tree/model 2>/dev/null; then
        # Detect Pi model for appropriate defaults
        if grep -qi "pi 5" /proc/device-tree/model 2>/dev/null; then
            local mem_kb=$(grep MemTotal /proc/meminfo | awk '{print $2}')
            if [ "$mem_kb" -gt 6000000 ]; then
                echo "pi-large"
            else
                echo "pi-standard"
            fi
        else
            echo "pi-standard"
        fi
    else
        echo "linux"
    fi
}

# Show help
show_help() {
    cat << EOF
WIP Unified Setup Script

Usage: $(basename "$0") [OPTIONS]

Network Modes (--network):
  localhost   Only accessible from local machine
  remote      Accessible from network (requires --hostname)
              Localhost access is redirected to hostname automatically.

Hardware Profiles (--profile):
  mac         Mac development - full stack + Mongo Express
  pi-minimal  Pi minimal - API keys only, no OIDC (~80MB less RAM)
  pi-standard Pi standard - full stack without Mongo Express
  pi-large    Pi 5 8GB+ - full stack + Mongo Express
  dev-minimal Any platform - API keys only

Options:
  -p, --profile PROFILE    Hardware profile (auto-detected if not specified)
  -n, --network MODE       Network mode: localhost, remote
  -h, --hostname HOSTNAME  Hostname for remote network mode
      --https-port PORT    HTTPS port (default: 8443)
      --http-port PORT     HTTP port (default: 8080)
      --data-dir DIR       Data storage directory (default: ./data)
      --debug              Enable debug output
      --help               Show this help message

Examples:
  # Mac localhost development (auto-detects profile and network)
  $(basename "$0")

  # Mac with network access
  $(basename "$0") --network remote --hostname dev-mac.local

  # Pi standard deployment
  $(basename "$0") --profile pi-standard --hostname wip-pi.local

  # Pi minimal (API keys only)
  $(basename "$0") --profile pi-minimal

  # Quick development without OIDC
  $(basename "$0") --profile dev-minimal

EOF
}

# Parse command line arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            -p|--profile)
                PROFILE="$2"
                shift 2
                ;;
            -n|--network)
                NETWORK="$2"
                shift 2
                ;;
            -h|--hostname)
                HOSTNAME="$2"
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
            --debug)
                DEBUG="true"
                shift
                ;;
            --help)
                show_help
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                echo "Use --help for usage information"
                exit 1
                ;;
        esac
    done
}

# Set defaults based on detected platform
set_defaults() {
    local detected_platform=$(detect_platform)

    # Auto-detect profile if not specified
    if [ -z "$PROFILE" ]; then
        case "$detected_platform" in
            mac)
                PROFILE="mac"
                ;;
            pi-large)
                PROFILE="pi-large"
                ;;
            pi-standard|pi-*)
                PROFILE="pi-standard"
                ;;
            *)
                PROFILE="mac"  # Default to mac for unknown Linux
                ;;
        esac
        log_info "Auto-detected profile: $PROFILE"
    fi

    # Auto-detect network mode based on profile
    if [ -z "$NETWORK" ]; then
        case "$PROFILE" in
            mac|dev-minimal)
                NETWORK="localhost"
                ;;
            pi-*)
                NETWORK="remote"
                ;;
            *)
                NETWORK="localhost"
                ;;
        esac
        log_info "Auto-selected network mode: $NETWORK"
    fi

    # Auto-detect hostname for remote mode
    if [ -z "$HOSTNAME" ] && [ "$NETWORK" != "localhost" ]; then
        HOSTNAME="$(hostname).local"
        log_info "Auto-detected hostname: $HOSTNAME"
    fi
}

# Validate configuration
validate_config() {
    # Validate profile
    local profile_file="$PROJECT_ROOT/config/profiles/${PROFILE}.env"
    if [ ! -f "$profile_file" ]; then
        log_error "Unknown profile: $PROFILE"
        echo "Available profiles: mac, pi-minimal, pi-standard, pi-large, dev-minimal"
        exit 1
    fi

    # Validate network mode
    case "$NETWORK" in
        localhost|remote) ;;
        both)
            log_warn "Network mode 'both' has been removed. Using 'remote' instead."
            log_warn "In remote mode, localhost access is automatically redirected to hostname."
            NETWORK="remote"
            ;;
        *)
            log_error "Invalid network mode: $NETWORK"
            echo "Valid modes: localhost, remote"
            exit 1
            ;;
    esac

    # Require hostname for remote mode
    if [ "$NETWORK" != "localhost" ] && [ -z "$HOSTNAME" ]; then
        log_error "Network mode '$NETWORK' requires --hostname"
        exit 1
    fi
}

# Load profile configuration
load_profile() {
    local profile_file="$PROJECT_ROOT/config/profiles/${PROFILE}.env"
    log_step "Loading profile from: $profile_file"

    if [ ! -f "$profile_file" ]; then
        log_error "Profile file not found: $profile_file"
        log_error "Make sure you have pulled the latest code: git pull"
        exit 1
    fi

    # Source profile file
    set -a
    if ! source "$profile_file"; then
        log_error "Failed to load profile file: $profile_file"
        exit 1
    fi
    set +a

    log_info "Profile loaded: $WIP_PROFILE_DESCRIPTION"
    log_debug "MongoDB image: $WIP_MONGODB_IMAGE"
    log_debug "Include Dex: $WIP_INCLUDE_DEX"
    log_debug "Include Caddy: $WIP_INCLUDE_CADDY"
    log_debug "Include Mongo Express: $WIP_INCLUDE_MONGO_EXPRESS"
    log_debug "Include MinIO: $WIP_INCLUDE_MINIO"
}

# Generate Dex configuration from template
generate_dex_config() {
    if [ "$WIP_INCLUDE_DEX" != "true" ]; then
        log_debug "Skipping Dex config (not included in profile)"
        return
    fi

    log_info "Generating Dex configuration..."

    local output="$PROJECT_ROOT/config/dex/config.yaml"

    # Determine issuer URL based on network mode and Caddy
    # IMPORTANT: The issuer URL is what goes into JWT tokens and must match
    # what the browser accesses. When Caddy is enabled, that's https://host:port/dex
    # In remote mode, localhost access is redirected to hostname by Caddy.
    local issuer
    if [ "$WIP_INCLUDE_CADDY" = "true" ]; then
        case "$NETWORK" in
            localhost)
                issuer="https://localhost:${HTTPS_PORT}/dex"
                ;;
            remote)
                issuer="https://${HOSTNAME}:${HTTPS_PORT}/dex"
                ;;
        esac
    else
        # No Caddy - direct HTTP access to Dex
        issuer="http://localhost:5556/dex"
    fi

    # Generate config directly using heredoc (avoids sed multi-line issues)
    cat > "$output" << DEXEOF
# Dex Configuration
# Generated by scripts/setup.sh - do not edit directly
# Network mode: $NETWORK

issuer: ${issuer}

storage:
  type: sqlite3
  config:
    file: /data/dex.db

web:
  http: 0.0.0.0:5556
  allowedOrigins:
DEXEOF

    # Add allowed origins based on network mode
    case "$NETWORK" in
        localhost)
            cat >> "$output" << ORIGINS
    - http://localhost:3000
    - http://localhost:3001
    - https://localhost:${HTTPS_PORT}
    - https://localhost
    - https://127.0.0.1:${HTTPS_PORT}
    - https://127.0.0.1
ORIGINS
            ;;
        remote)
            cat >> "$output" << ORIGINS
    - https://${HOSTNAME}:${HTTPS_PORT}
ORIGINS
            ;;
    esac

    # Add static clients section
    cat >> "$output" << 'CLIENTS'

staticClients:
  - id: wip-console
    name: WIP Console
    secret: wip-console-secret
    redirectURIs:
CLIENTS

    # Add redirect URIs based on network mode
    case "$NETWORK" in
        localhost)
            cat >> "$output" << REDIRECTS
      - http://localhost:3000/auth/callback
      - http://localhost:3000/auth/silent-renew
      - https://localhost:${HTTPS_PORT}/auth/callback
      - https://localhost:${HTTPS_PORT}/auth/silent-renew
      - https://localhost/auth/callback
      - https://localhost/auth/silent-renew
REDIRECTS
            ;;
        remote)
            cat >> "$output" << REDIRECTS
      - https://${HOSTNAME}:${HTTPS_PORT}/auth/callback
      - https://${HOSTNAME}:${HTTPS_PORT}/auth/silent-renew
REDIRECTS
            ;;
    esac

    # Add remaining static config
    cat >> "$output" << 'STATICCONFIG'
    public: true

enablePasswordDB: true

# Password hashes for dev users (bcrypt, cost 10)
# admin123, editor123, viewer123
staticPasswords:
  - email: "admin@wip.local"
    hash: "$2b$10$BQw2e6w70bAeuJOlxq25SOlGCao22TWXJlYjEqyiHh9ytdwh026cS"
    username: "admin"
    userID: "admin-001"
  - email: "editor@wip.local"
    hash: "$2b$10$6VcSivBiArWgUxrT9e/rNuWB5.JMcN/ZYHazHo5EaCRr3ISO5KJou"
    username: "editor"
    userID: "editor-001"
  - email: "viewer@wip.local"
    hash: "$2b$10$e.WStasfitEUEEE5vzBY9uSgEpKIplzvzzieBNPXKu3TWSNxUxw2i"
    username: "viewer"
    userID: "viewer-001"

oauth2:
  skipApprovalScreen: true
  alwaysShowLoginScreen: false

logger:
  level: "info"
  format: "text"
STATICCONFIG

    log_debug "Generated Dex config with issuer: $issuer"
}

# Generate Caddy configuration
generate_caddy_config() {
    if [ "$WIP_INCLUDE_CADDY" != "true" ]; then
        log_debug "Skipping Caddy config (not included in profile)"
        return
    fi

    log_info "Generating Caddy configuration..."

    local output="$PROJECT_ROOT/config/caddy/Caddyfile"

    # Determine host patterns based on network mode
    local hosts
    case "$NETWORK" in
        localhost)
            hosts="localhost, 127.0.0.1"
            ;;
        remote)
            hosts="${HOSTNAME}"
            # Also add IP if we can detect it
            local ip=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "")
            if [ -n "$ip" ]; then
                hosts="${hosts}, ${ip}"
            fi
            ;;
        both)
            hosts="*.local, 192.168.*.*"
            if [ -n "$HOSTNAME" ]; then
                hosts="${hosts}, ${HOSTNAME}"
            fi
            ;;
    esac

    # Write Caddy config directly
    cat > "$output" << 'CADDYHEADER'
# WIP Caddy Configuration
#
# THIS FILE IS GENERATED - DO NOT EDIT DIRECTLY
# Regenerate with: scripts/setup.sh
#
# Uses self-signed certificate for local network access.
# Browser will warn about self-signed cert - accept once.
CADDYHEADER

    # In remote mode, add a redirect block so localhost access goes to hostname
    if [ "$NETWORK" = "remote" ]; then
        cat >> "$output" << REDIRECT

# Redirect localhost to hostname (convenience for local access)
# Uses 302 (temporary) so browser doesn't cache if mode changes later
localhost, 127.0.0.1 {
    tls internal
    redir https://${HOSTNAME}:${HTTPS_PORT}{uri} 302
}
REDIRECT
    fi

    # Main site block with reverse proxy rules
    cat >> "$output" << SITEBLOCK

# HTTPS server with internal TLS (self-signed)
${hosts} {
    tls internal

    # Dex OIDC provider
    handle /dex/* {
        reverse_proxy wip-dex:5556
    }

    # API Services
    handle /api/registry/* {
        reverse_proxy wip-registry-dev:8001
    }
    handle /api/def-store/* {
        reverse_proxy wip-def-store-dev:8002
    }
    handle /api/template-store/* {
        reverse_proxy wip-template-store-dev:8003
    }
    handle /api/document-store/* {
        reverse_proxy wip-document-store-dev:8004
    }
    handle /api/reporting-sync/* {
        uri strip_prefix /api/reporting-sync
        reverse_proxy wip-reporting-sync-dev:8005
    }

    # Console (catch-all)
    handle {
        reverse_proxy wip-console-dev:3000
    }

    log {
        output stdout
        format console
        level WARN
    }
}

# HTTP redirect to HTTPS
http:// {
    redir https://{host}{uri} permanent
}
SITEBLOCK

    # In "both" mode, add a redirect block so localhost sends users to the hostname.
    # This is required because Dex's OIDC issuer is set to the hostname - login via
    # localhost would fail due to issuer mismatch.
    if [ "$NETWORK" = "both" ] && [ -n "$HOSTNAME" ]; then
        local redirect_block
        redirect_block=$(cat << REDIR

# Redirect localhost to hostname (required for OIDC issuer match)
localhost, 127.0.0.1 {
    tls internal
    redir https://${HOSTNAME}:${HTTPS_PORT}{uri} permanent
}
REDIR
)
        # Insert the redirect block before the main site block
        printf '%s\n\n%s\n' "$redirect_block" "$(cat "$output")" > "$output"
    fi

    log_debug "Generated Caddy config with hosts: $hosts"
}

# Generate environment file
generate_env_file() {
    log_info "Generating environment file..."

    local env_file="$PROJECT_ROOT/.env"

    # Determine JWT issuer URL
    # IMPORTANT: When Caddy is enabled, browser accesses Dex via Caddy at https://host:PORT/dex
    # The issuer URL must match what the browser sees (and what goes into tokens)
    local jwt_issuer
    if [ "$WIP_INCLUDE_DEX" = "true" ]; then
        if [ "$WIP_INCLUDE_CADDY" = "true" ]; then
            # Caddy enabled - use HTTPS through Caddy
            case "$NETWORK" in
                localhost)
                    jwt_issuer="https://localhost:${HTTPS_PORT}/dex"
                    ;;
                remote)
                    jwt_issuer="https://${HOSTNAME}:${HTTPS_PORT}/dex"
                    ;;
            esac
        else
            # No Caddy - access Dex directly via HTTP
            jwt_issuer="http://localhost:5556/dex"
        fi
    fi

    # Determine OIDC enabled state
    local oidc_enabled="false"
    if [ "$WIP_INCLUDE_DEX" = "true" ]; then
        oidc_enabled="true"
    fi

    # Write environment file
    cat > "$env_file" << EOF
# WIP Environment Configuration
# Generated by scripts/setup.sh
# Profile: $PROFILE | Network: $NETWORK | Hostname: ${HOSTNAME:-localhost}

# ============================================
# PROFILE SETTINGS
# ============================================
WIP_PROFILE=$PROFILE
WIP_DATA_DIR=$WIP_DATA_DIR

# ============================================
# NETWORK SETTINGS
# ============================================
WIP_NETWORK_MODE=$NETWORK
WIP_HOSTNAME=${HOSTNAME:-localhost}
WIP_HTTPS_PORT=$HTTPS_PORT
WIP_HTTP_PORT=$HTTP_PORT

# ============================================
# SERVICE IMAGES
# ============================================
WIP_MONGODB_IMAGE=$WIP_MONGODB_IMAGE

# ============================================
# AUTH CONFIGURATION
# ============================================
WIP_AUTH_MODE=$WIP_AUTH_MODE
WIP_AUTH_LEGACY_API_KEY=$API_KEY
EOF

    # Add JWT config if Dex is enabled
    if [ "$WIP_INCLUDE_DEX" = "true" ]; then
        cat >> "$env_file" << EOF
WIP_AUTH_JWT_ISSUER_URL=$jwt_issuer
WIP_AUTH_JWT_JWKS_URI=http://wip-dex:5556/dex/keys
WIP_AUTH_JWT_AUDIENCE=wip-console
EOF
    fi

    # Add MinIO config if enabled
    if [ "$WIP_INCLUDE_MINIO" = "true" ]; then
        cat >> "$env_file" << EOF

# ============================================
# FILE STORAGE (MinIO)
# ============================================
WIP_FILE_STORAGE_ENABLED=true
WIP_FILE_STORAGE_TYPE=minio
WIP_FILE_STORAGE_ENDPOINT=http://wip-minio:9000
WIP_FILE_STORAGE_ACCESS_KEY=wip-minio-root
WIP_FILE_STORAGE_SECRET_KEY=wip-minio-password
WIP_FILE_STORAGE_BUCKET=wip-attachments
EOF
    else
        cat >> "$env_file" << EOF

# ============================================
# FILE STORAGE (Disabled)
# ============================================
WIP_FILE_STORAGE_ENABLED=false
EOF
    fi

    # Add console config
    cat >> "$env_file" << EOF

# ============================================
# CONSOLE CONFIGURATION
# ============================================
VITE_OIDC_ENABLED=$oidc_enabled
EOF

    if [ "$WIP_INCLUDE_DEX" = "true" ]; then
        cat >> "$env_file" << EOF
VITE_OIDC_AUTHORITY=/dex
VITE_DEX_TARGET=http://wip-dex:5556
VITE_OIDC_CLIENT_ID=wip-console
VITE_OIDC_CLIENT_SECRET=wip-console-secret
VITE_OIDC_PROVIDER_NAME=Dex
EOF
    fi

    log_debug "Generated environment file: $env_file"
}

# Select and start infrastructure compose file
select_compose_files() {
    log_step "Selecting compose configuration..."
    log_debug "WIP_INCLUDE_DEX=$WIP_INCLUDE_DEX, WIP_INCLUDE_CADDY=$WIP_INCLUDE_CADDY"

    # Determine base infrastructure file based on profile
    # Using case for portability instead of [[ ]]
    local is_pi_profile=false
    case "$PROFILE" in
        pi-*) is_pi_profile=true ;;
    esac

    if [ "$WIP_INCLUDE_DEX" = "true" ] && [ "$WIP_INCLUDE_CADDY" = "true" ]; then
        if [ "$is_pi_profile" = "true" ]; then
            INFRA_COMPOSE="docker-compose.infra.pi.yml"
        else
            INFRA_COMPOSE="docker-compose.infra.yml"
        fi
    else
        if [ "$is_pi_profile" = "true" ]; then
            INFRA_COMPOSE="docker-compose.infra.pi.minimal.yml"
        else
            INFRA_COMPOSE="docker-compose.infra.minimal.yml"
        fi
    fi

    log_info "Infrastructure compose file: $INFRA_COMPOSE"
}

# Check dependencies
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

    if ! command -v jq &> /dev/null; then
        missing+=("jq")
    else
        echo "  jq: $(jq --version)"
    fi

    if [ ${#missing[@]} -gt 0 ]; then
        log_error "Missing dependencies: ${missing[*]}"
        echo ""
        echo "Installation instructions:"

        local platform=$(detect_platform)
        case "$platform" in
            mac)
                echo "  brew install podman jq"
                echo "  pip3 install podman-compose"
                echo "  podman machine init && podman machine start"
                ;;
            pi-*|linux)
                echo "  sudo apt update && sudo apt install -y podman podman-compose jq"
                ;;
        esac
        exit 1
    fi

    # Check podman machine on Mac
    if [[ "$(uname)" == "Darwin" ]]; then
        if ! podman machine inspect 2>/dev/null | grep -q '"State": "running"'; then
            log_warn "Podman machine not running. Starting..."
            podman machine start || {
                log_error "Failed to start Podman machine."
                echo "Please run: podman machine init && podman machine start"
                exit 1
            }
        fi
        echo "  podman machine: running"
    fi

    echo ""
}

# Create storage directories
setup_storage() {
    log_step "Setting up storage directories..."
    echo "  Data directory: $WIP_DATA_DIR"

    mkdir -p "$WIP_DATA_DIR"/{mongodb,postgres,nats,dex,caddy/data,caddy/config,minio}
    echo "  Created subdirectories: mongodb, postgres, nats, dex, caddy, minio"

    # Fix Dex directory ownership for rootless Podman on Linux
    if [[ "$(uname)" != "Darwin" ]] && [ "$WIP_INCLUDE_DEX" = "true" ]; then
        log_info "Setting Dex directory ownership for rootless Podman..."
        podman unshare chown 1001:1001 "$WIP_DATA_DIR/dex" 2>/dev/null || true
    fi

    echo ""
}

# Health check functions
wait_for_container() {
    local container="$1"
    local attempt=1
    local max_attempts=$((WIP_HEALTH_CHECK_TIMEOUT / WIP_HEALTH_CHECK_INTERVAL))

    while [ $attempt -le $max_attempts ]; do
        if podman ps --format "{{.Names}}" | grep -q "^${container}$"; then
            echo "  $container: running"
            return 0
        fi
        echo "  $container: waiting... ($attempt/$max_attempts)"
        sleep $WIP_HEALTH_CHECK_INTERVAL
        attempt=$((attempt + 1))
    done

    log_error "$container failed to start after ${WIP_HEALTH_CHECK_TIMEOUT}s"
    podman logs "$container" 2>&1 | tail -10 || true
    return 1
}

wait_for_health() {
    local name="$1"
    local url="$2"
    local attempt=1
    local max_attempts=$((WIP_HEALTH_CHECK_TIMEOUT / WIP_HEALTH_CHECK_INTERVAL))

    while [ $attempt -le $max_attempts ]; do
        local health=$(curl -s "$url" 2>/dev/null || echo '{"status":"unreachable"}')
        if echo "$health" | grep -q '"healthy"\|"status":"healthy"'; then
            log_info "  $name is healthy"
            return 0
        fi
        echo "  $name: waiting for health... ($attempt/$max_attempts)"
        sleep $WIP_HEALTH_CHECK_INTERVAL
        attempt=$((attempt + 1))
    done

    log_error "$name failed health check after ${WIP_HEALTH_CHECK_TIMEOUT}s"
    return 1
}

# Start infrastructure
start_infrastructure() {
    log_step "Starting infrastructure..."

    local services_desc="MongoDB, PostgreSQL, NATS"
    [ "$WIP_INCLUDE_MINIO" = "true" ] && services_desc="$services_desc, MinIO"
    [ "$WIP_INCLUDE_DEX" = "true" ] && services_desc="$services_desc, Dex"
    [ "$WIP_INCLUDE_CADDY" = "true" ] && services_desc="$services_desc, Caddy"
    [ "$WIP_INCLUDE_MONGO_EXPRESS" = "true" ] && services_desc="$services_desc, Mongo Express"

    log_info "Services: $services_desc"

    cd "$PROJECT_ROOT"
    podman-compose --env-file .env -f "$INFRA_COMPOSE" up -d

    log_info "Waiting for infrastructure containers..."

    local containers="wip-mongodb wip-postgres wip-nats"
    [ "$WIP_INCLUDE_MINIO" = "true" ] && containers="$containers wip-minio"
    [ "$WIP_INCLUDE_DEX" = "true" ] && containers="$containers wip-dex"
    [ "$WIP_INCLUDE_CADDY" = "true" ] && containers="$containers wip-caddy"
    [ "$WIP_INCLUDE_MONGO_EXPRESS" = "true" ] && containers="$containers wip-mongo-express"

    local all_healthy=true
    for container in $containers; do
        if ! wait_for_container "$container"; then
            all_healthy=false
        fi
    done

    if [ "$all_healthy" = false ]; then
        log_error "Infrastructure failed to start. Cannot continue."
        exit 1
    fi

    # Initialize MinIO bucket if enabled
    if [ "$WIP_INCLUDE_MINIO" = "true" ]; then
        log_info "Initializing MinIO bucket..."
        # Wait for MinIO to be fully ready (health check)
        local minio_ready=false
        for i in {1..10}; do
            if curl -sf http://localhost:9000/minio/health/live >/dev/null 2>&1; then
                minio_ready=true
                break
            fi
            sleep 2
        done

        if [ "$minio_ready" = true ]; then
            # Create the bucket using mc (MinIO client) inside the container
            podman exec wip-minio mc alias set local http://localhost:9000 wip-minio-root "${MINIO_ROOT_PASSWORD:-wip-minio-password}" 2>/dev/null || true
            podman exec wip-minio mc mb local/wip-attachments --ignore-existing 2>/dev/null || true
            if podman exec wip-minio mc ls local/wip-attachments >/dev/null 2>&1; then
                log_info "  MinIO bucket 'wip-attachments' ready"
            else
                # Bucket might already exist or mc not available - check via curl
                log_info "  MinIO is running (bucket will be created on first use)"
            fi
        else
            log_warn "  MinIO health check failed - may need manual setup"
        fi
    fi

    echo ""
}

# Start a service
start_service() {
    local name="$1"
    local dir="$2"
    local port="$3"

    log_info "Starting $name..."
    cd "$PROJECT_ROOT/components/$dir"
    podman-compose --env-file "$PROJECT_ROOT/.env" -f docker-compose.dev.yml up -d

    if ! wait_for_health "$name" "http://localhost:$port/health"; then
        log_warn "$name may still be starting - continuing anyway"
    fi
}

# Start all application services
start_services() {
    log_step "Starting Registry and initializing namespaces..."

    cd "$PROJECT_ROOT/components/registry"
    podman-compose --env-file "$PROJECT_ROOT/.env" -f docker-compose.dev.yml up -d

    if ! wait_for_health "Registry" "http://localhost:8001/health"; then
        log_error "Registry failed to start"
        exit 1
    fi

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
    start_service "Reporting-Sync" "reporting-sync" "8005"
    echo ""

    log_step "Starting WIP Console..."
    cd "$PROJECT_ROOT/ui/wip-console"
    podman-compose --env-file "$PROJECT_ROOT/.env" -f docker-compose.dev.yml up -d
    log_info "Waiting for Console to start..."
    sleep 10
    echo ""
}

# Run final health checks
final_health_checks() {
    log_step "Running final health checks..."

    local services=(
        "Registry:8001"
        "Def-Store:8002"
        "Template-Store:8003"
        "Document-Store:8004"
        "Reporting-Sync:8005"
    )

    local all_healthy=true
    for svc in "${services[@]}"; do
        local name="${svc%%:*}"
        local port="${svc##*:}"
        local health=$(curl -s "http://localhost:$port/health" 2>/dev/null || echo '{"status":"unreachable"}')
        if echo "$health" | grep -q '"healthy"\|"status":"healthy"'; then
            echo "  $name (port $port): healthy"
        else
            echo "  $name (port $port): NOT HEALTHY"
            all_healthy=false
        fi
    done

    echo ""
    echo "$all_healthy"
}

# Print final status and access information
print_status() {
    local all_healthy="$1"

    log_step "Final status..."
    echo ""
    echo "Containers running:"
    podman ps --format "  {{.Names}}: {{.Status}}"
    echo ""

    # Show memory on Pi
    if [[ "$(uname)" != "Darwin" ]]; then
        echo "Memory usage:"
        free -h | grep -E "Mem|Swap"
        echo ""
    fi

    local ip=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "")

    echo "=========================================="
    if [ "$all_healthy" = "true" ]; then
        echo -e "${GREEN}  Setup completed successfully!${NC}"
    else
        echo -e "${YELLOW}  Setup completed with warnings${NC}"
        echo -e "${YELLOW}  Some services may still be starting.${NC}"
    fi
    echo "=========================================="
    echo ""
    echo "Configuration:"
    echo "  Profile: $PROFILE"
    echo "  Network: $NETWORK"
    [ -n "$HOSTNAME" ] && echo "  Hostname: $HOSTNAME"
    echo ""

    # Access URLs based on network mode and profile
    if [ "$WIP_INCLUDE_CADDY" = "true" ]; then
        echo "Access WIP Console (HTTPS):"
        case "$NETWORK" in
            localhost)
                echo "  https://localhost:${HTTPS_PORT}"
                ;;
            remote)
                echo "  https://${HOSTNAME}:${HTTPS_PORT}"
                [ -n "$ip" ] && echo "  https://${ip}:${HTTPS_PORT}"
                echo "  (localhost access redirects to hostname automatically)"
                ;;
        esac
        echo ""
        echo "  Note: Browser will warn about self-signed certificate."
        echo "        Click 'Advanced' -> 'Proceed' to accept."
    else
        echo "Access WIP Console (HTTP):"
        echo "  http://localhost:3000"
        [ "$NETWORK" != "localhost" ] && [ -n "$HOSTNAME" ] && echo "  http://${HOSTNAME}:3000"
        [ "$NETWORK" != "localhost" ] && [ -n "$ip" ] && echo "  http://${ip}:3000"
    fi
    echo ""

    # Login information
    if [ "$WIP_INCLUDE_DEX" = "true" ]; then
        echo "Login options:"
        echo "  - Click 'Login with Dex' -> admin@wip.local / admin123"
        echo "  - Or use API Key: $API_KEY"
        echo ""
        echo "Test users (for Dex OIDC):"
        echo "  admin@wip.local / admin123 (wip-admins group)"
        echo "  editor@wip.local / editor123 (wip-editors group)"
        echo "  viewer@wip.local / viewer123 (wip-viewers group)"
    else
        echo "Login:"
        echo "  Use API Key: $API_KEY"
        echo "  (OIDC disabled - API keys only mode)"
    fi
    echo ""

    echo "API Documentation:"
    echo "  Registry:       http://localhost:8001/docs"
    echo "  Def-Store:      http://localhost:8002/docs"
    echo "  Template-Store: http://localhost:8003/docs"
    echo "  Document-Store: http://localhost:8004/docs"
    echo "  Reporting-Sync: http://localhost:8005/docs"
    echo ""

    echo "Database access:"
    [ "$WIP_INCLUDE_MONGO_EXPRESS" = "true" ] && echo "  Mongo Express:  http://localhost:8081 (admin/admin)"
    [ "$WIP_INCLUDE_MINIO" = "true" ] && echo "  MinIO Console:  http://localhost:9001 (wip-minio-root/wip-minio-password)"
    echo "  MongoDB CLI:    podman exec -it wip-mongodb mongo"
    echo "  PostgreSQL:     podman exec -it wip-postgres psql -U wip -d wip_reporting"
    echo ""

    echo "Seed test data:"
    echo "  python3 -m venv .venv && source .venv/bin/activate"
    echo "  pip install faker requests"
    echo "  python scripts/seed_comprehensive.py"
    echo ""
}

# Main execution
main() {
    echo "=========================================="
    echo "  WIP Unified Setup Script"
    echo "=========================================="
    echo ""

    parse_args "$@"
    set_defaults
    validate_config

    echo "Configuration:"
    echo "  Profile:  $PROFILE"
    echo "  Network:  $NETWORK"
    [ -n "$HOSTNAME" ] && echo "  Hostname: $HOSTNAME"
    echo "  Data dir: $WIP_DATA_DIR"
    echo ""

    log_step "Starting setup..."
    log_debug "Calling load_profile..."
    load_profile
    log_debug "Calling select_compose_files..."
    select_compose_files
    log_debug "Calling check_dependencies..."
    check_dependencies
    setup_storage

    generate_dex_config
    generate_caddy_config
    generate_env_file

    start_infrastructure
    start_services

    local all_healthy=$(final_health_checks)
    print_status "$all_healthy"
}

# Run main
main "$@"
