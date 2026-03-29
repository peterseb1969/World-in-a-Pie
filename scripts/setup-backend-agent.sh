#!/usr/bin/env bash
#
# Set up a cloned WIP repo for a backend coding agent.
#
# Usage:
#   ./scripts/setup-backend-agent.sh [--target local|ssh|http] [--host HOST] [--cert CERT_PATH]
#
# This script:
#   1. Sets up Python venv with a compatible Python (3.11-3.13)
#   2. Generates .mcp.json for the chosen transport
#   3. Generates a backend-focused CLAUDE.md
#   4. Copies backend slash commands to .claude/commands/
#   5. Verifies MCP connectivity
#

set -euo pipefail

WIP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# --- Parse arguments ---

TARGET="local"
HOST=""
CERT_PATH=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --target)
            TARGET="$2"
            shift 2
            ;;
        --host)
            HOST="$2"
            shift 2
            ;;
        --cert)
            CERT_PATH="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [--target local|ssh|http] [--host HOST] [--cert CERT_PATH]"
            echo ""
            echo "Set up a WIP repo for a backend coding agent."
            echo ""
            echo "Targets:"
            echo "  local   MCP via stdio to local venv (default)"
            echo "  ssh     MCP via SSH stdio proxy to remote host"
            echo "  http    MCP via HTTP/HTTPS transport to remote host"
            echo ""
            echo "Options:"
            echo "  --host HOST       Remote hostname (required for ssh/http)"
            echo "  --cert CERT_PATH  TLS cert for self-signed HTTPS (auto-detects from data/secrets/)"
            echo "  -h, --help        Show this help"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Run $0 --help for usage."
            exit 1
            ;;
    esac
done

# Validate arguments
if [[ "$TARGET" == "ssh" || "$TARGET" == "http" ]] && [[ -z "$HOST" ]]; then
    echo "Error: --host is required for --target $TARGET"
    exit 1
fi

echo "Setting up backend agent:"
echo "  WIP root:  $WIP_ROOT"
echo "  Target:    $TARGET"
[[ -n "$HOST" ]] && echo "  Host:      $HOST"
[[ -n "$CERT_PATH" ]] && echo "  Cert:      $CERT_PATH"
echo ""

# --- Helper: find a compatible Python (3.11-3.13) ---

find_compatible_python() {
    # Prefer specific versions known to work, newest first
    for cmd in python3.13 python3.12 python3.11; do
        local p
        p="$(command -v "$cmd" 2>/dev/null)" || continue
        if [ -n "$p" ]; then
            echo "$p"
            return 0
        fi
    done

    # Fall back to python3 if it's a compatible version
    local p
    p="$(command -v python3 2>/dev/null)" || true
    if [ -n "$p" ]; then
        local ver
        ver="$("$p" -c 'import sys; print(f"{sys.version_info.minor}")' 2>/dev/null)" || true
        if [ -n "$ver" ] && [ "$ver" -ge 11 ] && [ "$ver" -le 13 ]; then
            echo "$p"
            return 0
        fi
    fi

    return 1
}

# --- 1. Set up Python venv (if missing) ---

echo "1. Checking Python venv..."
if [ -d "$WIP_ROOT/.venv" ] && [ -f "$WIP_ROOT/.venv/bin/python" ]; then
    VENV_PYTHON="$WIP_ROOT/.venv/bin/python"
    VENV_VERSION="$("$VENV_PYTHON" --version 2>&1)"
    echo "   Venv exists: $VENV_VERSION"
