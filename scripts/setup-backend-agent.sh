#!/usr/bin/env bash
#
# Set up a cloned WIP repo for a backend coding agent.
#
# Usage (one auto-detecting command — CASE-537):
#   ./scripts/setup-backend-agent.sh [--target local|ssh|http] [--host HOST] [--cert CERT_PATH] [--kb <url>]
#
#   No mode flag — the clone state selects the behavior:
#     • fresh clone (no .mcp.json / .venv) → SET UP: venv, .mcp.json, CLAUDE.md, commands.
#     • already set up                     → RE-SYNC: regenerate the same surfaces (idempotent).
#     • --kb <url>                         → TIER 3: fold KB enablement into the same run (no
#                                            separate step); without it the clone stays tier 2.
#                                            An existing .claude/kb.json is preserved.
#
# This script:
#   1. Sets up Python venv with a compatible Python (3.11-3.13)
#   2. Generates .mcp.json for the chosen transport
#   3. Generates a backend-focused CLAUDE.md (overwrites any existing)
#   4. Copies backend slash commands to .claude/commands/ (deletes any existing
#      *.md in that directory first — custom commands will be lost)
#   4b. Regenerates the committed .claude/settings.json permission baseline
#       on every run (CASE-446); .claude/settings.local.json is never touched
#   5. Verifies MCP connectivity (local target only)
#
# Re-sync (an already-set-up clone, auto-detected — no flag):
#   Re-syncs the propagatable surfaces from the gene pool: slash commands
#   in .claude/commands/, regenerates CLAUDE.md from the current heredoc,
#   regenerates .mcp.json with current arguments, and re-runs the idempotent
#   wip_mcp install so dependency changes pick up. Preserves: venv (recreates
#   only if missing), .claude/settings.local.json (never touched; the
#   committed .claude/settings.json baseline is regenerated — CASE-446).
#   Run it after a `git pull` brings new docs/slash-commands/backend/*.md or
#   heredoc changes — those don't propagate to .claude/commands/* automatically
#   because that directory is generated, not git-tracked.
#   A running session picks up re-copied slash commands automatically — Claude
#   Code live-detects .claude/commands/ edits and re-reads on the next invocation
#   (no /clear). CLAUDE.md and .mcp.json changes, by contrast, take effect only
#   on a next session start.
#

set -euo pipefail

WIP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# --- Branch guard (CASE-383) ---
# Canonical BE-YAC gene-pool content lives on develop. Running this script
# from another branch (typically a fresh clone still on main) scaffolds the
# agent with stale slash commands and docs — the 2026-05-14 incident put a
# fresh BE-YAC on pre-v2 setup.md and cost a 30-minute false alarm.

CURRENT_BRANCH="$(git -C "$WIP_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
if [[ "$CURRENT_BRANCH" != "develop" && -z "${ALLOW_NON_DEVELOP:-}" ]]; then
    echo "Error: clone is on '$CURRENT_BRANCH' branch, not 'develop'." >&2
    echo "  Canonical BE-YAC work lives on develop." >&2
    echo "  Fix: cd $WIP_ROOT && git checkout develop && git pull" >&2
    echo "  Override (rare): ALLOW_NON_DEVELOP=1 $0 $*" >&2
    exit 1
fi

# --- Parse arguments ---

