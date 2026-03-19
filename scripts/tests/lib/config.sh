#!/bin/bash
# Configuration detection for WIP test framework
#
# Detects active modules from:
# 1. Running containers (inspecting what's deployed)
# 2. .env file (WIP_ACTIVE_MODULES)
# 3. Saved config file (config/last-install.conf)
#
# Usage:
#   source lib/config.sh
#   detect_config
#   if has_module "oidc"; then ... fi

# Source common utilities if not already sourced
if [[ -z "${TESTS_DIR:-}" ]]; then
    source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Configuration State
# ─────────────────────────────────────────────────────────────────────────────

# Only set defaults if not already set (preserves values from parent scripts)
# Active modules (space-separated)
: "${ACTIVE_MODULES:=}"

# Deployment variant (dev/prod)
: "${DEPLOYMENT_VARIANT:=dev}"

# Network mode
: "${LOCALHOST_MODE:=true}"
: "${HOSTNAME:=localhost}"

# Service ports (preserve if already set)
: "${PORT_REGISTRY:=8001}"
: "${PORT_DEF_STORE:=8002}"
: "${PORT_TEMPLATE_STORE:=8003}"
: "${PORT_DOCUMENT_STORE:=8004}"
: "${PORT_REPORTING_SYNC:=8005}"
: "${PORT_CONSOLE:=3000}"
: "${PORT_CONSOLE_HTTPS:=8443}"

# Auth configuration (preserve if already set)
: "${API_KEY:=dev_master_key_for_testing}"
: "${AUTH_MODE:=api_key_only}"

# ─────────────────────────────────────────────────────────────────────────────
# Module Detection
# ─────────────────────────────────────────────────────────────────────────────

# Check if a module is active
# Usage: has_module "oidc"
has_module() {
    local module="$1"
    [[ " $ACTIVE_MODULES " == *" $module "* ]]
}

# Detect modules from running containers
detect_from_containers() {
    local modules=""

    # Check for OIDC (Dex + Caddy)
    if podman ps --format "{{.Names}}" 2>/dev/null | grep -q "wip-dex"; then
        modules="$modules oidc"
    fi

    # Check for reporting (PostgreSQL + reporting-sync)
    if podman ps --format "{{.Names}}" 2>/dev/null | grep -q "wip-postgres"; then
        modules="$modules reporting"
    fi

    # Check for files (MinIO)
    if podman ps --format "{{.Names}}" 2>/dev/null | grep -q "wip-minio"; then
        modules="$modules files"
    fi

    # Check for ingest (ingest-gateway)
    if podman ps --format "{{.Names}}" 2>/dev/null | grep -q "wip-ingest"; then
        modules="$modules ingest"
    fi

    # Check for NATS
    if podman ps --format "{{.Names}}" 2>/dev/null | grep -q "wip-nats"; then
        modules="$modules nats"
    fi

    # Check for dev-tools (Mongo Express)
    if podman ps --format "{{.Names}}" 2>/dev/null | grep -q "wip-mongo-express"; then
        modules="$modules dev-tools"
    fi

    echo "$modules" | xargs  # trim whitespace
}

# Detect modules from .env file
detect_from_env() {
    local env_file="$PROJECT_ROOT/.env"
    if [[ -f "$env_file" ]]; then
        local modules
        modules=$(grep "^WIP_ACTIVE_MODULES=" "$env_file" 2>/dev/null | cut -d'"' -f2 | tr ',' ' ')
        echo "$modules"
    fi
}

# Detect modules from saved config
detect_from_config() {
    local config_file="${1:-$PROJECT_ROOT/config/last-install.conf}"
    if [[ -f "$config_file" ]]; then
        local modules
        modules=$(grep "^WIP_ACTIVE_MODULES=" "$config_file" 2>/dev/null | cut -d'"' -f2 | tr ',' ' ')
        echo "$modules"
    fi
}

