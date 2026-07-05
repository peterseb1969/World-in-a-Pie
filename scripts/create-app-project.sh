#!/usr/bin/env bash
#
# Create a new WIP app project directory with all required files.
#
# Usage (one auto-detecting command — CASE-535):
#   ./scripts/create-app-project.sh /path/to/dir [--kb <url>] [--name "My App"] [--prefix APP-<X>] [--preset query]
#
#   The directory state selects the behavior — you don't pick a mode:
#     • empty/new dir → CREATE: full scaffold + git init (steps 1-8 below).
#     • populated dir → SET UP IN PLACE: refresh everything propagatable from the
#       gene pool without disturbing the app's CLAUDE.md or working tree.
#     • --kb <url>    → TIER 3: fold KB enablement into the same run (no separate
#       step); without it the repo stays tier 2. An existing kb.json is preserved.
#
# Create does:
#   1. Creates the directory structure
#   2. Copies slash commands from docs/slash-commands/app-builder/
#   3. Copies reference docs (Vision.md, AI-Assisted-Development.md, WIP_PoNIFs.md,
#      WIP_DevGuardrails.md, wip-guide.md, technology-stack.md, ui-guidance.md,
#      ontology-support.md, wip-deployable-app-contract.md)
#   4. Generates .mcp.json pointing to this WIP installation
#   5. Copies and extracts client library tarballs + READMEs
#   6. Copies wip-toolkit wheel
#   7. Generates a starter CLAUDE.md
#   8. Initialises a git repo
#
# Set-up-in-place (populated dir) refreshes everything propagatable from the gene
#   pool: slash commands, slash-command playbooks, reference docs, client
#   libraries (tarballs + READMEs), wip-toolkit wheel, and regenerates .mcp.json
#   with the current WIP installation path. It is idempotent.
#   Does NOT touch: CLAUDE.md (would clobber app-specific customisation — a fresh
#   render is written to CLAUDE.md.refresh unless --force-claude-md),
#   .claude/settings.local.json (never touched — the committed
#   .claude/settings.json baseline is regenerated instead, CASE-446), bootstrap
#   templates, or git state.
#
# The generated .mcp.json uses WIP_API_KEY_FILE instead of a hardcoded key,
# so API key rotation in WIP automatically applies to all apps.

set -euo pipefail

WIP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# --- Branch guard (CASE-383) ---
# The scaffold copies gene-pool content (slash commands, docs, templates)
# out of this clone. Running from a non-develop branch (typically a fresh
# clone still on main) seeds the APP-YAC with stale content. Same guard as
# setup-backend-agent.sh.

CURRENT_BRANCH="$(git -C "$WIP_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
if [[ "$CURRENT_BRANCH" != "develop" && -z "${ALLOW_NON_DEVELOP:-}" ]]; then
    echo "Error: WIP clone is on '$CURRENT_BRANCH' branch, not 'develop'." >&2
    echo "  Canonical gene-pool content lives on develop." >&2
    echo "  Fix: cd $WIP_ROOT && git checkout develop && git pull" >&2
    echo "  Override (rare): ALLOW_NON_DEVELOP=1 $0 $*" >&2
    exit 1
fi

# --- Parse arguments ---

APP_DIR=""
APP_NAME=""
APP_PREFIX=""
PRESET="standard"
# REFRESH_MODE is AUTO-DETECTED below from the directory state (CASE-535), not
# a user flag: populated dir → set up in place (true); empty/new dir → create.
REFRESH_MODE=false
FORCE_CLAUDE_MD=false
WITH_BOOTSTRAP=false
# Tier-3 (KB) opt-in — CASE-463. Tier 2 (WIP-only) is the default.
KB_URL=""
KB_KEY_FILE=""

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
        --prefix)
            APP_PREFIX="$2"
            shift 2
            ;;
        --force-claude-md)
            FORCE_CLAUDE_MD=true
            shift
            ;;
        --with-bootstrap)
            WITH_BOOTSTRAP=true
            shift
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
            echo "Usage: $0 <directory> [--kb <url>] [--name \"App Name\"] [--prefix APP-<X>] [--preset standard|query]"
            echo ""
            echo "Sets up a WIP app project in <directory>. Auto-detects what to do (CASE-535):"
            echo "  • empty/new directory → create a fresh app (git init + full scaffold)"
            echo "  • populated directory → set up the existing checkout in place (idempotent;"
            echo "                          preserves CLAUDE.md and your working tree)"
            echo ""
            echo "There are no separate 'create' / 'refresh' / 'enable-kb' modes to choose —"
            echo "the one intent ('make this checkout a working YAC') is detected from the directory."
            echo ""
            echo "Options:"
            echo "  --kb <url>  Make the repo tier 3 (KB-backed collaboration, CASE-463) — the"
            echo "              explicit tier-3 opt-in. Writes .claude/kb.json, installs the served"
            echo "              KB client, drops the /wip-case stub, registers the role. Scheme is"
            echo "              optional (https:// assumed). Without --kb (and no existing kb.json)"
            echo "              the repo stays tier 2 (WIP-only); an existing kb.json is preserved."
            echo "  --kb-key    Path to the KB API key file (default: ~/.wip-deploy/kb/secrets/api-key)"
            echo "  --name      Display name for the app (default: derived from directory name)"
            echo "  --prefix    Session-role prefix (APP-KB, APP-RC, ...). Written to .claude/.session-role"
            echo "              so /wip-setup and /wip-wake mint <PREFIX>-YYYYMMDD-HHMMSS session IDs (CASE-389)."
            echo "  --preset    Project preset: 'standard' (default) or 'query' (NL query app)"
            echo "  --force-claude-md   Overwrite an existing CLAUDE.md outright instead of writing"
            echo "              CLAUDE.md.refresh. App-authored content is lost."
            echo "  --with-bootstrap    Retrofit the genesis bootstrap templates"
            echo "              (templates/bootstrap/*.template) for an app that predates them."
            echo "              Seeds only when templates/bootstrap/ is absent — never resurrects"
            echo "              a dir a built app deleted per the genesis banner. No-op on fresh create"
            echo "              (create always seeds them)."
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
    echo "Error: directory path is required."
    echo "Usage: $0 <directory> [--kb <url>] [--name \"App Name\"] [--prefix APP-<X>] [--preset standard|query]"
    exit 1
