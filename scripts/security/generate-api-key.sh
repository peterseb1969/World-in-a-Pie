#!/bin/bash
#
# WIP API Key Generator
#
# Generates a new API key with SHA-256 hash for use in api-keys.json.
# The hash is compatible with the wip-auth library.
#
# Usage:
#   ./scripts/security/generate-api-key.sh
#   ./scripts/security/generate-api-key.sh --name mykey --groups wip-editors
#   ./scripts/security/generate-api-key.sh --expires 90d
#
# Output: JSON snippet for api-keys.json
#

set -e

# Defaults
KEY_NAME="generated-key"
KEY_OWNER="system"
KEY_GROUPS=""
KEY_EXPIRES=""
KEY_DESCRIPTION=""
HASH_SALT="wip_auth_salt"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --name)
            KEY_NAME="$2"
            shift 2
            ;;
        --owner)
            KEY_OWNER="$2"
            shift 2
            ;;
        --groups)
            KEY_GROUPS="$2"
            shift 2
            ;;
        --expires)
            KEY_EXPIRES="$2"
            shift 2
            ;;
        --description)
            KEY_DESCRIPTION="$2"
            shift 2
            ;;
        --salt)
            HASH_SALT="$2"
            shift 2
            ;;
        --help)
            cat << EOF
WIP API Key Generator

Usage: $0 [options]

Options:
  --name NAME         Human-readable name for the key (default: generated-key)
  --owner OWNER       Owner of the key (default: system)
  --groups GROUPS     Comma-separated groups (e.g., wip-admins,wip-editors)
  --expires DURATION  Expiration (e.g., 30d, 90d, 1y, or ISO date)
  --description DESC  Description of the key's purpose
  --salt SALT         Hash salt (default: wip_auth_salt)
  --help              Show this help

Examples:
  $0 --name service-key --groups wip-services
  $0 --name temp-key --expires 7d --description "Temporary access"
  $0 --name admin-key --groups wip-admins --owner admin@example.com

EOF
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Generate random key
API_KEY=$(openssl rand -hex 32)

# Generate SHA-256 hash (matching wip-auth library)
KEY_HASH=$(echo -n "${HASH_SALT}:${API_KEY}" | shasum -a 256 | cut -d' ' -f1)

# Calculate expiration date if specified
EXPIRES_AT=""
if [ -n "$KEY_EXPIRES" ]; then
    case "$KEY_EXPIRES" in
        *d)
            days="${KEY_EXPIRES%d}"
            if [[ "$(uname)" == "Darwin" ]]; then
                EXPIRES_AT=$(date -v+${days}d -u +"%Y-%m-%dT%H:%M:%SZ")
            else
                EXPIRES_AT=$(date -d "+${days} days" -u +"%Y-%m-%dT%H:%M:%SZ")
            fi
            ;;
        *y)
            years="${KEY_EXPIRES%y}"
            days=$((years * 365))
            if [[ "$(uname)" == "Darwin" ]]; then
                EXPIRES_AT=$(date -v+${days}d -u +"%Y-%m-%dT%H:%M:%SZ")
            else
                EXPIRES_AT=$(date -d "+${days} days" -u +"%Y-%m-%dT%H:%M:%SZ")
            fi
            ;;
        *)
            # Assume ISO date format
            EXPIRES_AT="$KEY_EXPIRES"
            ;;
    esac
fi

# Format groups as JSON array
GROUPS_JSON="[]"
if [ -n "$KEY_GROUPS" ]; then
    # Convert comma-separated to JSON array
    GROUPS_JSON=$(echo "$KEY_GROUPS" | tr ',' '\n' | while read g; do echo "\"$g\""; done | paste -sd, - | sed 's/^/[/;s/$/]/')
fi

# Current timestamp
CREATED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

echo ""
echo "=========================================="
echo "  Generated API Key"
echo "=========================================="
echo ""
echo "Name:        $KEY_NAME"
echo "Owner:       $KEY_OWNER"
[ -n "$KEY_GROUPS" ] && echo "Groups:      $KEY_GROUPS"
[ -n "$EXPIRES_AT" ] && echo "Expires:     $EXPIRES_AT"
echo ""
echo "API Key (store securely):"
echo "  $API_KEY"
echo ""
echo "Key Hash (for api-keys.json):"
echo "  $KEY_HASH"
echo ""
echo "=========================================="
echo "  JSON Entry for api-keys.json"
echo "=========================================="
echo ""

# Build JSON (using heredoc to handle optional fields)
cat << EOF
{
  "name": "$KEY_NAME",
  "key_hash": "$KEY_HASH",
  "owner": "$KEY_OWNER",
  "groups": $GROUPS_JSON,
  "description": "${KEY_DESCRIPTION:-API key for $KEY_NAME}",
  "created_at": "$CREATED_AT"$([ -n "$EXPIRES_AT" ] && echo ",
  \"expires_at\": \"$EXPIRES_AT\"")
}
EOF

echo ""
echo "=========================================="
echo ""
echo "To use this key:"
echo "  1. Add the JSON entry to your api-keys.json file"
echo "  2. Use the API key in requests:"
echo "     curl -H 'X-API-Key: $API_KEY' https://your-wip-instance/api/..."
echo ""
echo "IMPORTANT: Store the API key securely. It cannot be recovered from the hash."
echo ""