TARGET="local"
HOST=""
CERT_PATH=""
# REFRESH_MODE is AUTO-DETECTED below from the clone state (CASE-537), not a
# user flag: already-set-up clone (has .mcp.json) → re-sync (true); fresh clone
# → first setup (false). It drives messaging only — every behavioral gate keys
# on KB_OPT_IN / .claude/.session-id.
REFRESH_MODE=false
# Tier-3 (KB) opt-in — CASE-463. Tier 2 (WIP-only) is the default.
KB_URL=""
KB_KEY_FILE=""

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
        --kb)
            KB_URL="$2"
            shift 2
            ;;
        --kb-key)
            KB_KEY_FILE="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [--target local|ssh|http] [--host HOST] [--cert CERT_PATH] [--kb <url>]"
            echo ""
            echo "Set up a WIP repo for a backend coding agent. One auto-detecting command:"
            echo "a fresh clone is set up; an already-set-up clone is re-synced (idempotent)."
            echo ""
            echo "Targets:"
            echo "  local   MCP via stdio to local venv (default)"
            echo "  ssh     MCP via SSH stdio proxy to remote host"
            echo "  http    MCP via HTTP/HTTPS transport to remote host"
            echo ""
            echo "Options:"
            echo "  --host HOST       Remote hostname (required for ssh/http)"
            echo "  --cert CERT_PATH  TLS cert for self-signed HTTPS (auto-detects from data/secrets/)"
            echo "  --kb URL          KB instance URL — makes this clone tier 3 (KB-backed collaboration,"
            echo "                    CASE-463) and folds enablement into this run: writes .claude/kb.json,"
            echo "                    installs the served KB client, drops the /wip-case stub. Idempotent;"
            echo "                    an existing kb.json is preserved. Default is tier 2: WIP-only, no KB."
            echo "                    Scheme optional (kb.internal → https://kb.internal)."
            echo "  --kb-key PATH     KB API key file (default: ~/.wip-deploy/kb/secrets/api-key)"
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

# --- Normalize --kb URL scheme (CASE-531) ---
# A scheme-less --kb (e.g. `kb.internal`) makes curl default to http, hit a 308
# redirect, and pipe the redirect HTML into `sh`. Assume https when no scheme.
if [ -n "$KB_URL" ] && [[ "$KB_URL" != *"://"* ]]; then
    KB_URL="https://$KB_URL"
fi

# --- Auto-detect re-sync vs first setup (CASE-537) ---
# The backend scaffold runs in situ on WIP_ROOT (no target-dir arg), so the
# switch is "has this clone already been set up as a BE-YAC", read from a prior
# generated artifact rather than a flag. .mcp.json is the signal: it is written
# ONLY by step 2 of this script, so its presence means a prior setup ran here.
# (Deliberately NOT .venv — a spawn-helper can pre-create .venv before first
# setup, per the wip_mcp note below, so .venv does not imply "already set up".)
if [ -f "$WIP_ROOT/.mcp.json" ]; then
    REFRESH_MODE=true
fi

# --- Tier resolution (CASE-463) ---
# Tier 2 (WIP-only) is the default; tier 3 (KB-backed collaboration) is explicit,
# declared by .claude/kb.json. KB_OPT_IN captures the EXPLICIT tier-3 intent —
# `--kb` passed on this run — which is what drives provisioning (a tier
# transition: tier-2→tier-3, or a re-affirmed tier-3). TIER3 is the resulting
# tier STATE (kb.json present OR --kb given) and gates emitted content. The tier
# is user intent, not generated content; it deliberately does NOT live in
# settings.json, which is regenerated every run.
KB_CONFIG="$WIP_ROOT/.claude/kb.json"
KB_OPT_IN=false
[ -n "$KB_URL" ] && KB_OPT_IN=true
TIER3=false
[ -f "$KB_CONFIG" ] && TIER3=true
[ -n "$KB_URL" ] && TIER3=true