fi

if [[ "$PRESET" != "standard" && "$PRESET" != "query" ]]; then
    echo "Error: Unknown preset '$PRESET'. Use 'standard' or 'query'."
    exit 1
fi

# Resolve to absolute path
APP_DIR="$(cd "$(dirname "$APP_DIR")" 2>/dev/null && pwd)/$(basename "$APP_DIR")" || APP_DIR="$(pwd)/$APP_DIR"

# --- Normalize --kb URL scheme (CASE-531) ---
# A scheme-less --kb (e.g. `kb.internal`) makes curl default to http, hit a 308
# redirect, and pipe the redirect HTML into `sh`. Assume https when no scheme.
if [ -n "$KB_URL" ] && [[ "$KB_URL" != *"://"* ]]; then
    KB_URL="https://$KB_URL"
fi

# --- MCP pre-flight: the WIP venv must be able to run wip_mcp (CASE-558) ---
# The generated .mcp.json runs `$WIP_ROOT/.venv/bin/python -m wip_mcp.server`,
# but wip_mcp is provisioned into that venv by setup-backend-agent.sh, not by
# this script. Scaffolding an app against an unprovisioned clone used to emit
# a config that looks fine and dies at connect time (ModuleNotFoundError →
# MCP error -32000). Fail loud here, before anything is generated.
# Deliberately verify-only, no auto-install: the clone's venv belongs to the
# backend scaffold; this script stays out of it (Peter's call, fail loud).
MCP_PYTHON="$WIP_ROOT/.venv/bin/python"
if [ ! -x "$MCP_PYTHON" ]; then
    echo "Error: $MCP_PYTHON not found — this WIP clone has no venv, so the generated .mcp.json could not start the MCP server (error -32000)." >&2
    echo "  Fix: run scripts/setup-backend-agent.sh on this clone first (it creates the venv and installs wip_mcp)." >&2
    exit 1
fi
if ! "$MCP_PYTHON" -c "import wip_mcp" 2>/dev/null; then
    echo "Error: $MCP_PYTHON cannot import wip_mcp — the generated .mcp.json would die at connect time (MCP error -32000)." >&2
    echo "  The clone's venv is provisioned by the backend scaffold, not this script. Fix (either):" >&2
    echo "    cd $WIP_ROOT && .venv/bin/pip install -e components/mcp-server/" >&2
    echo "    or run scripts/setup-backend-agent.sh on this clone" >&2
    exit 1
fi

# --- Auto-detect setup-in-place vs create (CASE-535) ---
# The one user intent ("make this checkout a working YAC") is read from the
# directory, not chosen via a flag: a populated dir is set up in place
# (idempotent; preserves CLAUDE.md + working tree); an empty/new dir is created.
# This is the very check that used to ERROR on a non-empty dir — now it switches.
if [ -d "$APP_DIR" ] && [ -n "$(ls -A "$APP_DIR" 2>/dev/null)" ]; then
    REFRESH_MODE=true
fi

# --- Tier resolution (CASE-463) ---
# Tier 2 (WIP-only) is the default; tier 3 (KB-backed collaboration) is explicit,
# declared by .claude/kb.json. KB_OPT_IN captures the EXPLICIT tier-3 intent —
# `--kb` passed on this run — which is what drives provisioning (a tier
# transition: tier-2→tier-3, or a fresh tier-3 create). TIER3 is the resulting
# tier STATE (kb.json present OR --kb given) and gates emitted content. The tier
# is user intent, not generated content; it deliberately does NOT live in
# settings.json, which is regenerated every run.
KB_CONFIG="$APP_DIR/.claude/kb.json"
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
    mkdir -p "$APP_DIR/.claude/commands"
    cat > "$KB_CONFIG" << KBEOF
{
  "kb_app_url": "$KB_URL",
  "kb_api_key_file": "$KB_KEY_FILE"
}
KBEOF
    echo "   Wrote: .claude/kb.json (tier 3 — KB at $KB_URL)"
    # Served-client install: digest-gated, harmless to re-run. Failure is
    # non-fatal — the cached runner may already exist; recovery is the same
    # one-liner by hand (case-workflow playbook, "The served KB client").
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
    if cp "$WIP_ROOT/docs/slash-commands/app-builder/wip-case.md" "$APP_DIR/.claude/commands/" 2>/dev/null; then
        echo "   Dropped: /wip-case stub"
    fi
    # Best-effort: register the role prefix as a SESSION_ROLE term (CASE-420).
    # A tier-3 clone's first /wip-setup session mirror is term-validated against
    # SESSION_ROLE (namespace kb); an unregistered prefix bounces. Enable is
    # exactly when the clone becomes mirror-capable, so it registers here.
    # Non-fatal — a laptop may have no KB reach. APP-KB confirmed SESSION_ROLE
    # still gates the mirror and owns the durable seed; KB_TARGET_YAC is NOT a
    # sync gap (free strings, hand-curated) so it is deliberately skipped.
    # Direct def-store POST is correct: terminology provisioning, not a
    # kb-document write (no CASE-464 gateway conflict). Base/key from kb.json;
    # bulk-first means HTTP is always 200 — parse the per-item body, not the code.
    ROLE_PREFIX="${APP_PREFIX:-$(cat "$APP_DIR/.claude/.session-role" 2>/dev/null || true)}"
    if [ -n "$ROLE_PREFIX" ] && [ -f "$KB_KEY_FILE" ]; then
        DS="${KB_URL%/}/api/def-store"
        KB_KEY="$(cat "$KB_KEY_FILE")"
        SR_TID="$(curl -sk "$DS/terminologies/by-value/SESSION_ROLE" -H "X-API-Key: $KB_KEY" 2>/dev/null \
                  | sed -n 's/.*"terminology_id"[": ]*"\([^"]*\)".*/\1/p' | head -1 || true)"
        if [ -z "$SR_TID" ]; then
            echo "   SESSION_ROLE not resolvable at $DS — skip; add $ROLE_PREFIX via APP-KB's SESSION_ROLE.json (CASE-420)"
        else
            SR_RESP="$(curl -sk -X POST "$DS/terminologies/$SR_TID/terms" \
                       -H "X-API-Key: $KB_KEY" -H "Content-Type: application/json" \
                       -d "[{\"value\":\"$ROLE_PREFIX\",\"label\":\"$ROLE_PREFIX\",\"description\":\"YAC role ($ROLE_PREFIX).\"}]" \
                       2>/dev/null || echo '{}')"
            if printf '%s' "$SR_RESP" | grep -q '"succeeded":[ ]*1'; then
                echo "   Registered new SESSION_ROLE term: $ROLE_PREFIX"
                echo "   *** Durable seed: add \"$ROLE_PREFIX\" to APP-KB's SESSION_ROLE.json —"
                echo "       live registration alone drifts on the next kb re-bootstrap (CASE-420)."
            elif printf '%s' "$SR_RESP" | grep -q 'already exists'; then
                echo "   SESSION_ROLE term $ROLE_PREFIX already present — ok"
            elif [ "$SR_RESP" = '{}' ]; then
                echo "   KB unreachable — register $ROLE_PREFIX in SESSION_ROLE later (re-run with --kb online)"
            else
                echo "   SESSION_ROLE registration: unexpected response for $ROLE_PREFIX — $SR_RESP"
            fi
        fi
    fi
    if [ ! -e "$APP_DIR/yac-discussions" ]; then
        echo "   NOTE: no yac-discussions/ staging surface. Symlink the shared case"
        echo "         store (transition) — the write-gateway (CASE-464) will make it optional."
    fi
}

