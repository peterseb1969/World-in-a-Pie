#!/usr/bin/env bash
#
# Create a new WIP app project directory with all required files.
#
# Usage:
#   ./scripts/create-app-project.sh /path/to/my-new-app [--name "My App"]
#   ./scripts/create-app-project.sh /path/to/my-new-app --preset query [--name "My App"]
#   ./scripts/create-app-project.sh --refresh /path/to/cloned-app
#
# This script:
#   1. Creates the directory structure
#   2. Copies slash commands from docs/slash-commands/app-builder/
#   3. Copies reference docs (AI-Assisted-Development.md, WIP_PoNIFs.md, WIP_DevGuardrails.md,
#      ontology-support.md, dev-delete.md)
#   4. Generates .mcp.json pointing to this WIP installation
#   5. Copies and extracts client library tarballs + READMEs
#   6. Copies wip-toolkit wheel and dev-delete.py
#   7. Generates a starter CLAUDE.md
#   8. Initialises a git repo
#
# --refresh mode (for cloned/existing apps):
#   Only regenerates .mcp.json and refreshes libs/tools. Does NOT touch
#   CLAUDE.md, slash commands, docs, or git. Use after cloning an app repo
#   on a new machine where the WIP installation path differs.
#
# The generated .mcp.json uses WIP_API_KEY_FILE instead of a hardcoded key,
# so API key rotation in WIP automatically applies to all apps.

set -euo pipefail

WIP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# --- Parse arguments ---

