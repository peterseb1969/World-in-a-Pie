#!/bin/bash
#
# WIP Production Readiness Check
#
# Validates that a WIP deployment is configured securely for production use.
# Run this before exposing a WIP instance to the internet.
#
# Usage: ./scripts/security/production-check.sh [--fix]
#
# Checks:
#   - No default passwords in .env
#   - CORS not wildcard
#   - File upload limit configured
#   - Rate limiting enabled
#   - API key not default
#   - MongoDB auth enabled
#   - MinIO console port not exposed
#   - NATS monitoring port not exposed
#   - Security headers present
#   - Debug endpoints gated
#   - Secrets files have correct permissions
#   - TLS configured correctly
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Script location
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Counters
ERRORS=0
WARNINGS=0

# Parse arguments
FIX_MODE=false
while [[ $# -gt 0 ]]; do
    case $1 in
        --fix)
            FIX_MODE=true
            shift
            ;;
        --help)
            echo "Usage: $0 [--fix]"
            echo ""
            echo "Options:"
            echo "  --fix    Attempt to fix issues automatically"
            echo "  --help   Show this help"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

pass() {
    echo -e "  ${GREEN}[PASS]${NC} $1"
}

warn() {
    echo -e "  ${YELLOW}[WARN]${NC} $1"
    ((WARNINGS++))
}

fail() {
    echo -e "  ${RED}[FAIL]${NC} $1"
    ((ERRORS++))
}

info() {
    echo -e "  ${BLUE}[INFO]${NC} $1"
}

echo ""
echo "=========================================="
echo "  WIP Production Readiness Check"
echo "=========================================="
echo ""

# Check .env file exists
if [ ! -f "$PROJECT_ROOT/.env" ]; then
    fail ".env file not found - run setup.sh first"
    exit 1
fi

source "$PROJECT_ROOT/.env"

# ===================
# API KEY CHECKS
# ===================
echo "API Key Security:"

# Check for default API key (C4)
if [ "$API_KEY" = "dev_master_key_for_testing" ]; then
    fail "Using default API key (dev_master_key_for_testing) — publicly documented"