# --- Resolve app metadata (CASE-418) ---
# Set-up-in-place NEVER derives metadata from the directory name — that produced
# 'Dev ns: dev-.' on an in-place run of '.'. Resolution order: persisted
# .claude/.app-meta -> explicit --name -> backfill from the existing
# CLAUDE.md title -> hard error.

APP_META_FILE="$APP_DIR/.claude/.app-meta"
APP_SLUG=""
DEV_NAMESPACE=""
META_SOURCE=""

meta_get() { sed -n "s/^$1=\"\(.*\)\"\$/\1/p" "$APP_META_FILE" 2>/dev/null | head -1; }

if $REFRESH_MODE; then
    if [ -f "$APP_META_FILE" ] && [ -n "$(meta_get APP_NAME)" ]; then
        APP_NAME="$(meta_get APP_NAME)"
        APP_SLUG="$(meta_get APP_SLUG)"
        DEV_NAMESPACE="$(meta_get DEV_NAMESPACE)"
        META_SOURCE=".claude/.app-meta"
    elif [ -n "$APP_NAME" ]; then
        META_SOURCE="--name"
    elif [ -f "$APP_DIR/CLAUDE.md" ]; then
        # One-time backfill for pre-.app-meta clones: the title line was
        # interpolated from APP_NAME at create time; the namespace is the
        # first backticked token in the Dev Namespace section (apps can use
        # namespaces that are NOT dev-<slug>, e.g. WIP-DnD's `dnd`).
        APP_NAME="$(sed -n 's/^# //p' "$APP_DIR/CLAUDE.md" | head -1)"
        if [ -z "$APP_NAME" ]; then
            echo "Error: could not derive the app name from $APP_DIR/CLAUDE.md."
            echo "       Re-run with --name \"App Name\"."
            exit 1
        fi
        # Backfill is a fallback PARSER, not an invariant. The grep exits 1
        # when the '## Dev Namespace' heading is absent or has drifted (long-
        # lived clones rename it '## Namespace' etc.); under `set -euo
        # pipefail` that 1 propagates and kills the script AT THIS ASSIGNMENT,
        # before any output — a silent death (CASE-460). `|| true` makes the
        # parse tolerant; the empty result is then handled loudly below.
        # shellcheck disable=SC2016  # literal backticks: extracting a `code`-formatted namespace from markdown
        DEV_NAMESPACE="$(sed -n '/^## Dev Namespace/,/^## /p' "$APP_DIR/CLAUDE.md" | grep -o '`[a-z0-9][a-z0-9-]*`' | head -1 | tr -d '`' || true)"
        if [ -z "$DEV_NAMESPACE" ]; then
            # Refuse to guess. Heading drift means this app may run on a
            # namespace that is NOT dev-<slug> (e.g. a live one); silently
            # defaulting would pin the wrong namespace into durable state.
            # Fail with the same remediation as the no-CLAUDE.md path.
            echo "Error: found $APP_DIR/CLAUDE.md but could not parse a"
            echo "       namespace from a '## Dev Namespace' section (the heading"
            echo "       may have drifted, e.g. '## Namespace'). Refusing to guess —"
            echo "       this app may use a non-dev-<slug> namespace."
            echo "       Fix: write .claude/.app-meta with the real values, or"
            echo "       re-run with --name \"App Name\" (CASE-418/460)."
            exit 1
        fi
        META_SOURCE="CLAUDE.md backfill"
    else
        echo "Error: cannot resolve app metadata for in-place setup: no .claude/.app-meta,"
        echo "       no --name, and no existing CLAUDE.md to derive from."
        echo "       Re-run with --name \"App Name\" (CASE-418)."
        exit 1
    fi
fi

# Derive app name from directory if not provided (create path only — in-place
# setup resolved it above or exited)
if [ -z "$APP_NAME" ]; then
    APP_NAME="$(basename "$APP_DIR" | sed 's/[-_]/ /g' | awk '{for(i=1;i<=NF;i++) $i=toupper(substr($i,1,1)) substr($i,2)}1')"
fi

# Derive slug from app name (lowercase, hyphens) — used for namespace and package name
if [ -z "$APP_SLUG" ]; then
    APP_SLUG="$(echo "$APP_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/ /-/g')"
fi
if [ -z "$DEV_NAMESPACE" ]; then
    # Create mode (or a --name refresh with no parsed namespace): dev-<slug>
    # is the documented default. The CLAUDE.md-backfill path never reaches
    # here empty — it fails loud above rather than guess (CASE-460).
    DEV_NAMESPACE="dev-${APP_SLUG}"
