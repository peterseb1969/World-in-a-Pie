#!/usr/bin/env bash
#
# Create a new WIP app project directory with all required files.
#
# Usage:
#   ./scripts/create-app-project.sh /path/to/my-new-app [--name "My App"]
#
# This script:
#   1. Creates the directory structure
#   2. Copies slash commands from docs/slash-commands/
#   3. Copies reference docs (AI-Assisted-Development.md, WIP_PoNIFs.md, WIP_DevGuardrails.md)
#   4. Generates .mcp.json pointing to this WIP installation
#   5. Copies and extracts client library tarballs + READMEs
#   6. Generates a starter CLAUDE.md
#   7. Initialises a git repo
#
# The generated .mcp.json uses WIP_API_KEY_FILE instead of a hardcoded key,
# so API key rotation in WIP automatically applies to all apps.

set -euo pipefail

WIP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# --- Parse arguments ---

APP_DIR=""
APP_NAME=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --name)
            APP_NAME="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 <app-directory> [--name \"App Name\"]"
            echo ""
            echo "Creates a new WIP app project with all required files."
            echo ""
            echo "Options:"
            echo "  --name    Display name for the app (default: derived from directory name)"
            echo "  -h        Show this help"
            exit 0
            ;;
        *)
            APP_DIR="$1"
            shift
            ;;
    esac
done

if [ -z "$APP_DIR" ]; then
    echo "Error: App directory path is required."
    echo "Usage: $0 <app-directory> [--name \"App Name\"]"
    exit 1
fi

# Resolve to absolute path
APP_DIR="$(cd "$(dirname "$APP_DIR")" 2>/dev/null && pwd)/$(basename "$APP_DIR")" || APP_DIR="$(pwd)/$APP_DIR"

# Derive app name from directory if not provided
if [ -z "$APP_NAME" ]; then
    APP_NAME="$(basename "$APP_DIR" | sed 's/[-_]/ /g' | awk '{for(i=1;i<=NF;i++) $i=toupper(substr($i,1,1)) substr($i,2)}1')"
fi

echo "Creating WIP app project:"
echo "  Directory: $APP_DIR"
echo "  App name:  $APP_NAME"
echo "  WIP root:  $WIP_ROOT"
echo ""

# --- Check prerequisites ---

if [ -d "$APP_DIR" ] && [ "$(ls -A "$APP_DIR" 2>/dev/null)" ]; then
    echo "Error: $APP_DIR already exists and is not empty."
    echo "Choose a new directory or remove the existing one."
    exit 1
fi

if [ ! -d "$WIP_ROOT/docs/slash-commands" ]; then
    echo "Error: $WIP_ROOT/docs/slash-commands/ not found."
    echo "Run this script from the WIP project root."
    exit 1
fi

# --- Create directory structure ---

echo "1. Creating directory structure..."
mkdir -p "$APP_DIR/.claude/commands"
mkdir -p "$APP_DIR/docs"
mkdir -p "$APP_DIR/libs"

# --- Copy slash commands ---

echo "2. Copying slash commands (12 files)..."
cp "$WIP_ROOT/docs/slash-commands/"*.md "$APP_DIR/.claude/commands/"
echo "   Copied: $(ls "$APP_DIR/.claude/commands/" | wc -l | tr -d ' ') commands"

# --- Copy reference docs ---

echo "3. Copying reference documentation..."
for doc in AI-Assisted-Development.md WIP_PoNIFs.md WIP_DevGuardrails.md; do
    if [ -f "$WIP_ROOT/docs/$doc" ]; then
        cp "$WIP_ROOT/docs/$doc" "$APP_DIR/docs/"
        echo "   Copied: docs/$doc"
    else
        echo "   Warning: docs/$doc not found, skipping"
    fi
done

# --- Generate .mcp.json ---

echo "4. Generating .mcp.json..."