else
    # Check key length (should be at least 32 chars for 128-bit security)
    if [ ${#API_KEY} -lt 32 ]; then
        warn "API key is short (${#API_KEY} chars, recommend 64+)"
    else
        pass "API key is strong (${#API_KEY} characters)"
    fi
fi

# Check API key hash salt (H2)
if [ -n "$WIP_AUTH_API_KEY_HASH_SALT" ] && [ "$WIP_AUTH_API_KEY_HASH_SALT" != "wip_auth_salt" ]; then
    pass "Per-deployment API key hash salt configured"
else
    warn "Using default API key hash salt (wip_auth_salt)"
    info "  Re-run setup.sh with --prod to generate per-deployment salt"
fi

# ===================
# CORS CHECKS (C1)
# ===================
echo ""
echo "CORS Configuration:"

if [ -n "$WIP_CORS_ORIGINS" ]; then
    if [ "$WIP_CORS_ORIGINS" = "*" ]; then
        fail "CORS allows all origins (wildcard *)"
        info "  Set WIP_CORS_ORIGINS to your hostname, e.g. https://wip-pi.local:8443"
    else
        pass "CORS restricted to: $WIP_CORS_ORIGINS"
    fi
else
    pass "CORS using service default (localhost only)"
fi

# ===================
# RATE LIMITING (C3)
# ===================
echo ""
echo "Rate Limiting:"

if [ -n "$WIP_RATE_LIMIT" ]; then
    if [ "$WIP_RATE_LIMIT" = "" ]; then
        warn "Rate limiting explicitly disabled"
    else
        pass "Rate limiting configured: $WIP_RATE_LIMIT"
    fi
else
    pass "Rate limiting enabled (default: 40000/minute)"
fi

# ===================
# FILE UPLOAD (C2)
# ===================
echo ""
echo "File Upload Security:"

if [ -n "$WIP_MAX_UPLOAD_SIZE" ]; then
    size_mb=$((WIP_MAX_UPLOAD_SIZE / 1024 / 1024))
    pass "File upload size limit configured: ${size_mb}MB"
else
    pass "File upload size limit enabled (default: 100MB)"
fi

# ===================
# DATABASE CHECKS
# ===================
echo ""
echo "Database Authentication:"

# MongoDB (M4)
if [ -n "$WIP_MONGO_USER" ] && [ -n "$WIP_MONGO_PASSWORD" ]; then
    pass "MongoDB authentication configured"
    if [ ${#WIP_MONGO_PASSWORD} -lt 24 ]; then
        warn "MongoDB password is short (recommend 24+ chars)"
    fi
else
    if [ "$WIP_VARIANT" = "prod" ]; then
        fail "MongoDB running without authentication in production mode"
    else
        warn "MongoDB running without authentication"
    fi
    info "  Re-run setup.sh with --prod to enable"
fi

# PostgreSQL (if reporting enabled)
if [[ "$WIP_MODULES" == *"reporting"* ]]; then
    if [ -n "$WIP_POSTGRES_PASSWORD" ] && [ "$WIP_POSTGRES_PASSWORD" != "wip" ]; then
        pass "PostgreSQL password configured"
    else
        warn "PostgreSQL using default password"
    fi
fi

# ===================
# NATS CHECKS
# ===================
echo ""
echo "Message Queue Security:"

if [ -n "$WIP_NATS_TOKEN" ]; then
    pass "NATS token authentication configured"
else
    warn "NATS running without authentication"
    info "  Re-run setup.sh with --prod to enable"
fi

# NATS monitoring port (H6)
if [ -n "$WIP_NATS_MONITOR_PORT" ] && [ "$WIP_NATS_MONITOR_PORT" != "" ]; then
    if [ "$WIP_VARIANT" = "prod" ]; then
        warn "NATS HTTP monitoring exposed on port $WIP_NATS_MONITOR_PORT (accessible without auth)"
    else
        info "NATS HTTP monitoring on port $WIP_NATS_MONITOR_PORT (dev mode)"
    fi
else
    pass "NATS HTTP monitoring port not exposed"
fi

# ===================
# SECRETS FILE CHECKS
# ===================
echo ""
echo "Secrets Storage:"

SECRETS_DIR="$WIP_DATA_DIR/secrets"
if [ -d "$SECRETS_DIR" ]; then
    # Check directory permissions
    dir_perms=$(stat -f "%Lp" "$SECRETS_DIR" 2>/dev/null || stat -c "%a" "$SECRETS_DIR" 2>/dev/null)
    if [ "$dir_perms" = "700" ]; then
        pass "Secrets directory has correct permissions (700)"
    else
        fail "Secrets directory has insecure permissions ($dir_perms, should be 700)"
        if [ "$FIX_MODE" = true ]; then
            chmod 700 "$SECRETS_DIR"
            info "  Fixed: chmod 700 $SECRETS_DIR"
        fi
    fi

    # Check file permissions
    insecure_files=0
    for secret_file in "$SECRETS_DIR"/*; do
        if [ -f "$secret_file" ]; then
            file_perms=$(stat -f "%Lp" "$secret_file" 2>/dev/null || stat -c "%a" "$secret_file" 2>/dev/null)
            if [ "$file_perms" != "600" ]; then
                ((insecure_files++))
                if [ "$FIX_MODE" = true ]; then
                    chmod 600 "$secret_file"
                fi
            fi
        fi
    done

    if [ $insecure_files -eq 0 ]; then
        pass "All secret files have correct permissions (600)"
    else
        fail "$insecure_files secret file(s) have insecure permissions"
        if [ "$FIX_MODE" = true ]; then
            info "  Fixed: chmod 600 for all files"
        fi
    fi
else
    if [ "$WIP_VARIANT" = "prod" ]; then
        fail "Secrets directory not found ($SECRETS_DIR)"
    else
        info "No secrets directory (dev mode - expected)"
    fi
fi

# ===================
# TLS CHECKS
# ===================
echo ""
echo "TLS Configuration:"

if [[ "$WIP_MODULES" == *"oidc"* ]]; then
    if [ -f "$PROJECT_ROOT/config/caddy/Caddyfile" ]; then
        if grep -q "tls internal" "$PROJECT_ROOT/config/caddy/Caddyfile"; then
            if [ -n "$WIP_ADMIN_EMAIL" ]; then
                warn "Caddy using self-signed certs but --email was provided"
            else
                pass "TLS configured (self-signed for local network)"
            fi
        elif [ -n "$WIP_ADMIN_EMAIL" ]; then
            pass "TLS configured (Let's Encrypt, email: $WIP_ADMIN_EMAIL)"
        else
            fail "No TLS configuration found"
        fi

        # Security headers check (H4)
        if grep -q "X-Content-Type-Options" "$PROJECT_ROOT/config/caddy/Caddyfile"; then
            pass "Security headers configured in Caddyfile"
        else
            warn "Security headers missing from Caddyfile"
            info "  Re-run setup.sh to regenerate Caddyfile with security headers"
        fi

        if [ "$WIP_VARIANT" = "prod" ] && grep -q "Strict-Transport-Security" "$PROJECT_ROOT/config/caddy/Caddyfile"; then
            pass "HSTS header configured (production)"
        elif [ "$WIP_VARIANT" = "prod" ]; then
            warn "HSTS header missing (recommended for production)"
        fi
    else
        fail "Caddyfile not found"
    fi
else
    info "OIDC module not enabled (no TLS proxy)"
fi

# ===================
# FILE STORAGE CHECKS
# ===================
if [[ "$WIP_MODULES" == *"files"* ]]; then
    echo ""
    echo "File Storage Security:"

    if [ "$WIP_FILE_STORAGE_SECRET_KEY" = "wip-minio-password" ]; then
        warn "MinIO using default password"
    else
        pass "MinIO password configured"
    fi

    # MinIO console port (H6)
    if [ -n "$WIP_MINIO_CONSOLE_PORT" ] && [ "$WIP_MINIO_CONSOLE_PORT" != "" ]; then
        if [ "$WIP_VARIANT" = "prod" ]; then
            warn "MinIO web console exposed on port $WIP_MINIO_CONSOLE_PORT (accessible without auth)"
        else
            info "MinIO web console on port $WIP_MINIO_CONSOLE_PORT (dev mode)"
        fi
    else
        pass "MinIO web console port not exposed"
    fi
fi

# ===================
# VARIANT CHECK
# ===================
echo ""
echo "Deployment Variant:"

if [ "$WIP_VARIANT" = "prod" ]; then
    pass "Production variant configured"
else
    warn "Development variant in use"
    info "  Use --prod flag for production deployments"
fi

# ===================
# SUMMARY
# ===================
echo ""
echo "=========================================="
echo "  Summary"
echo "=========================================="
echo ""

if [ $ERRORS -eq 0 ] && [ $WARNINGS -eq 0 ]; then
    echo -e "  ${GREEN}All checks passed!${NC}"
    echo "  Your deployment appears ready for production."
elif [ $ERRORS -eq 0 ]; then
    echo -e "  ${YELLOW}$WARNINGS warning(s), no errors${NC}"
    echo "  Review warnings before production deployment."
else
    echo -e "  ${RED}$ERRORS error(s), $WARNINGS warning(s)${NC}"
    echo "  Fix errors before production deployment."
    echo ""
    echo "  Quick fix: Re-run setup.sh with --prod flag:"
    echo "    ./scripts/setup.sh --preset <preset> --hostname <host> --prod -y"
fi

echo ""
exit $ERRORS