fi

if $REFRESH_MODE; then
    echo "Refreshing WIP app environment:"
else
    echo "Creating WIP app project:"
fi
echo "  Directory: $APP_DIR"
echo "  App name:  $APP_NAME"
echo "  Slug:      $APP_SLUG"
echo "  Dev ns:    $DEV_NAMESPACE"
if [ -n "$META_SOURCE" ]; then
    echo "  Metadata:  $META_SOURCE"
fi
echo "  Preset:    $PRESET"
echo "  WIP root:  $WIP_ROOT"
echo ""

# --- Check prerequisites ---

if $REFRESH_MODE; then
    # Set-up-in-place: the dir is non-empty by construction (that's what selected
    # this path). A missing CLAUDE.md just means it isn't a WIP app yet — warn,
    # don't refuse; we still set it up in place.
    if [ ! -f "$APP_DIR/CLAUDE.md" ]; then
        echo "Warning: $APP_DIR/CLAUDE.md not found — setting up an existing directory in place."
    fi
else
    # Create path: the dir is empty/absent by construction (a non-empty dir would
    # have selected set-up-in-place above — that old error is now the switch).
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

# Slash commands + wake-rollover are engine surfaces (CASE-612 step 3) —
# rendered in one wip_scaffold call further down (after key-file
# resolution, which CLAUDE.md needs). Note the deliberate order change:
# enable_kb below now runs BEFORE the commands copy; its wip-case stub cp
# is belt-only — the engine's tier-gated copy is authoritative either way.

# --- Tier-3 provisioning (CASE-463, CASE-517, CASE-535) ---
# Provisioning (kb.json write, served-client install, SESSION_ROLE POST) runs on
# the explicit tier-3 opt-in only — KB_OPT_IN, i.e. `--kb` passed on this run.
# That covers both a fresh create-with-kb AND retrofitting tier-3 onto a populated
# tier-2 checkout (the old --enable-kb, now folded into the one flow — CASE-532).
# A set-up-in-place run WITHOUT --kb is offline file-propagation: the /wip-case
# stub is already re-copied above, the served client self-refreshes on next use
# (digest-gated), and a pre-existing kb.json must not be rewritten. Tier-2 runs
# (no --kb, no kb.json) skip this entirely.
if $KB_OPT_IN; then
    enable_kb
fi

# Session role (CASE-389), .app-meta (CASE-418), settings baseline
# (CASE-446), post-compact hook (CASE-480), playbooks (copy-only —
# CASE-522), and reference docs are engine surfaces (CASE-612 step 3),
# rendered in the wip_scaffold call further down. Their policies and the
# full per-surface rationale live in scaffold/src/wip_scaffold/surfaces.py.

# --- Bootstrap templates (genesis copies, CASE-415) ---
# Genesis-copy provenance stamp (CASE-415). These three files are one-time
# Phase-1 STARTING POINTS — the APP-YAC builds server/lib/bootstrap.ts from them
# by hand, then they are vestigial. A frozen, unmarked copy invites the
# grep-it-as-canonical misread that produced CASE-414. Stamp each SPAWNED copy
# with its source SHA, a "not canonical" warning, and a delete-after-use
# instruction. The canonical source in WIP_ROOT is never touched. `|| echo
# unknown` keeps the SHA capture from dying under set -euo pipefail (CASE-460).
seed_bootstrap_templates() {
    local BOOTSTRAP_SRC="$WIP_ROOT/apps/templates/bootstrap"
    if [ ! -d "$BOOTSTRAP_SRC" ]; then
        echo "   Warning: $BOOTSTRAP_SRC not found, skipping bootstrap templates"
        return
    fi
    echo ""
    echo "   Copying bootstrap templates..."
    local STAMP_SHA STAMP_DATE tpl dest
    STAMP_SHA="$(git -C "$WIP_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    STAMP_DATE="$(date '+%Y-%m-%d')"
    mkdir -p "$APP_DIR/templates/bootstrap"
    for tpl in bootstrap.server.ts.template bootstrap.routes.ts.template BootstrapGate.tsx.template; do
        if [ -f "$BOOTSTRAP_SRC/$tpl" ]; then
            dest="$APP_DIR/templates/bootstrap/$tpl"
            {
                cat <<BANNER
// ============================================================================
// GENESIS COPY (CASE-415) — not canonical, not live. One-time Phase-1 start.
//   Source: World-in-a-Pie@${STAMP_SHA}, spawned ${STAMP_DATE}.
//   Canonical scaffold: World-in-a-Pie/apps/templates/bootstrap/ — this frozen
//   copy WILL drift from it. NEVER grep this dir as evidence of platform or
//   scaffold behavior (an agent once did, and shipped fixes against long-drifted code).
//   After you build server/lib/bootstrap.ts from this, DELETE templates/bootstrap/.
// ============================================================================
BANNER
                cat "$BOOTSTRAP_SRC/$tpl"
            } > "$dest"
            echo "     templates/bootstrap/$tpl (genesis-stamped)"
        else
            echo "     Warning: $tpl not found in $BOOTSTRAP_SRC, skipping"
        fi
    done
}

# Create always seeds the genesis templates. Set-up-in-place seeds them ONLY with
# --with-bootstrap AND only when templates/bootstrap/ is absent: a deliberate
# retrofit for apps that predate the templates (the refreshed CLAUDE.md tells the
# YAC to read templates/bootstrap/*.template, but a plain set-up-in-place never shipped
# them). Never clobber an existing dir — a mature app that built bootstrap.ts and
# deleted templates/bootstrap/ per the genesis banner must NOT have it resurrected;
# that is why retrofit is opt-in, not automatic-on-absence.
if ! $REFRESH_MODE; then
    seed_bootstrap_templates
elif $WITH_BOOTSTRAP; then
    if [ -d "$APP_DIR/templates/bootstrap" ]; then
        echo "   --with-bootstrap: templates/bootstrap/ already present — left as-is."
        echo "     (A built app that removed it per the genesis banner should not have it"
        echo "      resurrected; delete the dir first if you want fresh genesis copies.)"
    else
        echo "   --with-bootstrap: retrofitting genesis bootstrap templates (app predates them)..."
        seed_bootstrap_templates
    fi