enable_kb() {
    # Idempotent tier-3 enable: config + served client + case stub + staging note.
    if [ -z "$KB_URL" ] && [ -f "$KB_CONFIG" ]; then
        KB_URL="$(python3 -c "import json;print(json.load(open('$KB_CONFIG'))['kb_app_url'])")"
        KB_KEY_FILE="$(python3 -c "import json;print(json.load(open('$KB_CONFIG'))['kb_api_key_file'])")"
    fi
    if [ -z "$KB_URL" ]; then
        echo "Error: tier-3 enable needs --kb <url> (no existing .claude/kb.json to reuse)."
        exit 1
    fi
    KB_KEY_FILE="${KB_KEY_FILE:-$HOME/.wip-deploy/kb/secrets/api-key}"
    mkdir -p "$WIP_ROOT/.claude/commands"
    cat > "$KB_CONFIG" << KBEOF
{
  "kb_app_url": "$KB_URL",
  "kb_api_key_file": "$KB_KEY_FILE"
}
KBEOF
    echo "   Wrote: .claude/kb.json (tier 3 — KB at $KB_URL)"
    if [ -f "$KB_KEY_FILE" ]; then
        # Never pipe an un-inspected HTTP response into sh (CASE-557). Fetch the
        # install script to a temp file, then execute it ONLY on a 2xx status AND
        # a non-empty body — a redirect/error/HTML body (or a Caddy empty-200 on an
        # unmatched path) would otherwise run as shell.
        _kb_install="$(mktemp)"
        _kb_code="$(curl -sSk -o "$_kb_install" -w '%{http_code}' \
            -H "X-API-Key: $(cat "$KB_KEY_FILE")" \
            "$KB_URL/apps/kb/server-api/kb-client/install" 2>/dev/null || echo 000)"
        case "$_kb_code" in
            2??)
                if [ -s "$_kb_install" ] && sh "$_kb_install"; then
                    echo "   Served KB client installed/refreshed (~/.cache/wip-kb-client/)"
                else
                    echo "   WARNING: served-client install returned HTTP $_kb_code but no runnable"
                    echo "            body; run the install one-liner from docs/playbooks/case-workflow.md."
                fi ;;
            *)
                echo "   WARNING: served-client install skipped (HTTP $_kb_code); run the install"
                echo "            one-liner from docs/playbooks/case-workflow.md when KB is reachable." ;;
        esac
        rm -f "$_kb_install"
    else
        echo "   WARNING: KB key file not found at $KB_KEY_FILE; skipped client install."
    fi
    if cp "$WIP_ROOT/docs/slash-commands/backend/wip-case.md" "$WIP_ROOT/.claude/commands/" 2>/dev/null; then
        echo "   Dropped: /wip-case stub"
    fi
    if [ ! -e "$WIP_ROOT/yac-discussions" ]; then
        echo "   NOTE: no yac-discussions/ staging surface. Symlink the shared case"
        echo "         store (transition) — the write-gateway (CASE-464) will make it optional."
    fi
}

if $REFRESH_MODE; then
    echo "Refreshing backend agent environment:"
else
    echo "Setting up backend agent:"
fi
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

    # Install test dependencies
    pip install pytest ruff mypy -q 2>/dev/null || true

    echo "   Venv created"
fi

# Idempotent wip_mcp install. A pre-existing .venv may be incomplete — a
# spawn-helper that creates .venv before setup runs, or an earlier setup that
# half-failed, leaves the "venv exists" check above satisfied while wip_mcp is
# still missing. The stdio MCP server then dies on import; Claude Code surfaces
# that as "not connected" with no diagnostic.
if ! "$VENV_PYTHON" -c "import wip_mcp" 2>/dev/null; then
    if [ -f "$WIP_ROOT/components/mcp-server/pyproject.toml" ]; then
        echo "   Installing MCP server dependencies (wip_mcp missing in venv)..."
        if "$VENV_PYTHON" -m pip install -e "$WIP_ROOT/components/mcp-server/" -q 2>&1; then
            echo "   MCP server installed"
        else
            echo ""
            echo "   !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
            echo "   !!  MCP SERVER INSTALL FAILED                          !!"
            echo "   !!                                                     !!"
            echo "   !!  Without this, Claude cannot connect to WIP.        !!"
            echo "   !!  Fix manually:                                      !!"
            echo "   !!    source .venv/bin/activate                        !!"
            echo "   !!    pip install -e components/mcp-server/            !!"
            echo "   !!                                                     !!"
            echo "   !!  Then run /wip-setup in Claude to verify.               !!"
            echo "   !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
            echo ""
        fi
    else
        echo "   WARNING: components/mcp-server/pyproject.toml missing; cannot install wip_mcp"
    fi