else
    # Find a compatible Python
    SYSTEM_PYTHON=""
    if SYSTEM_PYTHON="$(find_compatible_python)"; then
        SYSTEM_VERSION="$("$SYSTEM_PYTHON" --version 2>&1)"
        echo "   Using $SYSTEM_VERSION ($SYSTEM_PYTHON)"
    else
        echo ""
        echo "   !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        echo "   !!  NO COMPATIBLE PYTHON FOUND                        !!"
        echo "   !!                                                     !!"
        echo "   !!  WIP requires Python 3.11, 3.12, or 3.13.          !!"
        echo "   !!  Python 3.14+ is not yet supported.                !!"
        echo "   !!                                                     !!"
        echo "   !!  Install a compatible version:                      !!"
        echo "   !!    macOS:  brew install python@3.13                 !!"
        echo "   !!    Linux:  apt install python3.13 (or similar)      !!"
        echo "   !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        echo ""
        exit 1
    fi

    echo "   Creating venv..."
    "$SYSTEM_PYTHON" -m venv "$WIP_ROOT/.venv"
    VENV_PYTHON="$WIP_ROOT/.venv/bin/python"

    # shellcheck disable=SC1091
    source "$WIP_ROOT/.venv/bin/activate"

    # Upgrade pip and setuptools — fresh venvs bundle old versions that may not
    # support modern pyproject.toml build backends
    pip install --upgrade pip setuptools -q 2>/dev/null || true

    # Install MCP server and its dependencies — this is critical for MCP connectivity
    MCP_INSTALL_OK=false
    if [ -f "$WIP_ROOT/components/mcp-server/pyproject.toml" ]; then
        echo "   Installing MCP server dependencies..."
        if pip install -e "$WIP_ROOT/components/mcp-server/" -q 2>&1; then
            MCP_INSTALL_OK=true
            echo "   MCP server installed successfully"
        fi
    fi

    if [ "$MCP_INSTALL_OK" = false ]; then
        echo ""
        echo "   !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        echo "   !!  MCP SERVER INSTALL FAILED                          !!"
        echo "   !!                                                     !!"
        echo "   !!  Without this, Claude cannot connect to WIP.        !!"
        echo "   !!  Fix manually:                                      !!"
        echo "   !!    source .venv/bin/activate                        !!"
        echo "   !!    pip install -e components/mcp-server/            !!"
        echo "   !!                                                     !!"
        echo "   !!  Then run /setup in Claude to verify.               !!"
        echo "   !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        echo ""
    fi

    # Install test dependencies
    pip install pytest ruff mypy -q 2>/dev/null || true

    echo "   Venv created"
fi

# --- 2. Generate .mcp.json ---

echo "2. Generating .mcp.json..."

# Determine API key
API_KEY=""
if [[ "$TARGET" == "local" ]]; then
    if [ -f "$WIP_ROOT/.env" ]; then
        API_KEY=$(grep "^API_KEY=" "$WIP_ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2-)
    fi
    API_KEY="${API_KEY:-dev_master_key_for_testing}"
    echo "   API key: sourced from .env (${#API_KEY} chars)"
else
    echo -n "   Enter API key for $HOST: "
    read -r API_KEY
    if [[ -z "$API_KEY" ]]; then
        echo "   Error: API key is required for remote targets."
        exit 1
    fi
fi

case "$TARGET" in
    local)
        cat > "$WIP_ROOT/.mcp.json" << EOF
{
  "mcpServers": {
    "wip": {
      "command": "$VENV_PYTHON",
      "args": ["-m", "wip_mcp"],
      "cwd": "$WIP_ROOT",
      "env": {
        "WIP_API_KEY": "$API_KEY",
        "PYTHONPATH": "$WIP_ROOT/components/mcp-server/src"
      }
    }
  }
}
EOF
        echo "   Written: .mcp.json (stdio, local — $VENV_PYTHON)"
        ;;

    ssh)
        echo -n "   SSH user [$USER]: "
        read -r SSH_USER
        SSH_USER="${SSH_USER:-$USER}"

        echo -n "   WIP install path on $HOST [/home/$SSH_USER/World-in-a-Pie]: "
        read -r REMOTE_PATH
        REMOTE_PATH="${REMOTE_PATH:-/home/$SSH_USER/World-in-a-Pie}"

        cat > "$WIP_ROOT/.mcp.json" << EOF
{
  "mcpServers": {
    "wip": {
      "command": "ssh",
      "args": [
        "-o", "StrictHostKeyChecking=no",
        "$SSH_USER@$HOST",
        "cd $REMOTE_PATH && source .venv/bin/activate && PYTHONPATH=components/mcp-server/src REGISTRY_URL=http://localhost:8001 DEF_STORE_URL=http://localhost:8002 TEMPLATE_STORE_URL=http://localhost:8003 DOCUMENT_STORE_URL=http://localhost:8004 API_KEY=$API_KEY python -m wip_mcp"
      ]
    }
  }
}
EOF
        echo "   Written: .mcp.json (stdio via SSH to $SSH_USER@$HOST)"
        ;;

    http)
        # Determine URL scheme and port
        URL="https://$HOST/mcp"
        echo "   MCP URL: $URL"

        # Auto-detect TLS cert if not specified
        if [[ -z "$CERT_PATH" ]]; then
            for crt in "$WIP_ROOT/data/secrets/"*.crt; do
                if [[ -f "$crt" ]]; then
                    CERT_PATH="$crt"
                    echo "   Auto-detected cert: $CERT_PATH"
                    break
                fi
            done
        fi

        # Build .mcp.json with optional cert
        if [[ -n "$CERT_PATH" ]]; then
            cat > "$WIP_ROOT/.mcp.json" << EOF
{
  "mcpServers": {
    "wip": {
      "type": "http",
      "url": "$URL",
      "headers": {
        "X-API-Key": "$API_KEY"
      },
      "env": {
        "NODE_EXTRA_CA_CERTS": "$CERT_PATH"
      }
    }
  }
}
EOF
        else
            cat > "$WIP_ROOT/.mcp.json" << EOF
{
  "mcpServers": {
    "wip": {
      "type": "http",
      "url": "$URL",
      "headers": {
        "X-API-Key": "$API_KEY"
      }
    }
  }
}
EOF
        fi
        echo "   Written: .mcp.json (HTTP to $HOST)"
        ;;