fi

# --- Generate .mcp.json ---

if $REFRESH_MODE; then
    echo "1. Regenerating .mcp.json..."
else
    echo "4. Generating .mcp.json..."
fi

# Determine the API-key secrets file (CASE-520).
# The MCP server reads its key from WIP_API_KEY_FILE at startup, so key rotation
# in wip-deploy automatically propagates without re-running this script. The MCP
# server needs a privileged key (wip-admins or wip-services) because it operates
# across namespaces; app runtime code should use a namespace-scoped key instead.
# This single value flows into .mcp.json, .env, and CLAUDE.md, so resolve it once.
# Resolution priority — avoids the dead-path whack-a-mole of a hardcoded install
# name (CASE-520: a literal 'wip-dev-local' pointed at a nonexistent install, so
# every set-up-in-place re-injected a 401-causing key path):
#   1. WIP_API_KEY_FILE_OVERRIDE — explicit escape hatch.
#   2. set-up-in-place: PRESERVE a readable key file already in the app's .mcp.json
#      (mirrors the WIP_BASE_URL preserve block below — stops set-up-in-place clobbering
#      a known-good path on every run).
#   3. Detect the RUNNING wip-deploy install from a live WIP container's compose
#      working_dir label (the install path itself; container names are
#      service-named, not install-named, so a name guess is unreliable).
#   4. Nothing resolved → HARD ERROR (CASE-558). There is no literal default:
#      the old ~/.wip-deploy/wip-local fallback is a dead path for
#      deploy-guide installs (--name wip), and a stale path written here
#      passes generation only to 401/ENOENT at connect time.
WIP_API_KEY_FILE="${WIP_API_KEY_FILE_OVERRIDE:-}"
if [ -z "$WIP_API_KEY_FILE" ] && $REFRESH_MODE && [ -f "$APP_DIR/.mcp.json" ]; then
    _existing_key="$(python3 -c "import json; print(json.load(open('$APP_DIR/.mcp.json'))['mcpServers']['wip']['env'].get('WIP_API_KEY_FILE',''))" 2>/dev/null || true)"
    if [ -n "$_existing_key" ] && [ -f "$_existing_key" ]; then
        WIP_API_KEY_FILE="$_existing_key"
        echo "   Preserving existing .mcp.json key file: $WIP_API_KEY_FILE"
    fi
fi
if [ -z "$WIP_API_KEY_FILE" ] && command -v podman >/dev/null 2>&1; then
    # `|| true`: grep exits 1 when NO running container is a wip-deploy install.
    # Under `set -euo pipefail` that 1 propagates through the command
    # substitution and kills the script AT THIS ASSIGNMENT — before .mcp.json
    # and .env are ever written (CASE-534). The empty result is handled below.
    # CASE-539: parse working_dir out of the `{{.Labels}}` STRING. podman 6.0.0
    # exposes `podman ps` `.Labels` as a comma-joined string, not a map, so the
    # old `{{index .Labels "…"}}` returned empty for every container — silently
    # defeating the running-install detection. (`podman inspect` is still a map.)
    _wip_dirs="$(podman ps --format '{{.Labels}}' 2>/dev/null | grep -o 'com\.docker\.compose\.project\.working_dir=[^,]*' | cut -d= -f2- | grep '/\.wip-deploy/' | sort -u || true)"
    if [ "$(printf '%s\n' "$_wip_dirs" | grep -c .)" -eq 1 ] && [ -f "$_wip_dirs/secrets/api-key" ]; then
        WIP_API_KEY_FILE="$_wip_dirs/secrets/api-key"
        echo "   Detected running WIP install: $WIP_API_KEY_FILE"
    fi
fi
if [ -z "$WIP_API_KEY_FILE" ]; then
    echo "Error: could not resolve the WIP API key file — no WIP_API_KEY_FILE_OVERRIDE, no preserved .mcp.json path, and no (single) running WIP install detected (CASE-558)." >&2
    echo "  Fix (either):" >&2
    echo "    deploy a WIP install first (wip-deploy install ... --name wip), then re-run" >&2
    echo "    or WIP_API_KEY_FILE_OVERRIDE=\$HOME/.wip-deploy/<name>/secrets/api-key $0 ..." >&2
    exit 1
fi
if [ ! -f "$WIP_API_KEY_FILE" ]; then
    # Only reachable via WIP_API_KEY_FILE_OVERRIDE — the preserve and
    # auto-detect steps both check existence. Keep the explicit escape
    # hatch usable for pre-provisioning, but say so.
    echo "   Warning: $WIP_API_KEY_FILE does not exist yet (override accepted as-is)."
fi

# Python path: the MCP pre-flight (CASE-558) already guaranteed this
# interpreter exists and imports wip_mcp. No system-python fallback — a
# python without wip_mcp is exactly the -32000 the pre-flight exists to stop.
PYTHON_PATH="$MCP_PYTHON"

# Target base URL for the WIP services (CASE-516). Default is the local Caddy.
# On set-up-in-place, PRESERVE a deliberate non-localhost target already in .mcp.json
# instead of clobbering it back to localhost — an earlier refresh silently reset
# APP-KB's hand-set kb.internal target. Override with WIP_BASE_URL_OVERRIDE.
WIP_BASE_URL="${WIP_BASE_URL_OVERRIDE:-}"
if [ -z "$WIP_BASE_URL" ] && $REFRESH_MODE && [ -f "$APP_DIR/.mcp.json" ]; then
    WIP_BASE_URL="$(python3 -c "import json; print(json.load(open('$APP_DIR/.mcp.json'))['mcpServers']['wip']['env'].get('REGISTRY_URL',''))" 2>/dev/null || true)"
    [ -n "$WIP_BASE_URL" ] && echo "   Preserving existing .mcp.json target: $WIP_BASE_URL"
fi
WIP_BASE_URL="${WIP_BASE_URL:-https://localhost:8443}"