# Determine API key from .env (source of truth for running containers)
ACTIVE_KEY=""
if [ -f "$WIP_ROOT/.env" ]; then
    ACTIVE_KEY=$(grep "^API_KEY=" "$WIP_ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2-)
fi
ACTIVE_KEY="${ACTIVE_KEY:-dev_master_key_for_testing}"

if [ "$ACTIVE_KEY" = "dev_master_key_for_testing" ]; then
    # Dev mode — hardcode the well-known dev key
    MCP_ENV=$(cat <<ENVEOF
        "WIP_API_KEY": "dev_master_key_for_testing",
        "PYTHONPATH": "$WIP_ROOT/components/mcp-server/src"
ENVEOF
)
    echo "   API key: dev_master_key_for_testing (dev mode)"
else
    # Production — embed the actual key from .env
    MCP_ENV=$(cat <<ENVEOF
        "WIP_API_KEY": "$ACTIVE_KEY",
        "PYTHONPATH": "$WIP_ROOT/components/mcp-server/src"
ENVEOF
)
    echo "   API key: production key from .env (${#ACTIVE_KEY} chars)"
    echo "   Note: if you rotate the API key, re-run this script or update .mcp.json"
fi

# Determine Python path
PYTHON_PATH="$WIP_ROOT/.venv/bin/python"
if [ ! -f "$PYTHON_PATH" ]; then
    PYTHON_PATH="$(which python3 2>/dev/null || which python 2>/dev/null || echo "python")"
    echo "   Warning: $WIP_ROOT/.venv/bin/python not found, using: $PYTHON_PATH"
fi

cat > "$APP_DIR/.mcp.json" << EOF
{
  "mcpServers": {
    "wip": {
      "command": "$PYTHON_PATH",
      "args": ["-m", "wip_mcp"],
      "cwd": "$WIP_ROOT",
      "env": {
$MCP_ENV
      }
    }
  }
}
EOF
echo "   Written: .mcp.json"

# --- Copy client libraries ---

echo "5. Copying client libraries..."
CLIENT_TARBALL=$(ls "$WIP_ROOT/libs/wip-client/"*.tgz 2>/dev/null | head -1)
REACT_TARBALL=$(ls "$WIP_ROOT/libs/wip-react/"*.tgz 2>/dev/null | head -1)

# Auto-build tarballs if missing and npm is available
if [ -z "$CLIENT_TARBALL" ] && command -v npm &>/dev/null; then
    echo "   Building @wip/client tarball..."
    (cd "$WIP_ROOT/libs/wip-client" && npm pack --quiet 2>/dev/null)
    CLIENT_TARBALL=$(ls "$WIP_ROOT/libs/wip-client/"*.tgz 2>/dev/null | head -1)
fi
if [ -z "$REACT_TARBALL" ] && command -v npm &>/dev/null; then
    echo "   Building @wip/react tarball..."
    (cd "$WIP_ROOT/libs/wip-react" && npm pack --quiet 2>/dev/null)
    REACT_TARBALL=$(ls "$WIP_ROOT/libs/wip-react/"*.tgz 2>/dev/null | head -1)
fi

if [ -n "$CLIENT_TARBALL" ]; then
    cp "$CLIENT_TARBALL" "$APP_DIR/libs/"
    if tar -xzf "$CLIENT_TARBALL" --to-stdout package/README.md > "$APP_DIR/libs/wip-client-README.md" 2>/dev/null; then
        echo "   Copied: $(basename "$CLIENT_TARBALL") + README"
    else
        rm -f "$APP_DIR/libs/wip-client-README.md"
        echo "   Copied: $(basename "$CLIENT_TARBALL") (README extraction failed)"
    fi
else
    echo "   Warning: @wip/client tarball not found in libs/wip-client/"
    echo "   Build with: cd $WIP_ROOT/libs/wip-client && npm pack"
fi

if [ -n "$REACT_TARBALL" ]; then
    cp "$REACT_TARBALL" "$APP_DIR/libs/"
    if tar -xzf "$REACT_TARBALL" --to-stdout package/README.md > "$APP_DIR/libs/wip-react-README.md" 2>/dev/null; then
        echo "   Copied: $(basename "$REACT_TARBALL") + README"
    else
        rm -f "$APP_DIR/libs/wip-react-README.md"
        echo "   Copied: $(basename "$REACT_TARBALL") (README extraction failed)"
    fi
else
    echo "   Warning: @wip/react tarball not found in libs/wip-react/"
    echo "   Build with: cd $WIP_ROOT/libs/wip-react && npm pack"
fi

# --- Generate CLAUDE.md ---

echo "6. Generating CLAUDE.md..."
cat > "$APP_DIR/CLAUDE.md" << EOF
# $APP_NAME

## What This App Does

> TODO: Describe what this app does in one paragraph.

## The Golden Rule

> **Never modify WIP. Build on top of it.**

WIP is the backend. This app is a frontend that maps a domain onto WIP's primitives (terminologies, templates, documents) and presents them to users.

## Process

Follow the 4-phase development process. Start with:

\`\`\`
/explore
\`\`\`

**Core phases** (in order):
1. \`/explore\` — Read MCP resources, discover existing data model, understand the domain
2. \`/design-model\` — Map the domain to WIP primitives (user must approve before proceeding)
3. \`/implement\` — Create terminologies and templates in WIP, verify with test documents
4. \`/build-app\` — Scaffold and build the React/TypeScript application

**After Phase 4:**
- \`/improve\` — Iterate (add features, fix bugs, refine UI)
- \`/document\` — Generate README, ARCHITECTURE, etc.

**Available at any time:**
- \`/wip-status\` — Check WIP service health and data state
- \`/export-model\` — Save data model to git as seed files
- \`/bootstrap\` — Recreate data model from seed files
- \`/add-app\` — Add a second app that cross-references the first
- \`/resume\` — Recover context after compaction or at start of a new session

**Context management:** When context reaches ~70-80%, the human should tell you to run \`/resume\` or save state (DESIGN.md, memory files) before compaction hits.

## Reference Documentation

Read these before starting:
- \`docs/AI-Assisted-Development.md\` — 4-phase process, data model design guide, PoNIFs quick reference
- \`docs/WIP_PoNIFs.md\` — Full guide to WIP's 6 non-intuitive behaviours
- \`docs/WIP_DevGuardrails.md\` — UI stack, app skeleton, testing conventions

## MCP

WIP is accessed exclusively via MCP tools (68 tools, 4 resources). Before starting:
- Read \`wip://conventions\` — bulk-first API, identity hashing, versioning
- Read \`wip://data-model\` — terminologies, templates, documents, fields, relationships
- Read \`wip://ponifs\` — 6 behaviours that trip up every new developer

\`wip://development-guide\` provides the full 4-phase workflow reference if needed.

## Client Libraries

For Phase 4 (app building), use @wip/client and @wip/react:
- \`libs/wip-client-README.md\` — TypeScript client (6 services, error hierarchy, bulk abstraction)
- \`libs/wip-react-README.md\` — React hooks (TanStack Query, 30+ hooks)

Install from tarballs in \`libs/\`:
\`\`\`bash
npm install ./libs/wip-client-*.tgz ./libs/wip-react-*.tgz
\`\`\`
EOF
echo "   Written: CLAUDE.md"

# --- Initialise git ---

echo "7. Initialising git repository..."
(cd "$APP_DIR" && git init -q && git add -A && git commit -q -m "Initial project setup for $APP_NAME

Generated by WIP create-app-project.sh from:
  $WIP_ROOT")
echo "   Git repo initialised with initial commit"

# --- Done ---

echo ""
echo "Done! Your app project is ready at: $APP_DIR"
echo ""
echo "Next steps:"
echo "  cd $APP_DIR"
echo "  claude          # Launch Claude Code"
echo "  /explore        # Start Phase 1"
echo ""
echo "Verify MCP connection:"
echo "  In Claude Code, run /mcp — you should see 68 tools and 4 resources."