esac

# --- 3. Generate CLAUDE.md ---

echo "3. Generating CLAUDE.md..."
cat > "$WIP_ROOT/CLAUDE.md" << 'CLAUDEEOF'
# WIP — Backend Development

## What Is WIP

WIP is a universal template-driven document storage system. It runs on anything from a Raspberry Pi 5 (8GB) to cloud infrastructure. Users define terminologies and templates, then store validated documents against those templates. A reporting pipeline syncs data to PostgreSQL for analytics.

## Getting Started

1. Run `/setup` — verify environment (venv, containers, MCP connectivity)
2. Run `/wip-status` — check service health and data state
3. Run `/roadmap` — see current priorities
4. Run `/understand <component>` — deep-dive into what you're working on

## Essential Reading

- `docs/api-conventions.md` — bulk-first API, BulkResponse contract
- `docs/uniqueness-and-identity.md` — Registry, identity hashing, composite keys
- `docs/development-guide.md` — running tests, quality audit, seed data
- `docs/change-propagation-checklist.md` — what to update when adding/changing fields or features
- `docs/design/ontology-support.md` — term relationships
- MCP resource `wip://ponifs` — 6 non-intuitive behaviours

## Architecture

| Service | Port | Purpose |
|---------|------|---------|
| Registry | 8001 | ID generation, namespace management, synonyms |
| Def-Store | 8002 | Terminologies, terms, aliases, ontology relationships |
| Template-Store | 8003 | Document schemas, field definitions, inheritance, draft mode |
| Document-Store | 8004 | Document CRUD, versioning, term validation, file storage, CSV/XLSX import |
| Reporting-Sync | 8005 | MongoDB → PostgreSQL sync via NATS events |
| Ingest Gateway | 8006 | Async bulk ingestion via NATS JetStream |
| MCP Server | stdio/SSE | 69 tools, 4 resources for AI-assisted development |
| WIP Console | 8443 | Vue 3 + PrimeVue UI (served via Caddy reverse proxy) |

**Infrastructure:** MongoDB (primary store), PostgreSQL (reporting), NATS JetStream (events), MinIO (files), Caddy (proxy/TLS), Dex (OIDC)

**Libraries:** wip-auth (Python, `libs/wip-auth/`), @wip/client (TypeScript, `libs/wip-client/`), @wip/react (React hooks, `libs/wip-react/`)

See `docs/architecture.md` for full details.

## Key Conventions

- **Bulk-first API:** Every write endpoint accepts `List[ItemRequest]`, returns `BulkResponse`. Always HTTP 200 — errors are per-item. See `docs/api-conventions.md`.
- **Synonym resolution:** APIs accept human-readable synonyms wherever IDs are expected. UUIDs pass through. See `docs/design/universal-synonym-resolution.md`.
- **Stable IDs:** `entity_id` stays the same across versions; `(entity_id, version)` is the unique key. See `docs/uniqueness-and-identity.md`.

## Commands

| Command | Purpose |
|---------|---------|
| `/setup` | First-run environment check and guided setup |
| `/resume` | Recover context after compaction or new session |
| `/wip-status` | Check service health and data state |
| `/understand` | Deep-dive into a component or library |
| `/test` | Run component tests |
| `/quality` | Run quality audit |
| `/review-changes` | Analyze uncommitted work |
| `/pre-commit` | CI-equivalent checks |
| `/roadmap` | Show project priorities |

## File Structure