fi

# --- 2. Generate .mcp.json ---

echo "2. Generating .mcp.json..."

# Determine API key
# The backend agent's MCP server needs a privileged key (wip-admins or wip-services)
# because it operates across namespaces. Non-privileged keys without explicit namespace
# scoping will get no access.
API_KEY=""
API_KEY_SOURCE=""
if [[ "$TARGET" == "local" ]]; then
    # 1. Prefer the RUNNING wip-deploy install (CASE-521): detect it from a live
    #    WIP container's compose working_dir label — the install path itself.
    #    Container names are service-named, not install-named, so a name guess is
    #    unreliable; the working_dir label is. Without this, the alphabetical glob
    #    below picks the wrong install on a multi-install box and bakes a
    #    401-causing key into .mcp.json.
    # CASE-539: parse working_dir out of the `{{.Labels}}` STRING. podman 6.0.0
    #    exposes `podman ps` `.Labels` as a comma-joined string, not a map, so the
    #    old `{{index .Labels "…"}}` returned empty for every container — silently
    #    defeating this detection (and `|| true` is required: under `set -euo
    #    pipefail` the empty-grep exit 1 would otherwise abort the whole script).
    if command -v podman >/dev/null 2>&1; then
        _wip_dirs="$(podman ps --format '{{.Labels}}' 2>/dev/null | grep -o 'com\.docker\.compose\.project\.working_dir=[^,]*' | cut -d= -f2- | grep '/\.wip-deploy/' | sort -u || true)"
        if [ "$(printf '%s\n' "$_wip_dirs" | grep -c .)" -eq 1 ] && [ -f "$_wip_dirs/secrets/api-key" ]; then
            API_KEY=$(tr -d '[:space:]' < "$_wip_dirs/secrets/api-key" 2>/dev/null)
            [ -n "$API_KEY" ] && API_KEY_SOURCE="$_wip_dirs/secrets/api-key (running install)"
        fi
    fi
    # 2. Else any wip-deploy v2 secrets/api-key — first match (alphabetical).
    #    Last resort among installs when none is detectably running.
    if [ -z "$API_KEY" ]; then
        for secrets_file in "$HOME/.wip-deploy"/*/secrets/api-key; do
            if [ -f "$secrets_file" ]; then
                API_KEY=$(tr -d '[:space:]' < "$secrets_file" 2>/dev/null)
                [ -n "$API_KEY" ] && API_KEY_SOURCE="$secrets_file"
                break
            fi
        done
    fi
    # 3. Dev default (won't authenticate against a real install; dev fixture only).
    #    (The pre-v2 repo-root .env fallback was retired — installs own their env.)
    if [ -z "$API_KEY" ]; then
        API_KEY="dev_master_key_for_testing"
        API_KEY_SOURCE="dev default (no install detected)"
    fi
    echo "   API key: sourced from $API_KEY_SOURCE (${#API_KEY} chars)"
else
    echo -n "   Enter API key for $HOST (must be wip-admins or wip-services): "
    read -r API_KEY
    if [[ -z "$API_KEY" ]]; then
        echo "   Error: API key is required for remote targets."
        exit 1
    fi
fi

MCP_FLAGS=""
case "$TARGET" in
    local)
        # The engine writes .mcp.json (shared writer with the app scaffold).
        # A key FILE is preferred when sourced from a wip-deploy secrets file
        # — rotation then applies without re-running this script; the literal
        # key covers only the no-install dev fixture. Base URL assumes WIP
        # via Caddy on https://localhost:8443 (the wip-deploy install shape);
        # edit after generation for direct-to-service setups.
        if [[ "$API_KEY_SOURCE" == "$HOME/.wip-deploy/"*/secrets/api-key ]]; then
            MCP_FLAGS="--mcp-python $VENV_PYTHON --mcp-base-url https://localhost:8443 --mcp-key-file $API_KEY_SOURCE"
        else
            MCP_FLAGS="--mcp-python $VENV_PYTHON --mcp-base-url https://localhost:8443 --mcp-key $API_KEY"
        fi
        echo "   .mcp.json: engine surface (stdio, local — $VENV_PYTHON, Caddy-routed on :8443)"
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
        "cd $REMOTE_PATH && source .venv/bin/activate && REGISTRY_URL=http://localhost:8001 DEF_STORE_URL=http://localhost:8002 TEMPLATE_STORE_URL=http://localhost:8003 DOCUMENT_STORE_URL=http://localhost:8004 REPORTING_SYNC_URL=http://localhost:8005 WIP_API_KEY=$API_KEY python -m wip_mcp.server"
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