# Main config detection
# Usage: detect_config [config_file]
detect_config() {
    local config_file="${1:-}"

    log_debug "Detecting deployment configuration..."

    # Priority 1: Explicit config file
    if [[ -n "$config_file" && -f "$config_file" ]]; then
        log_debug "Loading config from: $config_file"
        # shellcheck disable=SC1090
        source "$config_file"
        ACTIVE_MODULES="${WIP_ACTIVE_MODULES:-}"
        ACTIVE_MODULES="${ACTIVE_MODULES//,/ }"
        DEPLOYMENT_VARIANT="${WIP_VARIANT:-dev}"
        LOCALHOST_MODE="${WIP_LOCALHOST_MODE:-true}"
        HOSTNAME="${WIP_HOSTNAME:-localhost}"
        API_KEY="${WIP_API_KEY:-dev_master_key_for_testing}"

    # Priority 2: Running containers
    elif podman ps --format "{{.Names}}" 2>/dev/null | grep -q "wip-"; then
        log_debug "Detecting from running containers..."
        ACTIVE_MODULES=$(detect_from_containers)

        # Try to get other settings from .env (grep specific vars to avoid eval issues)
        if [[ -f "$PROJECT_ROOT/.env" ]]; then
            DEPLOYMENT_VARIANT=$(grep "^WIP_VARIANT=" "$PROJECT_ROOT/.env" 2>/dev/null | cut -d= -f2 || echo "dev")
            LOCALHOST_MODE=$(grep "^WIP_NETWORK_MODE=" "$PROJECT_ROOT/.env" 2>/dev/null | cut -d= -f2 || echo "localhost")
            [[ "$LOCALHOST_MODE" == "localhost" ]] && LOCALHOST_MODE="true" || LOCALHOST_MODE="false"
            HOSTNAME=$(grep "^WIP_HOSTNAME=" "$PROJECT_ROOT/.env" 2>/dev/null | cut -d= -f2 || echo "localhost")
            API_KEY=$(grep "^WIP_API_KEY=" "$PROJECT_ROOT/.env" 2>/dev/null | cut -d= -f2 || echo "dev_master_key_for_testing")
        fi

    # Priority 3: .env file (use grep to avoid sourcing issues with special chars)
    elif [[ -f "$PROJECT_ROOT/.env" ]]; then
        log_debug "Loading from .env file..."
        ACTIVE_MODULES=$(grep "^WIP_MODULES=" "$PROJECT_ROOT/.env" 2>/dev/null | cut -d= -f2 || echo "")
        ACTIVE_MODULES="${ACTIVE_MODULES//,/ }"
        DEPLOYMENT_VARIANT=$(grep "^WIP_VARIANT=" "$PROJECT_ROOT/.env" 2>/dev/null | cut -d= -f2 || echo "dev")
        LOCALHOST_MODE=$(grep "^WIP_NETWORK_MODE=" "$PROJECT_ROOT/.env" 2>/dev/null | cut -d= -f2 || echo "localhost")
        [[ "$LOCALHOST_MODE" == "localhost" ]] && LOCALHOST_MODE="true" || LOCALHOST_MODE="false"
        HOSTNAME=$(grep "^WIP_HOSTNAME=" "$PROJECT_ROOT/.env" 2>/dev/null | cut -d= -f2 || echo "localhost")
        API_KEY=$(grep "^WIP_API_KEY=" "$PROJECT_ROOT/.env" 2>/dev/null | cut -d= -f2 || echo "dev_master_key_for_testing")

    # Priority 4: Last install config
    elif [[ -f "$PROJECT_ROOT/config/last-install.conf" ]]; then
        log_debug "Loading from last-install.conf..."
        # shellcheck disable=SC1091
        source "$PROJECT_ROOT/config/last-install.conf"
        ACTIVE_MODULES="${WIP_ACTIVE_MODULES:-}"
        ACTIVE_MODULES="${ACTIVE_MODULES//,/ }"
        DEPLOYMENT_VARIANT="${WIP_VARIANT:-dev}"
        LOCALHOST_MODE="${WIP_LOCALHOST_MODE:-true}"
        HOSTNAME="${WIP_HOSTNAME:-localhost}"
        API_KEY="${WIP_API_KEY:-dev_master_key_for_testing}"
    fi

    # Determine auth mode
    if has_module "oidc" 2>/dev/null; then
        AUTH_MODE="dual"
    else
        AUTH_MODE="api_key_only"
    fi
    true  # Ensure function returns success

    log_debug "Active modules: $ACTIVE_MODULES"
    log_debug "Variant: $DEPLOYMENT_VARIANT"
    log_debug "Auth mode: $AUTH_MODE"
}

# Print detected configuration
print_config() {
    echo -e "${BOLD}Detected Configuration:${NC}"
    echo -e "  Modules:  ${CYAN}${ACTIVE_MODULES:-none}${NC}"
    echo -e "  Variant:  ${CYAN}$DEPLOYMENT_VARIANT${NC}"
    echo -e "  Auth:     ${CYAN}$AUTH_MODE${NC}"
    echo -e "  Hostname: ${CYAN}$HOSTNAME${NC}"
}

# ─────────────────────────────────────────────────────────────────────────────
# Service URLs
# ─────────────────────────────────────────────────────────────────────────────

get_base_url() {
    if [[ "$LOCALHOST_MODE" == "true" ]]; then
        echo "http://localhost"
    else
        echo "https://$HOSTNAME:$PORT_CONSOLE_HTTPS"
    fi
}

get_registry_url() {
    if [[ "$LOCALHOST_MODE" == "true" ]]; then
        echo "http://localhost:$PORT_REGISTRY"
    else
        echo "$(get_base_url)/api/registry"
    fi
}

get_def_store_url() {
    if [[ "$LOCALHOST_MODE" == "true" ]]; then
        echo "http://localhost:$PORT_DEF_STORE"
    else
        echo "$(get_base_url)/api/def-store"
    fi
}

get_template_store_url() {
    if [[ "$LOCALHOST_MODE" == "true" ]]; then
        echo "http://localhost:$PORT_TEMPLATE_STORE"
    else
        echo "$(get_base_url)/api/template-store"
    fi
}

get_document_store_url() {
    if [[ "$LOCALHOST_MODE" == "true" ]]; then
        echo "http://localhost:$PORT_DOCUMENT_STORE"
    else
        echo "$(get_base_url)/api/document-store"
    fi
}

get_reporting_sync_url() {
    if [[ "$LOCALHOST_MODE" == "true" ]]; then
        echo "http://localhost:$PORT_REPORTING_SYNC"
    else
        echo "$(get_base_url)/api/reporting-sync"
    fi
}

get_console_url() {
    if [[ "$LOCALHOST_MODE" == "true" ]]; then
        if has_module "oidc"; then
            echo "https://localhost:$PORT_CONSOLE_HTTPS"
        else
            echo "http://localhost:$PORT_CONSOLE"
        fi
    else
        echo "https://$HOSTNAME:$PORT_CONSOLE_HTTPS"
    fi
}