```
WorldInPie/
├── CLAUDE.md                 # This file (generated by setup-backend-agent.sh)
├── docs/                     # Documentation (architecture, APIs, security, design specs)
│   ├── design/               # Feature design documents
│   ├── security/             # Security docs (key rotation, encryption at rest)
│   └── slash-commands/       # Slash command sources (app-builder/, backend/)
├── scripts/                  # Setup, security, quality audit, seed data
├── config/                   # Caddy, Dex, presets, API key configs
├── libs/
│   ├── wip-auth/             # Shared Python auth library
│   ├── wip-client/           # @wip/client TypeScript library
│   └── wip-react/            # @wip/react hooks library
├── components/
│   ├── registry/             # ID & namespace management
│   ├── def-store/            # Terminologies & terms
│   ├── template-store/       # Document schemas
│   ├── document-store/       # Document storage, files, import, replay
│   ├── reporting-sync/       # PostgreSQL sync
│   ├── ingest-gateway/       # Async ingestion via NATS
│   ├── mcp-server/           # MCP server (69 tools, 4 resources)
│   └── seed_data/            # Test data generation
├── docker-compose/           # Modular compose: base.yml + modules/
├── k8s/                      # Kubernetes manifests
├── ui/wip-console/           # Vue 3 + PrimeVue UI
├── WIP-Toolkit/              # CLI toolkit
├── data/                     # Runtime data (volumes, secrets)
└── testdata/                 # Test fixtures
```

## Git & CI

**Two remotes — always push to both:**
\`\`\`bash
git push gitea develop && git push github develop
\`\`\`

- **gitea** — `http://gitea.local:3000/peter/World-in-a-Pie.git` (primary, runs CI)
- **github** — `git@github.com:peterseb1969/World-in-a-Pie.git` (mirror)

**Branching:** Work on `develop`. `main` is the stable branch (tagged releases only). PRs go to `main` when ready.

**CI:** Gitea Actions via act_runner on `wip-pi.local`. Workflow: `.gitea/workflows/test.yaml`. Runs all component tests. Use `/pre-commit` locally before pushing.

## Working Principles

- **You own what you see.** Multiple AI agents work on this codebase. If you encounter a bug, lint issue, or broken test — fix it. Don't say "another agent should handle this." The user doesn't care who introduced a problem, only that it gets fixed quickly.
- **Don't over-engineer.** Make the minimal change needed. No speculative abstractions, no "while I'm here" refactors.
- **Ask before destructive actions.** Git force-push, dropping data, deleting branches — confirm first.

## Critical Gotchas

- **OIDC three-value rule** — issuer URL must match in 3 places. See `docs/network-configuration.md`.
- **Caddy: `handle` not `handle_path`** — services expect the full path. See `docs/network-configuration.md`.
- **Always activate venv** — `source .venv/bin/activate` before running Python.
- **Container recreate vs restart** — after `.env` changes, `podman-compose down && up -d`, not `restart`.
CLAUDEEOF
echo "   Written: CLAUDE.md"

# --- 4. Copy backend slash commands ---

echo "4. Copying backend slash commands..."
mkdir -p "$WIP_ROOT/.claude/commands"

# Remove any existing commands (from a previous setup)
rm -f "$WIP_ROOT/.claude/commands/"*.md 2>/dev/null || true

cp "$WIP_ROOT/docs/slash-commands/backend/"*.md "$WIP_ROOT/.claude/commands/"
echo "   Copied: $(find "$WIP_ROOT/.claude/commands/" -maxdepth 1 -name '*.md' -type f | wc -l | tr -d ' ') commands"

# --- 5. Verify MCP connectivity (local only) ---

if [[ "$TARGET" == "local" ]]; then
    echo "5. Verifying MCP server can start..."
    # Quick import check — doesn't need services running
    if PYTHONPATH="$WIP_ROOT/components/mcp-server/src" "$WIP_ROOT/.venv/bin/python" -c "import wip_mcp" 2>/dev/null; then
        echo "   MCP server module imports successfully"
    else
        echo "   Warning: MCP server module import failed."
        echo "   Fix: source .venv/bin/activate && pip install -e components/mcp-server/"
    fi
else
    echo "5. Skipping MCP verification (remote target — verify after launching claude)"
fi

# --- Done ---

echo ""
echo "Done! Backend agent is configured."
echo ""
echo "Next steps:"
echo "  claude"
echo "  /setup         # first-run environment checks"
echo ""