cat > "$APP_DIR/.mcp.json" << EOF
{
  "mcpServers": {
    "wip": {
      "type": "stdio",
      "command": "$PYTHON_PATH",
      "args": ["-m", "wip_mcp.server"],
      "env": {
        "WIP_API_KEY_FILE": "$WIP_API_KEY_FILE",
        "REGISTRY_URL": "$WIP_BASE_URL",
        "DEF_STORE_URL": "$WIP_BASE_URL",
        "TEMPLATE_STORE_URL": "$WIP_BASE_URL",
        "DOCUMENT_STORE_URL": "$WIP_BASE_URL",
        "REPORTING_SYNC_URL": "$WIP_BASE_URL",
        "WIP_VERIFY_TLS": "false"
      }
    }
  }
}
EOF
echo "   Written: .mcp.json"
echo "   API key source: $WIP_API_KEY_FILE"

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
# CASE-442 (supersedes CASE-441's `-latest.tgz` selection): distribute the
# VERSIONED tarball named by the lib's package.json (wip-<lib>-<version>.tgz).
# `-latest.tgz` was a mutable artifact — fixed filename, changing content — so
# every rebuild desynced consumer package-lock.json integrity (and the npm
# cache) from the shipped file, and `npm ci` failed with EINTEGRITY. A
# version-named tarball is immutable by convention: a content change requires
# a version bump, which changes the `file:` spec and forces npm to re-resolve
# from disk. Empty result (no tarball matching the lib's current version)
# falls through to the rebuild block below.
lib_tarball() {
    local lib_dir="$1" base ver
    base=$(basename "$lib_dir")
    ver=$(sed -n 's/^[[:space:]]*"version":[[:space:]]*"\([^"]*\)".*/\1/p' "$lib_dir/package.json" 2>/dev/null | head -1)
    [ -n "$ver" ] && [ -f "$lib_dir/${base}-${ver}.tgz" ] && printf '%s\n' "$lib_dir/${base}-${ver}.tgz"
}
CLIENT_TARBALL=$(lib_tarball "$WIP_ROOT/libs/wip-client")
REACT_TARBALL=$(lib_tarball "$WIP_ROOT/libs/wip-react")
PROXY_TARBALL=$(lib_tarball "$WIP_ROOT/libs/wip-proxy")

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
    # CASE-441: return the exact tarball `npm pack` just produced
    # (<name>-<version>.tgz from package.json), not `find … | head -1` which
    # returned an arbitrary (often stale) tarball from the accumulated set.
    lib_tarball "$lib_dir"
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

    # CASE-442: versioned tarballs are immutable — the same filename must mean
    # the same bytes. If the app already holds a tarball of this exact name
    # with DIFFERENT content, the lib's content changed without a version bump;
    # shipping it would silently re-point the version at new bytes. Fail loudly.
    local dest
    dest="$APP_DIR/libs/$(basename "$tarball")"
    if [ -f "$dest" ] && ! cmp -s "$tarball" "$dest"; then
        echo "   ERROR: $(basename "$tarball") already exists in the app with different content."
        echo "   The library's content changed without a version bump (CASE-442: versioned"
        echo "   tarballs are immutable). Bump the version in the lib's package.json,"
        echo "   npm pack, commit the new tarball, then re-run this script."
        exit 1
    fi

    # CASE-441: drop any previously-copied tarballs for this lib so the app's
    # `npm install ./libs/<lib>-*.tgz` glob resolves to exactly this one — older
    # (buggy) refreshes copied version-named tarballs; leaving them makes the glob
    # ambiguous and can pin a stale version alongside the new one.
    rm -f "$APP_DIR/libs/wip-${lib_name#@wip/}-"*.tgz
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