APP_DIR=""
APP_NAME=""
PRESET="standard"
REFRESH_MODE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --name)
            APP_NAME="$2"
            shift 2
            ;;
        --preset)
            PRESET="$2"
            shift 2
            ;;
        --refresh)
            REFRESH_MODE=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 <app-directory> [--name \"App Name\"] [--preset standard|query]"
            echo "       $0 --refresh <existing-app-directory>"
            echo ""
            echo "Creates a new WIP app project with all required files."
            echo ""
            echo "Options:"
            echo "  --name      Display name for the app (default: derived from directory name)"
            echo "  --preset    Project preset: 'standard' (default) or 'query' (NL query app)"
            echo "  --refresh   Refresh machine-specific files (.mcp.json, libs) in an existing app"
            echo "  -h          Show this help"
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
    echo "Usage: $0 <app-directory> [--name \"App Name\"] [--preset standard|query]"
    exit 1
fi

if [[ "$PRESET" != "standard" && "$PRESET" != "query" ]]; then
    echo "Error: Unknown preset '$PRESET'. Use 'standard' or 'query'."
    exit 1
fi

# Resolve to absolute path
APP_DIR="$(cd "$(dirname "$APP_DIR")" 2>/dev/null && pwd)/$(basename "$APP_DIR")" || APP_DIR="$(pwd)/$APP_DIR"

# Derive app name from directory if not provided
if [ -z "$APP_NAME" ]; then
    APP_NAME="$(basename "$APP_DIR" | sed 's/[-_]/ /g' | awk '{for(i=1;i<=NF;i++) $i=toupper(substr($i,1,1)) substr($i,2)}1')"
fi

if $REFRESH_MODE; then
    echo "Refreshing WIP app environment:"
else
    echo "Creating WIP app project:"
fi
echo "  Directory: $APP_DIR"
echo "  App name:  $APP_NAME"
echo "  Preset:    $PRESET"
echo "  WIP root:  $WIP_ROOT"
echo ""

# --- Check prerequisites ---

if $REFRESH_MODE; then
    if [ ! -d "$APP_DIR" ]; then
        echo "Error: $APP_DIR does not exist. Use --refresh on an existing app directory."
        exit 1
    fi
    if [ ! -f "$APP_DIR/CLAUDE.md" ]; then
        echo "Warning: $APP_DIR/CLAUDE.md not found — this may not be a WIP app project."
    fi
else
    if [ -d "$APP_DIR" ] && [ "$(ls -A "$APP_DIR" 2>/dev/null)" ]; then
        echo "Error: $APP_DIR already exists and is not empty."
        echo "Choose a new directory or remove the existing one."
        exit 1
    fi

    if [ ! -d "$WIP_ROOT/docs/slash-commands/app-builder" ]; then
        echo "Error: $WIP_ROOT/docs/slash-commands/app-builder/ not found."
        echo "Run this script from the WIP project root."
        exit 1
    fi
fi

# --- Create directory structure (new projects only) ---

if ! $REFRESH_MODE; then
    echo "1. Creating directory structure..."
    mkdir -p "$APP_DIR/.claude/commands"
    mkdir -p "$APP_DIR/docs"
fi
mkdir -p "$APP_DIR/libs"
mkdir -p "$APP_DIR/tools"

# --- Copy slash commands (new projects only) ---

if ! $REFRESH_MODE; then
    echo "2. Copying slash commands (12 files)..."
    cp "$WIP_ROOT/docs/slash-commands/app-builder/"*.md "$APP_DIR/.claude/commands/"
    echo "   Copied: $(find "$APP_DIR/.claude/commands/" -maxdepth 1 -type f | wc -l | tr -d ' ') commands"

    # --- Copy reference docs (new projects only) ---

    echo "3. Copying reference documentation..."
    for doc in AI-Assisted-Development.md WIP_PoNIFs.md WIP_DevGuardrails.md dev-delete.md; do
        if [ -f "$WIP_ROOT/docs/$doc" ]; then
            cp "$WIP_ROOT/docs/$doc" "$APP_DIR/docs/"
            echo "   Copied: docs/$doc"
        else
            echo "   Warning: docs/$doc not found, skipping"
        fi
    done
    # Design docs live in a subdirectory
    if [ -f "$WIP_ROOT/docs/design/ontology-support.md" ]; then
        cp "$WIP_ROOT/docs/design/ontology-support.md" "$APP_DIR/docs/"
        echo "   Copied: docs/design/ontology-support.md"
    else
        echo "   Warning: docs/design/ontology-support.md not found, skipping"
    fi
fi

# --- Generate .mcp.json ---

if $REFRESH_MODE; then
    echo "1. Regenerating .mcp.json..."
else
    echo "4. Generating .mcp.json..."
fi

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

# Validate that a tarball contains compiled output (dist/*.js), not just metadata.
# This catches the four-time offender: npm pack run without npm run build first.
# The prepack hook in each library's package.json should prevent this, but belt-and-suspenders.
validate_tarball() {
    local tarball="$1"
    local lib_name="$2"
    if [ -z "$tarball" ]; then return 1; fi
    local js_count
    js_count=$(tar -tzf "$tarball" 2>/dev/null | grep -c 'dist/.*\.js$' || true)
    if [ "$js_count" -eq 0 ]; then
        echo "   ERROR: $lib_name tarball contains no compiled JS in dist/"
        echo "   Fix: cd $WIP_ROOT/libs/$lib_name && npm run build && npm pack"
        return 1
    fi
    return 0
}

if $REFRESH_MODE; then
    echo "2. Refreshing client libraries..."
else
    echo "5. Copying client libraries..."
fi
MISSING_LIBS=()
CLIENT_TARBALL=$(find "$WIP_ROOT/libs/wip-client/" -maxdepth 1 -name '*.tgz' -type f 2>/dev/null | head -1)
REACT_TARBALL=$(find "$WIP_ROOT/libs/wip-react/" -maxdepth 1 -name '*.tgz' -type f 2>/dev/null | head -1)
PROXY_TARBALL=$(find "$WIP_ROOT/libs/wip-proxy/" -maxdepth 1 -name '*.tgz' -type f 2>/dev/null | head -1)

# Auto-build tarballs if missing or invalid
# Fresh clones have no node_modules, so we must npm install before npm pack
# NOTE: Only the final 'find' line goes to stdout (captured by caller).
# All progress/error messages go to stderr so they display without polluting the return value.
rebuild_tarball() {
    local lib_dir="$1"
    local lib_name="$2"
    echo "   Building $lib_name tarball..." >&2
    if ! (cd "$lib_dir" && npm install --quiet 2>&1 && npm pack --quiet 2>&1) >&2; then
        echo "   Warning: failed to build $lib_name tarball" >&2
        return 1
    fi
    find "$lib_dir" -maxdepth 1 -name '*.tgz' -type f 2>/dev/null | head -1
}

if command -v npm &>/dev/null; then
    if [ -z "$CLIENT_TARBALL" ] || ! validate_tarball "$CLIENT_TARBALL" "wip-client"; then
        CLIENT_TARBALL=$(rebuild_tarball "$WIP_ROOT/libs/wip-client" "@wip/client")
    fi
    if [ -z "$REACT_TARBALL" ] || ! validate_tarball "$REACT_TARBALL" "wip-react"; then
        REACT_TARBALL=$(rebuild_tarball "$WIP_ROOT/libs/wip-react" "@wip/react")
    fi
    if [ -z "$PROXY_TARBALL" ] || ! validate_tarball "$PROXY_TARBALL" "wip-proxy"; then
        PROXY_TARBALL=$(rebuild_tarball "$WIP_ROOT/libs/wip-proxy" "@wip/proxy")
    fi
else
    echo "   npm not found — cannot auto-build tarballs"
fi

# Copy and validate each tarball
copy_tarball() {
    local tarball="$1"
    local lib_name="$2"
    local readme_name="$3"

    if [ -z "$tarball" ]; then
        MISSING_LIBS+=("$lib_name")
        return
    fi

    if ! validate_tarball "$tarball" "$lib_name"; then
        MISSING_LIBS+=("$lib_name (tarball has no dist/ — run npm run build first)")
        return
    fi

    cp "$tarball" "$APP_DIR/libs/"
    if tar -xzf "$tarball" --to-stdout package/README.md > "$APP_DIR/libs/$readme_name" 2>/dev/null; then
        echo "   Copied: $(basename "$tarball") + README"
    else
        rm -f "$APP_DIR/libs/$readme_name"
        echo "   Copied: $(basename "$tarball") (README extraction failed)"
    fi
}

copy_tarball "$CLIENT_TARBALL" "@wip/client" "wip-client-README.md"
copy_tarball "$REACT_TARBALL" "@wip/react" "wip-react-README.md"
copy_tarball "$PROXY_TARBALL" "@wip/proxy" "wip-proxy-README.md"

# --- Copy wip-toolkit and dev-delete.py ---

if $REFRESH_MODE; then
    echo "3. Refreshing wip-toolkit and dev-delete.py..."
else
    echo "6. Copying wip-toolkit and dev-delete.py..."
fi

# wip-toolkit wheel
TOOLKIT_WHEEL=$(find "$WIP_ROOT/WIP-Toolkit/dist/" -maxdepth 1 -name '*.whl' -type f 2>/dev/null | head -1 || true)
if [ -z "$TOOLKIT_WHEEL" ]; then
    # Try to build it — prefer WIP's venv (has `build` installed)
    if [ -f "$WIP_ROOT/.venv/bin/python" ]; then
        TOOLKIT_PYTHON="$WIP_ROOT/.venv/bin/python"
    elif command -v python3 &>/dev/null; then
        TOOLKIT_PYTHON="$(command -v python3)"
    elif command -v python &>/dev/null; then
        TOOLKIT_PYTHON="$(command -v python)"
    else
        TOOLKIT_PYTHON=""
    fi
    if [ -n "$TOOLKIT_PYTHON" ]; then
        echo "   Building wip-toolkit wheel (using $TOOLKIT_PYTHON)..."
        if ! (cd "$WIP_ROOT/WIP-Toolkit" && "$TOOLKIT_PYTHON" -m build . --wheel -q 2>&1); then
            echo "   Warning: wheel build failed — ensure 'build' is installed:"
            echo "            $TOOLKIT_PYTHON -m pip install build"
        fi
        TOOLKIT_WHEEL=$(find "$WIP_ROOT/WIP-Toolkit/dist/" -maxdepth 1 -name '*.whl' -type f 2>/dev/null | head -1 || true)
    fi
fi

if [ -n "$TOOLKIT_WHEEL" ]; then
    cp "$TOOLKIT_WHEEL" "$APP_DIR/libs/"
    echo "   Copied: $(basename "$TOOLKIT_WHEEL")"
else
    echo "   Warning: wip-toolkit wheel not found. Build it with:"
    echo "            cd $WIP_ROOT/WIP-Toolkit && $WIP_ROOT/.venv/bin/python -m build . --wheel"
fi

# dev-delete.py
if [ -f "$WIP_ROOT/scripts/dev-delete.py" ]; then
    cp "$WIP_ROOT/scripts/dev-delete.py" "$APP_DIR/tools/"
    echo "   Copied: tools/dev-delete.py"
else
    echo "   Warning: scripts/dev-delete.py not found"
fi

# --- Copy query scaffold files (--preset query only, new projects only) ---

if ! $REFRESH_MODE && [ "$PRESET" = "query" ]; then
    SCAFFOLD_DIR="$WIP_ROOT/scripts/scaffold-query"
    if [ ! -d "$SCAFFOLD_DIR" ]; then
        echo "Error: Scaffold template directory not found: $SCAFFOLD_DIR"
        exit 1
    fi

    echo "7. Copying NL query scaffold..."

    # Derive slug from app name (lowercase, hyphens)
    APP_SLUG="$(echo "$APP_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/ /-/g')"

    # Copy scaffold structure
    cp -r "$SCAFFOLD_DIR/server" "$APP_DIR/"
    cp -r "$SCAFFOLD_DIR/src" "$APP_DIR/"
    cp "$SCAFFOLD_DIR/package.json" "$APP_DIR/"
    cp "$SCAFFOLD_DIR/tsconfig.json" "$APP_DIR/"
    cp "$SCAFFOLD_DIR/vite.config.ts" "$APP_DIR/"
    cp "$SCAFFOLD_DIR/tailwind.config.js" "$APP_DIR/"
    cp "$SCAFFOLD_DIR/postcss.config.js" "$APP_DIR/"
    cp "$SCAFFOLD_DIR/index.html" "$APP_DIR/"
    cp "$SCAFFOLD_DIR/.env.example" "$APP_DIR/"
    cp "$SCAFFOLD_DIR/.gitignore" "$APP_DIR/.gitignore.scaffold"

    # Merge .gitignore (scaffold additions)
    if [ -f "$APP_DIR/.gitignore" ]; then
        cat "$APP_DIR/.gitignore.scaffold" >> "$APP_DIR/.gitignore"
    else
        mv "$APP_DIR/.gitignore.scaffold" "$APP_DIR/.gitignore"
    fi
    rm -f "$APP_DIR/.gitignore.scaffold"

    # Replace placeholders
    sed -i '' "s/SCAFFOLD_APP_SLUG/$APP_SLUG/g" "$APP_DIR/package.json"
    sed -i '' "s/SCAFFOLD_APP_NAME/$APP_NAME/g" "$APP_DIR/index.html"

    # Update .env.example with actual paths
    sed -i '' "s|/path/to/WorldInPie|$WIP_ROOT|g" "$APP_DIR/.env.example"

    echo "   Copied: server/ (agent.ts, index.ts, prompts/)"
    echo "   Copied: src/ (App.tsx, AskBar.tsx, HomePage.tsx)"
    echo "   Copied: package.json, tsconfig.json, vite.config.ts, tailwind, .env.example"
    echo "   App slug: $APP_SLUG"

    STEP_OFFSET=1
else
    STEP_OFFSET=0
fi

# --- Generate CLAUDE.md (new projects only) ---

if ! $REFRESH_MODE; then
STEP_NUM=$((7 + STEP_OFFSET))
echo "$STEP_NUM. Generating CLAUDE.md..."
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
- \`/report\` — Capture fireside chat or trigger session summary

**Context management:** When context reaches ~70-80%, the human should tell you to run \`/resume\` or save state (DESIGN.md, memory files) before compaction hits.

## Reference Documentation

Read these before starting:
- \`docs/AI-Assisted-Development.md\` — 4-phase process, data model design guide, PoNIFs quick reference
- \`docs/WIP_PoNIFs.md\` — Full guide to WIP's 6 non-intuitive behaviours
- \`docs/WIP_DevGuardrails.md\` — UI stack, app skeleton, testing conventions
- \`docs/ontology-support.md\` — Term relationships, polyhierarchy, typed relationships, traversal queries
- \`docs/dev-delete.md\` — Hard-delete entities during development (modes, backends, remote usage)

## MCP

WIP is accessed exclusively via MCP tools (70 tools, 5 resources). Before starting:
- Read \`wip://conventions\` — bulk-first API, identity hashing, versioning
- Read \`wip://data-model\` — terminologies, templates, documents, fields, relationships
- Read \`wip://ponifs\` — 6 behaviours that trip up every new developer

\`wip://development-guide\` provides the full 4-phase workflow reference if needed.
\`wip://query-assistant-prompt\` provides a complete system prompt for NL query agents (used by --preset query apps).

## Client Libraries

For Phase 4 (app building), use @wip/client, @wip/react, and @wip/proxy:
- \`libs/wip-client-README.md\` — TypeScript client (6 services, error hierarchy, bulk abstraction)
- \`libs/wip-react-README.md\` — React hooks (TanStack Query, 30+ hooks)
- \`libs/wip-proxy-README.md\` — Express middleware for WIP API proxying with auth injection

Install from tarballs in \`libs/\`:
\`\`\`bash
npm install ./libs/wip-client-*.tgz ./libs/wip-react-*.tgz ./libs/wip-proxy-*.tgz
\`\`\`

## WIP Toolkit

\`wip-toolkit\` is a CLI for backup, export, import, and data migration. Install from the wheel in \`libs/\`:

\`\`\`bash
pip install libs/wip_toolkit-*.whl
\`\`\`

Key commands:
- \`wip-toolkit export <namespace> <output.zip>\` — Export namespace to archive
- \`wip-toolkit import <archive.zip> --mode fresh\` — Import with new IDs (cross-namespace)
- \`wip-toolkit import <archive.zip> --mode restore\` — Restore with original IDs (disaster recovery)

Remote WIP instances:
\`\`\`bash
wip-toolkit --host pi-poe-8gb.local --proxy export wip /tmp/backup.zip
\`\`\`

## Dev Delete

\`tools/dev-delete.py\` hard-deletes entities during iterative development. See \`docs/dev-delete.md\` for full usage.

\`\`\`bash
# Dry run (default)
python tools/dev-delete.py --namespace myapp

# Actually delete
python tools/dev-delete.py --namespace myapp --force

# Remote MongoDB
python tools/dev-delete.py --mongo-uri mongodb://remote-host:27017/ --namespace myapp --force
\`\`\`

Requires \`pymongo\`. For file/reporting cleanup also install \`boto3\` and \`psycopg2-binary\`.

## YAC Reporting

You are a YAC (Yet Another Claude). You report your work to the Field Reporter by writing files to a shared directory. This reporting is also useful for the *next* YAC — your session reports are input for future agents resuming your work.

**Getting the current time:** Always use \`date '+%Y-%m-%d %H:%M'\` for timestamps. Do not guess.

**Off the record:** If Peter says "off the record" or "don't report this," skip reporting for that segment. Resume when told.

### Session Identity

At the start of every session, run \`date '+%Y%m%d-%H%M'\` and assign yourself a session ID using your app prefix:

| App | Prefix |
|-----|--------|
| Statement Manager | \`APP-SM\` |
| Receipt Scanner | \`APP-RS\` |
| D&D Compendium | \`APP-DND\` |
| ClinTrial Explorer | \`APP-CT\` |
| New apps | \`APP-<SHORT>\` (pick a 2-4 letter code, tell the user) |

Format: \`<PREFIX>-YYYYMMDD-HHMM\`. Example: \`APP-CT-20260331-2015\`.

### Report Directory

Create your report directory at the start of every session:

\`\`\`bash
mkdir -p /Users/peter/Development/FR-YAC/reports/<PREFIX>-YYYYMMDD-HHMM/
\`\`\`

### Resuming — Check Previous Sessions

At session start (and when running \`/resume\`), check for recent sessions with your prefix:

\`\`\`bash
ls -d /Users/peter/Development/FR-YAC/reports/<PREFIX>-* 2>/dev/null | tail -1
\`\`\`

If a previous session exists, read its \`session.md\` to recover context from the previous agent's work. This is faster and richer than reconstructing from git alone.

### Session Start

Create \`session.md\` immediately when starting work:

\`\`\`markdown
---
session: <PREFIX>-YYYYMMDD-HHMM
type: app
app: <app name>
repo: <repo directory name>
started: YYYY-MM-DD HH:MM
phase: <explore | design-model | implement | build-app | improve | other>
tasks:
  - <initial task from user>
---
\`\`\`

### After Every Commit

Before appending, read \`commits.md\` first. If the commit hash is already listed, skip it (prevents duplicates after context compaction).

Append to \`commits.md\` in your report directory:

\`\`\`markdown
## <short-hash> — <commit message>
**Time:** <run \`date '+%H:%M'\`>
**Files:** <count> changed, +<added>/-<removed>
**Tests:** <X passed, Y failed — or "not run">
**What:** <1-2 sentences — what changed>
**Why:** <1-2 sentences — what motivated this change>
**PoNIF:** <if you encountered a PoNIF — which one and whether it caused issues. Omit if none.>
**Discovered:** <anything surprising, bugs found, or gaps identified — omit if nothing>
\`\`\`

If you encountered a PoNIF and handled it correctly, note which one. If you hit a PoNIF and it caused a bug, definitely note it — the Field Reporter tracks these patterns.

### Session Summary

Write the session summary to \`session.md\` when:
- Peter runs \`/report session-end\`
- You detect context is running low (~70-80%)
- The session is naturally ending

Update (overwrite) the summary section — don't append multiple summaries.

\`\`\`markdown
## Session Summary
**Duration:** <start time> – <run \`date '+%H:%M'\`>
**Commits:** <count>
**Lines:** +<added>/-<removed>
**Phase:** <which phase(s) you worked in>
**What happened:** <3-5 sentences covering the session's arc — not a commit list, but the narrative>
**WIP interactions:** <any platform bugs, missing MCP tools, or upstream issues discovered — omit if none>
**Unfinished:** <what's left, if anything>
**For the next YAC:** <context the next agent needs to pick up where you left off>
\`\`\`

### Fireside Chats

When Peter initiates a design discussion, architecture debate, or scope conversation, use the \`/report\` slash command to capture it. These are the high-value narrative moments — not just what was decided, but why, what alternatives were considered, and what Peter said.
EOF
echo "   Written: CLAUDE.md"

# --- Initialise git ---

STEP_NUM=$((8 + STEP_OFFSET))
echo "$STEP_NUM. Initialising git repository..."
(cd "$APP_DIR" && git init -q && git add -A && git commit -q -m "Initial project setup for $APP_NAME

Generated by WIP create-app-project.sh from:
  $WIP_ROOT")
echo "   Git repo initialised with initial commit"
fi  # end of ! $REFRESH_MODE block (CLAUDE.md + git init)

# --- Done ---

echo ""
if $REFRESH_MODE; then
    echo "Done! Environment refreshed at: $APP_DIR"
else
    echo "Done! Your app project is ready at: $APP_DIR"
fi
echo ""

# --- Prominent warning if client libraries are missing ---

if [ ${#MISSING_LIBS[@]} -gt 0 ]; then
    echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    echo "!!                                                              !!"
    echo "!!  CLIENT LIBRARIES MISSING — APP BUILDING WILL NOT WORK       !!"
    echo "!!                                                              !!"
    echo "!!  Without these libraries, Claude falls back to raw API       !!"
    echo "!!  calls instead of using the typed client SDK.                !!"
    echo "!!                                                              !!"
    echo "!!  Missing: ${MISSING_LIBS[*]}"
    echo "!!                                                              !!"
    echo "!!  Fix: install npm, then run from the WIP directory:          !!"
    echo "!!                                                              !!"
    echo "!!    cd $WIP_ROOT/libs/wip-client && npm pack"
    echo "!!    cd $WIP_ROOT/libs/wip-react && npm pack"
    echo "!!    # Then re-run this script                                 !!"
    echo "!!                                                              !!"
    echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    echo ""
fi

if $REFRESH_MODE; then
    echo "Next steps:"
    echo "  cd $APP_DIR"
    echo "  claude          # Launch Claude Code"
    echo "  /resume         # Recover context from existing code and docs"
    echo ""
    echo "Verify MCP connection:"
    echo "  In Claude Code, run /mcp — you should see 70 tools and 5 resources."
    echo ""
    echo "Note: .mcp.json has been regenerated with paths for this machine."
    echo "      Add it to .gitignore if you don't want to commit machine-specific paths."
else
    echo "Next steps:"
    echo "  cd $APP_DIR"
    echo "  claude          # Launch Claude Code"
    echo "  /explore        # Start Phase 1"
    echo ""
    echo "Verify MCP connection:"
    echo "  In Claude Code, run /mcp — you should see 70 tools and 5 resources."
fi