# --- 3+4. Content surfaces via the engine (CASE-612 step 3) ---
# CLAUDE.md (render + tier filter), slash commands (wipe + tier gate),
# wake-rollover, settings baseline, and .session-role now run through
# wip_scaffold's surface matrix — one implementation shared with the app
# scaffold, CASE-604 discipline (atomic writes, idempotent, --dry-run).
# Surface policies and CASE provenance live in scaffold/src/wip_scaffold/.

echo "3. Rendering content surfaces (engine)..."
TIER_FLAG=""
if $TIER3; then TIER_FLAG="--tier3"; fi
# shellcheck disable=SC2086  # TIER_FLAG/MCP_FLAGS are deliberately word-split
PYTHONPATH="$WIP_ROOT/scaffold/src${PYTHONPATH:+:$PYTHONPATH}" \
    "$VENV_PYTHON" -m wip_scaffold backend --wip-root "$WIP_ROOT" $TIER_FLAG $MCP_FLAGS
echo "   Written: CLAUDE.md (tier $($TIER3 && echo 3 || echo 2))"

# --- Tier-3 provisioning (CASE-463, CASE-517, CASE-537) ---
# Provisioning (kb.json write, served-client install, /wip-case stub) runs only
# when --kb is passed (KB_OPT_IN) — a tier transition (tier-2→tier-3) or a
# re-affirmed tier-3. A re-sync WITHOUT --kb is offline file-propagation: the
# /wip-case stub is already re-copied above when tier-3, the served client
# self-refreshes on next use (digest-gated), and an existing kb.json must not be
# rewritten (it's user intent, not generated content). Tier-2 runs skip this.
if $KB_OPT_IN; then
    enable_kb
fi

# Session role (CASE-389) + settings baseline (CASE-446: scaffold-owned,
# regenerated every run; settings.local.json never touched — the full
# rationale lives on the surface entries in wip_scaffold/surfaces.py)
# are engine surfaces above.

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
# First command keyed on session STATE, not setup-vs-resync (CASE-532 #1): a
# clone with no .claude/.session-id has never minted a session, so /wip-wake
# would correctly refuse — /wip-setup is the right entry. /wip-wake is only for
# continuing an existing session (after /clear or a compaction reset).
if [ -f "$WIP_ROOT/.claude/.session-id" ]; then
    FIRST_CMD="/wip-wake          # Continue: roll the prior session over + recover context"
else
    FIRST_CMD="/wip-setup         # First run: mint a session ID + load baseline context"
fi
if $REFRESH_MODE; then
    echo "Done! Backend agent environment refreshed."
    echo ""
    echo "Slash-command edits are live in the running session immediately —"
    echo "Claude Code re-reads .claude/commands/ on the next invocation (no /clear"
    echo "or restart). CLAUDE.md and .mcp.json changes do need a next session start."
else
    echo "Done! Backend agent is configured."
fi
echo ""
echo "Next steps:"
echo "  claude"
echo "  $FIRST_CMD"
echo ""