# CASE-442: on refresh, re-record the shipped tarballs in the app's
# package.json + package-lock.json. Copying a tarball alone leaves the
# lockfile pinning the OLD content hash, and `npm ci` (the Dockerfile
# default) fails with EINTEGRITY. `npm install ./libs/<name>-<version>.tgz`
# rewrites the `file:` spec and re-hashes the file from disk — a changed spec
# cannot be satisfied from the npm cache, which is what made lockfile-only
# regeneration against the old mutable `-latest.tgz` unreliable. Only deps
# the app already declares are synced — refresh must not add libraries the
# app doesn't use.
if $REFRESH_MODE && [ -f "$APP_DIR/package.json" ] && command -v npm &>/dev/null; then
    SYNC_SPECS=()
    for tb in "$CLIENT_TARBALL" "$REACT_TARBALL" "$PROXY_TARBALL"; do
        [ -n "$tb" ] || continue
        pkg="@wip/$(basename "$tb" | sed 's/^wip-//; s/-[0-9].*//')"
        if grep -q "\"$pkg\"" "$APP_DIR/package.json"; then
            SYNC_SPECS+=("./libs/$(basename "$tb")")
        fi
    done
    if [ ${#SYNC_SPECS[@]} -gt 0 ]; then
        echo "   Syncing package.json + package-lock.json to the shipped tarballs..."
        if (cd "$APP_DIR" && npm install "${SYNC_SPECS[@]}" --no-audit --no-fund --loglevel=error); then
            # Guard against same-version content drift: the lockfile must now
            # record the hash of the file we shipped. A mismatch means npm
            # satisfied the spec from a stale cache entry — i.e. the lib's
            # content changed without a version bump (the immutable-tarball
            # rule this fix exists to enforce). Fail loudly, not silently.
            for spec in "${SYNC_SPECS[@]}"; do
                tb_name=$(basename "$spec")
                pkg="@wip/$(echo "$tb_name" | sed 's/^wip-//; s/-[0-9].*//')"
                want="sha512-$(node -e "const c=require('crypto'),f=require('fs');process.stdout.write(c.createHash('sha512').update(f.readFileSync('$APP_DIR/libs/$tb_name')).digest('base64'))")"
                got=$(node -e "const l=JSON.parse(require('fs').readFileSync('$APP_DIR/package-lock.json','utf8'));const e=l.packages&&l.packages['node_modules/$pkg'];process.stdout.write((e&&e.integrity)||'')")
                if [ "$want" != "$got" ]; then
                    echo "   ERROR: $tb_name — lockfile integrity does not match the shipped file."
                    echo "   The library's content changed without a version bump (CASE-442:"
                    echo "   versioned tarballs are immutable). Bump the version in the lib's"
                    echo "   package.json, npm pack, commit the new tarball, then re-run this script."
                    exit 1
                fi
            done
            echo "   Lockfile synced — commit package.json + package-lock.json in the app repo."
        else
            echo "   WARNING: npm install failed — sync the lockfile manually:"
            echo "     cd $APP_DIR && npm install ${SYNC_SPECS[*]}"
            echo "   then commit package.json + package-lock.json."
        fi
    fi
fi

# --- Copy wip-toolkit ---
#
# (CASE-286: dev-delete.py copy removed 2026-05-08. The legacy script bypassed
# the WIP API and wrote directly to MongoDB; superseded by `mcp__wip__delete_namespace`
# which the heredoc already prescribes. Apps no longer get the orphan tool in
# their tools/ directory.)

if $REFRESH_MODE; then
    echo "3. Refreshing wip-toolkit..."
else
    echo "6. Copying wip-toolkit..."
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

# --- Copy query scaffold files (--preset query only, new projects only) ---

if ! $REFRESH_MODE && [ "$PRESET" = "query" ]; then
    SCAFFOLD_DIR="$WIP_ROOT/scripts/scaffold-query"
    if [ ! -d "$SCAFFOLD_DIR" ]; then
        echo "Error: Scaffold template directory not found: $SCAFFOLD_DIR"
        exit 1
    fi

    echo "7. Copying NL query scaffold..."

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
    # k8s-ready from day 1 (CASE-370): the production Dockerfile, the
    # Dockerfile.dev (wip-deploy --app-source dev flow) + its entrypoint, and
    # .dockerignore are root-level files that `cp -r src` doesn't catch.
    cp "$SCAFFOLD_DIR/Dockerfile" "$APP_DIR/"
    cp "$SCAFFOLD_DIR/Dockerfile.dev" "$APP_DIR/"
    cp "$SCAFFOLD_DIR/docker-entrypoint-dev.sh" "$APP_DIR/"
    cp "$SCAFFOLD_DIR/.dockerignore" "$APP_DIR/"
    # Scaffold-owned CI workflow (CASE-489): a single template so cross-cutting
    # CI changes are one edit, not an N-repo sweep. Templates the proven shape
    # as-is (arch/runner behaviour unchanged — the arm64 regression is a
    # separate concern, not entangled here).
    mkdir -p "$APP_DIR/.github/workflows"
    cp "$SCAFFOLD_DIR/.github/workflows/build.yaml" "$APP_DIR/.github/workflows/"
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
    sed -i '' "s/SCAFFOLD_APP_SLUG/$APP_SLUG/g" "$APP_DIR/.github/workflows/build.yaml"

    # Update .env.example with actual paths
    sed -i '' "s|/path/to/WorldInPie|$WIP_ROOT|g" "$APP_DIR/.env.example"

    echo "   Copied: server/ (agent.ts, index.ts, prompts/)"
    echo "   Copied: src/ (App.tsx, AskBar.tsx, HomePage.tsx, vite-env.d.ts)"
    echo "   Copied: package.json, tsconfig.json, vite.config.ts, tailwind, .env.example"
    echo "   Copied: Dockerfile, Dockerfile.dev, docker-entrypoint-dev.sh, .dockerignore (k8s-ready from day 1 — CASE-370)"
    echo "   Copied: .github/workflows/build.yaml (scaffold-owned CI — CASE-489)"
    echo "   App slug: $APP_SLUG"

    STEP_OFFSET=1
else
    STEP_OFFSET=0
fi

# --- Create dev namespace (new projects only) ---

if ! $REFRESH_MODE; then
    STEP_NUM=$((7 + STEP_OFFSET))
    echo "$STEP_NUM. Creating dev namespace '$DEV_NAMESPACE'..."
    STEP_OFFSET=$((STEP_OFFSET + 1))

    # Best-effort — WIP may not be running. Non-fatal.
    # ACTIVE_KEY is the admin key content from the wip-deploy secrets file.
    # (Its assignment was dropped in 4e39dde's .mcp.json rework while the two
    # uses below survived — under `set -u` that aborted every fresh create at
    # this step. Empty/missing file degrades to the non-fatal HTTP branches.)
    ACTIVE_KEY=$(cat "$WIP_API_KEY_FILE" 2>/dev/null || true)
    NS_RESPONSE=$(curl -k -s -o /dev/null -w "%{http_code}" \
        -X POST "https://localhost:8443/api/registry/namespaces" \
        -H "X-API-Key: $ACTIVE_KEY" \
        -H "Content-Type: application/json" \
        -d "{\"prefix\": \"$DEV_NAMESPACE\", \"description\": \"$APP_NAME (dev)\"}" \
        2>/dev/null || echo "000")

    if [ "$NS_RESPONSE" = "200" ]; then
        echo "   Created namespace: $DEV_NAMESPACE"
    elif [ "$NS_RESPONSE" = "000" ]; then
        echo "   WIP not reachable — APP-YAC will create the namespace on first run"
    else
        echo "   HTTP $NS_RESPONSE — namespace may already exist (ok)"
    fi

    # Set WIP_NAMESPACE in .env.example if query preset
    if [ "$PRESET" = "query" ] && [ -f "$APP_DIR/.env.example" ]; then
        sed -i '' "s|# WIP_NAMESPACE=myapp|WIP_NAMESPACE=$DEV_NAMESPACE|" "$APP_DIR/.env.example"
        echo "   Set WIP_NAMESPACE=$DEV_NAMESPACE in .env.example"
    fi

    # --- Runtime key source: the live wip-deploy secrets file (CASE-495) ---
    # Point .env at the SAME live file the MCP config uses ($WIP_API_KEY_FILE),
    # rather than provisioning a namespace-scoped key and baking its plaintext
    # into .env. A baked key goes stale the moment the deploy key rotates or
    # the target redeploys — which stranded the whole fleet. Reading the file
    # at app startup (the @wip/proxy `apiKeyFile` option does this) makes a
    # rotation a non-event: just restart. The deploy key is an admin/proxy key
    # spanning all namespaces — what a cross-namespace console needs as-is; a
    # data-model app wanting least-privilege can provision its own scoped key
    # and repoint WIP_API_KEY_FILE at it (Peter's call, CASE-495).
    STEP_NUM=$((7 + STEP_OFFSET))
    echo "$STEP_NUM. Pointing .env runtime key at the wip-deploy secrets file..."
    STEP_OFFSET=$((STEP_OFFSET + 1))

    cat > "$APP_DIR/.env" << ENVEOF
# Runtime key SOURCE — the live wip-deploy secrets file (CASE-495).
# Resolved at app startup (like the MCP server's WIP_API_KEY_FILE), so a key
# rotation or target-redeploy is picked up on restart rather than baked stale
# here. This is the deployment's admin/proxy key and spans all namespaces — a
# cross-namespace console uses it as-is. A data-model app that wants a
# least-privilege, namespace-scoped key can provision one (POST
# /api/registry/api-keys with "namespaces" + "grant_permission") and repoint
# WIP_API_KEY_FILE below at its own secrets file.
WIP_API_KEY_FILE=$WIP_API_KEY_FILE
ENVEOF
    echo "   Written: .env (WIP_API_KEY_FILE -> $WIP_API_KEY_FILE)"
fi

# --- Content surfaces via the engine (CASE-612 step 3) ---
# Slash commands (wipe + tier gate), wake-rollover, .session-role,
# .app-meta, settings baseline, post-compact hook, playbooks (copy-only —
# CASE-522), reference docs, and CLAUDE.md (create → CLAUDE.md; refresh +
# existing → CLAUDE.md.refresh unless --force-claude-md, CASE-418) run
# through wip_scaffold's surface matrix — one implementation shared with
# the backend scaffold, CASE-604 discipline (atomic writes, idempotent,
# --dry-run). Placed here because the CLAUDE.md render needs the resolved
# WIP_API_KEY_FILE. bash retains: guards, arg parsing, enable_kb,
# .mcp.json, libs/toolkit, bootstrap templates, query preset, namespace,
# .env, git init (they migrate in later step-3 phases).
if ! $REFRESH_MODE; then
STEP_NUM=$((7 + STEP_OFFSET))
echo "$STEP_NUM. Rendering content surfaces (engine)..."
else
echo "Rendering content surfaces (engine; metadata: $META_SOURCE)..."
fi
ENGINE_FLAGS=""
if $TIER3; then ENGINE_FLAGS="$ENGINE_FLAGS --tier3"; fi
if $REFRESH_MODE; then ENGINE_FLAGS="$ENGINE_FLAGS --refresh"; fi
if $FORCE_CLAUDE_MD; then ENGINE_FLAGS="$ENGINE_FLAGS --force-claude-md"; fi
# shellcheck disable=SC2086  # ENGINE_FLAGS is deliberately word-split (0..3 flags)
PYTHONPATH="$WIP_ROOT/scaffold/src${PYTHONPATH:+:$PYTHONPATH}" \
    "$MCP_PYTHON" -m wip_scaffold app \
    --wip-root "$WIP_ROOT" --app-dir "$APP_DIR" \
    --app-name "$APP_NAME" --app-slug "$APP_SLUG" \
    --dev-namespace "$DEV_NAMESPACE" --key-file "$WIP_API_KEY_FILE" \
    --preset "$PRESET" --role-prefix "$APP_PREFIX" $ENGINE_FLAGS

# --- Git init + gitignore sentinels (new projects only) ---
if ! $REFRESH_MODE; then
# Ensure .env is gitignored — environment-specific (carries WIP_API_KEY_FILE,
# the local wip-deploy secrets path; CASE-495 removed the baked plaintext key)
if [ -f "$APP_DIR/.env" ]; then
    if [ ! -f "$APP_DIR/.gitignore" ]; then
        printf '.env\n' > "$APP_DIR/.gitignore"
    elif ! grep -qx '.env' "$APP_DIR/.gitignore"; then
        printf '.env\n' >> "$APP_DIR/.gitignore"
    fi
fi

# Ensure the session sentinels are gitignored (CASE-389). .session-id is
# per-session/ephemeral; .session-role is regenerated by --prefix / a re-run.
# Neither should ever be committed.
for _ign in '.claude/.session-id' '.claude/.session-role' '.claude/settings.local.json'; do
    if [ ! -f "$APP_DIR/.gitignore" ]; then
        printf '%s\n' "$_ign" > "$APP_DIR/.gitignore"
    elif ! grep -qx "$_ign" "$APP_DIR/.gitignore"; then
        printf '%s\n' "$_ign" >> "$APP_DIR/.gitignore"
    fi
done

# --- Initialise git ---

STEP_NUM=$((8 + STEP_OFFSET))
echo "$STEP_NUM. Initialising git repository..."
(cd "$APP_DIR" && git init -q && git add -A && git commit -q -m "Initial project setup for $APP_NAME

Generated by WIP create-app-project.sh from:
  $WIP_ROOT")
echo "   Git repo initialised with initial commit"
fi  # end of ! $REFRESH_MODE block (gitignore sentinels + git init)

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

# First command keyed on session STATE, not create-vs-setup (CASE-532 #1): a
# freshly-set-up checkout has no .claude/.session-id, so /wip-wake would
# correctly refuse — /wip-setup is the right entry. /wip-wake is only for
# continuing an existing session (after /clear or a compaction reset).
if [ -f "$APP_DIR/.claude/.session-id" ]; then
    FIRST_CMD="/wip-wake     # Continue: roll the prior session over + recover context"
else
    FIRST_CMD="/wip-setup    # First run: mint a session ID + load baseline context"
fi

echo "Next steps:"
echo "  cd $APP_DIR"
echo "  claude          # Launch Claude Code"
echo "  $FIRST_CMD"
if ! $REFRESH_MODE; then
    echo "  /wip-explore    # Then start Phase 1 (explore the domain)"
fi
echo ""
echo "Verify MCP connection:"
echo "  In Claude Code, run /mcp — you should see 94 tools and 5 resources."
if $REFRESH_MODE; then
    echo ""
    echo "Note: .mcp.json has been regenerated with paths for this machine."
    echo "      Add it to .gitignore if you don't want to commit machine-specific paths."
fi
