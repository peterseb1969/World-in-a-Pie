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
#   3. Copies reference docs (AI-Assisted-Development.md, WIP_PoNIFs.md, WIP_DevGuardrails.md,
#      wip-guide.md, technology-stack.md, ui-guidance.md, ontology-support.md)
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
        if curl -fsSk -H "X-API-Key: $(cat "$KB_KEY_FILE")" \
            "$KB_URL/apps/kb/server-api/kb-client/install" | sh; then
            echo "   Served KB client installed/refreshed (~/.cache/wip-kb-client/)"
        else
            echo "   WARNING: served-client install failed; run the install one-liner"
            echo "            from docs/playbooks/case-workflow.md when KB is reachable."
        fi
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

# --- Copy slash commands (new + refresh) ---
# Canonical source is WIP. Gene pool updates must propagate to all repos.

echo "2. Copying slash commands..."
# Remove any existing commands first so renamed/retired gene-pool commands
# (e.g. the pre-CASE-390 un-prefixed setup.md/resume.md) don't linger alongside
# the current wip-* set. Matches setup-backend-agent.sh's refresh behavior.
rm -f "$APP_DIR/.claude/commands/"*.md 2>/dev/null || true
cp "$WIP_ROOT/docs/slash-commands/app-builder/"*.md "$APP_DIR/.claude/commands/"
if ! $TIER3; then
    # Tier 2: /wip-case is KB-backed collaboration — a tier-3 artifact.
    # Emitting it in a no-KB repo errors on the missing yac-discussions
    # symlink (CASE-463; the tier rule: no tier hard-depends on the one above).
    rm -f "$APP_DIR/.claude/commands/wip-case.md"
    echo "   Tier 2 (no KB): /wip-case stub omitted"
fi
echo "   Copied: $(find "$APP_DIR/.claude/commands/" -maxdepth 1 -type f | wc -l | tr -d ' ') commands"

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

# --- Session role marker (CASE-389) ---
# /wip-setup and /wip-wake read this to mint <PREFIX>-YYYYMMDD-HHMMSS session IDs.
if [ -n "$APP_PREFIX" ]; then
    printf '%s\n' "$APP_PREFIX" > "$APP_DIR/.claude/.session-role"
    echo "   Wrote: .claude/.session-role ($APP_PREFIX)"
elif [ -f "$APP_DIR/.claude/.session-role" ]; then
    echo "   Kept: .claude/.session-role ($(cat "$APP_DIR/.claude/.session-role"))"
else
    echo "   WARNING: no --prefix given and no .claude/.session-role present."
    echo "            /wip-setup and /wip-wake need it to mint session IDs."
    echo "            Re-run with --prefix APP-<X> (e.g. --prefix APP-KB)."
fi

# --- Persist app metadata (CASE-418) ---
# Sibling to .session-role, but committed (per-app, not per-machine): in-place setup
# reads it to regenerate CLAUDE.md with correct metadata instead of deriving
# from the directory name. Rewritten every run from the resolved values.
mkdir -p "$APP_DIR/.claude"
cat > "$APP_META_FILE" << META_EOF
# Generated by create-app-project.sh (CASE-418). Read on set-up-in-place to
# regenerate CLAUDE.md. Edit deliberately if the app's metadata changes.
APP_NAME="$APP_NAME"
APP_SLUG="$APP_SLUG"
DEV_NAMESPACE="$DEV_NAMESPACE"
PRESET="$PRESET"
META_EOF
echo "   Wrote: .claude/.app-meta ($APP_NAME / $DEV_NAMESPACE)"

# --- Generate committed .claude/settings.json baseline (CASE-446) ---
# Replaces the old create-time-only settings.local.json seed (CASE-169 +
# CASE-385). This file is 100% scaffold-owned and REGENERATED ON EVERY RUN
# (create AND set-up-in-place) — do not hand-edit; machine-/operator-specific
# rules belong in .claude/settings.local.json (no longer touched here).
# One unified baseline for BE + APP repos: the old per-role 31-pattern
# sets had already drifted (node/npm vs python) — the union ships to both.
#
# Shape verified against code.claude.com/docs/en/permissions.md
# (2026-06-12): deny > ask > allow regardless of specificity, so the
# destructive-verb `ask` entries reliably gate the broad allows. MCP
# partial-name wildcards (mcp__wip__get_*) are valid.
# find:* deliberately omitted (find -exec/-delete are destructive). git
# WRITE verbs deliberately omitted (commit/push/add/reset/branch -D stay
# human-gated); read-only git subcommands (log/status/diff/show/rev-parse/
# ls-files/blame) are allowed below — no destructive form.

cat > "$APP_DIR/.claude/settings.json" << 'EOF'
{
  "permissions": {
    "allow": [
      "Bash(cat:*)",
      "Bash(sed:*)",
      "Bash(awk:*)",
      "Bash(grep:*)",
      "Bash(tee:*)",
      "Bash(head:*)",
      "Bash(tail:*)",
      "Bash(tr:*)",
      "Bash(cut:*)",
      "Bash(wc:*)",
      "Bash(xargs:*)",
      "Bash(sort:*)",
      "Bash(uniq:*)",
      "Bash(diff:*)",
      "Bash(ls:*)",
      "Bash(pwd:*)",
      "Bash(stat:*)",
      "Bash(file:*)",
      "Bash(basename:*)",
      "Bash(dirname:*)",
      "Bash(realpath:*)",
      "Bash(which:*)",
      "Bash(type:*)",
      "Bash(test:*)",
      "Bash(command:*)",
      "Bash(python:*)",
      "Bash(python3:*)",
      "Bash(node:*)",
      "Bash(npm:*)",
      "Bash(npx:*)",
      "Bash(podman:*)",
      "Bash(docker:*)",
      "Bash(wip-deploy:*)",
      "Bash(kbc:*)",
      "Bash(curl:*)",
      "Bash(cd:*)",
      "Bash(echo:*)",
      "Bash(date:*)",
      "Bash(rg:*)",
      "Bash(jq:*)",
      "Bash(git log:*)",
      "Bash(git status:*)",
      "Bash(git diff:*)",
      "Bash(git show:*)",
      "Bash(git rev-parse:*)",
      "Bash(git ls-files:*)",
      "Bash(git blame:*)",
      "mcp__wip__get_*",
      "mcp__wip__list_*",
      "mcp__wip__query_*",
      "mcp__wip__search",
      "mcp__wip__search_registry",
      "mcp__wip__describe_data_model",
      "mcp__wip__lookup_entry",
      "mcp__wip__validate_*",
      "mcp__wip__export_*",
      "mcp__wip__run_report_query"
    ],
    "ask": [
      "Bash(wip-deploy nuke:*)",
      "Bash(podman rm:*)",
      "Bash(podman volume rm:*)",
      "Bash(podman system prune:*)",
      "Bash(docker rm:*)",
      "Bash(docker volume rm:*)",
      "Bash(docker system prune:*)",
      "Bash(npm publish:*)"
    ]
  },
  "hooks": {
    "SessionStart": [
      {
        "matcher": "compact",
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/post-compact-reanchor.sh"
          }
        ]
      }
    ]
  }
}
EOF
echo "   Written: .claude/settings.json (committed baseline — regenerated every run, CASE-446)"

# --- Generate the post-compaction re-anchor hook (CASE-480) ---
# SessionStart[matcher=compact] fires deterministically on every compaction
# (manual /compact, auto, or /resume). Compaction evicts the baseline reading
# /wip-setup + /wip-wake load, which is precisely when an agent drifts — runs on
# evicted context, loses the vision, takes the cheapest route (a sidecar model in
# metadata). Hooks CANNOT auto-run a slash command, so this re-injects the core
# modelling invariant + an instruction to run /wip-wake for the full rollover.
# Scaffold-owned, regenerated every run; verified against code.claude.com/docs/en/hooks.md.
mkdir -p "$APP_DIR/.claude/hooks"
cat > "$APP_DIR/.claude/hooks/post-compact-reanchor.sh" << 'HOOKEOF'
#!/usr/bin/env bash
# SessionStart[matcher=compact] re-anchor (CASE-480). Plain stdout on exit 0 is
# injected into context before the next turn. Do not auto-run slash commands here
# (unsupported) — nudge; running /wip-wake is the model's job.
cat <<'REANCHOR'
[post-compaction re-anchor] Context was just compacted — your baseline reading
(Vision.md, wip://ponifs, wip://data-model, wip://conventions, the deployable-app
contract) was likely evicted. This is NOT a seamless continuation; that feeling is
exactly when drift starts.

  → RUN /wip-wake NOW, before continuing. It rolls this session and reloads the
    baseline. Do not judge whether you "still remember" — you cannot reliably tell
    what compaction dropped.

Core invariant that must survive every compaction:
  WIP's primitives are your only data model — namespaces, terminologies, terms,
  templates, documents, files, relationships. metadata.* is a throwaway scratchpad,
  never identity, config, or schema. If your code reads metadata back as structure,
  you have built a sidecar model — stop and model it properly. A data-model change
  is a design event: get approval, do not inline it, do not route around it via
  metadata.
REANCHOR
HOOKEOF
chmod +x "$APP_DIR/.claude/hooks/post-compact-reanchor.sh"
echo "   Written: .claude/hooks/post-compact-reanchor.sh (SessionStart/compact re-anchor, CASE-480)"

# --- Copy slash command playbooks (new + refresh) ---
# Slim slash commands reference docs/playbooks/<name>.md (flat) for full procedures.
# Source layout in WIP is docs/playbooks/app-builder/, destination is flat docs/playbooks/.

if [ -d "$WIP_ROOT/docs/playbooks/app-builder" ]; then
    mkdir -p "$APP_DIR/docs/playbooks"
    cp "$WIP_ROOT/docs/playbooks/app-builder/"*.md "$APP_DIR/docs/playbooks/" 2>/dev/null || true
    # NOTE (CASE-522): we no longer delete docs/playbooks/case-workflow.md here.
    # The per-clone copy lane it once removed (CASE-440/463) is retired — the
    # /wip-case stub reads the served bundle at ~/.cache/wip-kb-client/ — so a
    # leftover copy is harmless in a normal app (nothing reads it). But WIP-KB
    # HOSTS the kb served bundle and serves THIS path as a load-bearing source
    # (server/kb-client.routes.ts PLAYBOOK_PATH), so deleting it on every
    # set-up-in-place broke the served playbook. The scaffold can't tell a stale copy
    # from a served source, and preserving it is harmless for the former and
    # mandatory for the latter — so we preserve.
    PLAYBOOK_COUNT=$(find "$APP_DIR/docs/playbooks/" -maxdepth 1 -name '*.md' -type f 2>/dev/null | wc -l | tr -d ' ')
    echo "   Copied: $PLAYBOOK_COUNT playbook(s) to docs/playbooks/"
else
    echo "   Warning: $WIP_ROOT/docs/playbooks/app-builder/ not found, skipping playbooks"
fi

# --- Copy reference docs (new + refresh) ---

echo "3. Copying reference documentation..."
for doc in AI-Assisted-Development.md WIP_PoNIFs.md WIP_DevGuardrails.md wip-guide.md technology-stack.md ui-guidance.md; do
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
//   scaffold behavior (that misread caused CASE-414).
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
#   4. Literal local-dev default, last resort.
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
    _wip_dirs="$(podman ps --format '{{index .Labels "com.docker.compose.project.working_dir"}}' 2>/dev/null | grep '/.wip-deploy/' | sort -u || true)"
    if [ "$(printf '%s\n' "$_wip_dirs" | grep -c .)" -eq 1 ] && [ -f "$_wip_dirs/secrets/api-key" ]; then
        WIP_API_KEY_FILE="$_wip_dirs/secrets/api-key"
        echo "   Detected running WIP install: $WIP_API_KEY_FILE"
    fi
fi
WIP_API_KEY_FILE="${WIP_API_KEY_FILE:-$HOME/.wip-deploy/wip-local/secrets/api-key}"
if [ ! -f "$WIP_API_KEY_FILE" ]; then
    echo "   Warning: $WIP_API_KEY_FILE does not exist (no running WIP install detected)."
    echo "            Deploy a WIP install or set WIP_API_KEY_FILE_OVERRIDE before using MCP."
fi

# Determine Python path
PYTHON_PATH="$WIP_ROOT/.venv/bin/python"
if [ ! -f "$PYTHON_PATH" ]; then
    PYTHON_PATH="$(which python3 2>/dev/null || which python 2>/dev/null || echo "python")"
    echo "   Warning: $WIP_ROOT/.venv/bin/python not found, using: $PYTHON_PATH"
fi

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

# --- Generate CLAUDE.md (both modes — CASE-418) ---
# Create mode writes CLAUDE.md directly. Refresh mode: missing file -> generate
# it (nothing to clobber); existing file -> render to CLAUDE.md.refresh + merge
# notice (app CLAUDE.md is generated-then-customised; a silent overwrite would
# clobber app-authored content), unless --force-claude-md.

CLAUDE_TARGET="$APP_DIR/CLAUDE.md"
CLAUDE_PREEXISTS=false
if $REFRESH_MODE && [ -f "$APP_DIR/CLAUDE.md" ] && ! $FORCE_CLAUDE_MD; then
    CLAUDE_TARGET="$APP_DIR/CLAUDE.md.refresh"
    CLAUDE_PREEXISTS=true
fi
if ! $REFRESH_MODE; then
STEP_NUM=$((7 + STEP_OFFSET))
echo "$STEP_NUM. Generating CLAUDE.md..."
else
echo "Generating ${CLAUDE_TARGET##*/} (metadata: $META_SOURCE)..."
fi
cat > "$CLAUDE_TARGET" << EOF
# $APP_NAME

<!-- last reviewed: 2026-05-07 -->

## What This App Does

> $APP_NAME — TODO: replace this line with what this app does.

## The Golden Rule

> **Never modify WIP. Build on top of it.**

WIP is the backend. This app is a frontend that maps a domain onto WIP's primitives (terminologies, templates, documents) and presents them to users.

**Verify before asserting any factual claim.** Any factual claim a cheap check could falsify — a file's contents, a function's location, a date, a count, a previous case's content — must be checked, not asserted from memory. "I'm pretty sure" is fabrication if you haven't run the check. The pattern has been observed across BE-YAC and FRanC; it is agent-agnostic.

## Dev Namespace

Your development namespace is \`$DEV_NAMESPACE\`. Use it for all data modeling during development.

**A missing namespace is never a setup failure.** The namespace is created at *bootstrap* — when you actually have data to put in it — not as a setup precondition. \`/wip-setup\` does not check for it, and its absence at session start is expected and non-blocking. If you have no data model yet (or this app is a cross-namespace console that owns no namespace of its own), there is simply nothing to create now — do **not** treat an absent \`$DEV_NAMESPACE\` as an error, fail setup over it, or go hunting for a namespace that was never provisioned.

**Why:** Terminologies and templates are hard to delete cleanly once documents reference them. A dev namespace lets you iterate freely — create, modify, delete, start over — without polluting production data.

**Workflow:**
1. Use \`$DEV_NAMESPACE\` for all \`/wip-design-model\` and \`/wip-implement\` work
2. Create terminologies, templates, and test documents in this namespace
3. Iterate until the data model is stable
4. When ready for production, create a new namespace (e.g., \`${APP_SLUG}\`) and recreate the finalized model there
5. Retire the dev namespace via the API when the data model is finalised:
   \`\`\`
   mcp__wip__delete_namespace(prefix="$DEV_NAMESPACE")
   \`\`\`
   (or \`DELETE /api/registry/namespaces/$DEV_NAMESPACE\`). The API
   honours each namespace's deletion mode (\`retain\` vs \`full\`) — no
   \`--force\` flag needed.

**Important:** MCP tool calls use the privileged admin key, so always pass \`namespace=$DEV_NAMESPACE\` explicitly when modeling. Your app's runtime key comes from \`WIP_API_KEY_FILE\` in \`.env\` (the wip-deploy secrets file) — see **API Key** below.

## API Key

The MCP server resolves its key from \`WIP_API_KEY_FILE\` (the live wip-deploy secrets file) — see \`.mcp.json\`. This is fine for data modeling via MCP tools.

**For your app's runtime API calls**, \`.env\` carries \`WIP_API_KEY_FILE\` pointing at that same live file. Resolve the key from the file at startup — the \`@wip/proxy\` \`apiKeyFile\` option does this for you, mirroring the MCP server — rather than baking a plaintext key. A key rotation or target-redeploy is then picked up on restart instead of stranding a stale \`.env\` (CASE-495). This is the deployment's admin/proxy key and spans all namespaces, which a cross-namespace console needs as-is.
EOF

cat >> "$CLAUDE_TARGET" << EOF
\`.env\` was written with \`WIP_API_KEY_FILE=$WIP_API_KEY_FILE\` — no plaintext key is baked in. The runtime reads the file at startup, so rotating the deploy key or redeploying the target needs no edit here.

\`\`\`bash
# .env (already created)
WIP_API_KEY_FILE=$WIP_API_KEY_FILE
\`\`\`

**Least-privilege (optional).** The deploy key is admin-scoped. If this app owns a single namespace and you want a least-privilege key, provision one and repoint \`WIP_API_KEY_FILE\` at its own secrets file:
\`\`\`bash
curl -k -X POST https://localhost:8443/api/registry/api-keys \\
  -H 'X-API-Key: <admin-key>' -H 'Content-Type: application/json' \\
  -d '{"name": "$APP_SLUG", "namespaces": ["$DEV_NAMESPACE"], "grant_permission": "write"}'
\`\`\`
EOF

cat >> "$CLAUDE_TARGET" << EOF

**Multi-namespace key → pass \`namespace\` explicitly.** The deploy admin/proxy key spans all namespaces, so WIP cannot derive one for you — pass \`namespace=$DEV_NAMESPACE\` on API calls that need scoping, or set \`defaultNamespace\` on \`@wip/proxy\` to scope reads (CASE-457). A single-namespace key (the least-privilege opt-in above) gets automatic derivation instead.

**Grants:** writes need an explicit namespace grant. If you provision a least-privilege key, pass \`grant_permission\` on \`create_api_key\` / \`POST /api/registry/api-keys\`, or add a grant afterwards (\`create_grant\` MCP tool, \`registry.createGrants\` in @wip/client, or \`POST /api/registry/namespaces/<ns>/grants\`). Grant subject for api keys is the bare key name.

**Key management:** Runtime keys can be listed, updated, and revoked via the Registry API. See WIP's \`docs/api-key-management.md\` for details.

## The wip-deployable app contract

**Read this before scaffolding any app code:** \`FR-YAC/papers/wip-deployable-app-contract.md\`. Four-line summary:

1. **Source repo** needs \`Dockerfile.dev\` + correct \`vite.config.ts\` (\`server.host: '0.0.0.0'\`, dev proxy targets *your* Express port, not 3001). Client fetches use \`import.meta.env.BASE_URL\`, never bare paths.
2. **WIP repo \`apps/<name>/wip-app.yaml\`** declares both http and dev ports, \`WIP_BASE_URL\` via \`from_component: router\`, \`APP_BASE_PATH\` literal, and a healthcheck that doesn't depend on WIP being reachable.
3. **Verify** with \`wip-deploy install --target dev --app <name> --app-source <name>=~/Development/WIP-<name>\` — SPA must load at \`https://localhost:8443/apps/<name>/\` on the first try, container healthy, no manual env patching.
4. **If something breaks**, find the failure signature in the paper's "What breaks when you skip step N" annex. Once \`/check-app-deployability\` ships, run it before considering your scaffold done.

The contract is target-agnostic — compose, k8s, and apps-only installs satisfy the same contract.

## Process

Follow the 4-phase development process.

If a \`KICKOFF.md\` exists in this directory, read it first — the kickoff supersedes the standard \`/wip-explore\` start for special-case apps (e.g. design-package-driven apps like APP-KB).

Otherwise start with:

\`\`\`
/wip-explore
\`\`\`

**Core phases** (in order):
1. \`/wip-explore\` — Read MCP resources, discover existing data model, understand the domain
2. \`/wip-design-model\` — Map the domain to WIP primitives (user must approve before proceeding)
3. \`/wip-implement\` — Create terminologies and templates in WIP, verify with test documents
4. \`/wip-build-app\` — Scaffold and build the React/TypeScript application

**After Phase 4:**
- \`/wip-improve\` — Iterate (add features, fix bugs, refine UI)
- \`/wip-document\` — Generate README, ARCHITECTURE, etc.

**Available at any time:**
- \`/wip-status\` — Check WIP service health and data state
- \`/wip-export-model\` — Save data model to git as seed files
- \`/wip-bootstrap\` — Recreate data model from seed files
- \`/wip-add-app\` — Add a second app that cross-references the first
- \`/wip-wake\` — Recover context after compaction or at start of a new session
- \`/wip-report\` — Capture fireside chat or trigger session summary
- \`/wip-deploy redeploy|verify\` — Redeploy this YAC's own source to the running dev install (or smoke-only). Subset of BE-YAC's \`/wip-deploy\` — install is BE-YAC's territory
<!--TIER3-->
- \`/wip-case file|list|read|respond|comment|close|implement\` — Cross-agent case management. **All KB reads/writes go through the served client — never a raw gateway curl:** \`kbc kb-write.py <TYPE> …\` (writes) / \`kbc case-fetch.py …\` (reads); the served playbook (\`~/.cache/wip-kb-client/case-workflow.md\`) is the version-matched source of truth for each verb. The gateway mints the \`CASE-<n>\` number + synonym and persists edges, but status-transition validity is enforced caller-side and a respond/close/implement is two writes (response doc + \`CASE_RECORD --patch status=…\`). Cases live in the KB, not on disk — never \`Write\` a case file with a hand-picked number; never reason about "the next number".
<!--/TIER3-->

**Context management:** When context reaches ~70-80%, the human should tell you to run \`/wip-wake\` or save state (DESIGN.md, memory files) before compaction hits.

## Namespace Bootstrap on Launch

Every WIP-consuming app must follow the **offer-on-empty / use-on-exists** discipline at runtime. This is a **runtime** discipline (triggered at app launch, when a user is present to act), **not a setup precondition** — a namespace absent at \`/wip-setup\` time is expected and creates no obligation; it is created here, at bootstrap, only when there is data to put in it. Three rules:

1. **Namespace missing on launch** → show the user an explicit bootstrap offer. Do **not** auto-bootstrap silently. The user can either (a) confirm bootstrap or (b) restore from a backup via the WIP console / \`wip-deploy\` first and reload.
2. **Namespace exists on launch** → use it as-is. **No** schema reconciliation, **no** "templates differ" check, **no** merge logic. Rolling redeploys against an existing namespace must come up clean. A partially-bootstrapped namespace is the user's signal to use the console, not the app's signal to silently re-bootstrap.
3. **On user-initiated bootstrap** → write one **\`BOOTSTRAP_RECORD\`** audit doc capturing: \`bootstrap_id\`, \`app_version\`, \`bootstrapped_at\`, \`commit_sha\`, \`templates_created\`, \`edge_types_created\`, \`terminologies_created\`. This is the provenance trail any future YAC reading the namespace can rely on.

**Restore is not an app concern.** The bootstrap UI mentions restore as an alternative the user may prefer; it does not provide UI for it. Restore is console-initiated.

**Starting point — three template files** are copied into \`templates/bootstrap/\` of every new app project:
- \`bootstrap.server.ts.template\` — \`checkStatus()\` and \`runBootstrap()\` library functions, with the §3.4 deltas (post-rename term-relations API, BOOTSTRAP_RECORD writing) already applied
- \`bootstrap.routes.ts.template\` — Express \`GET /server-api/bootstrap/status\` and \`POST /server-api/bootstrap/run\` (SSE streaming for progress)
- \`BootstrapGate.tsx.template\` — React component that wraps the app and renders the four states (checking / unreachable / needs-bootstrap / bootstrapping / error / ready)

Read each template's header comment, fill in the TODO markers (namespace, app title), drop a \`BOOTSTRAP_RECORD\` template into \`server/seed/templates/\`, and you're done. The seed-file convention (\`server/seed/terminologies/<VALUE>.json\`, \`server/seed/templates/<NN>_<VALUE>.json\`) is documented in the server template's header.

## Reference Documentation

Read these before starting:
- \`docs/AI-Assisted-Development.md\` — 4-phase process, data model design guide, PoNIFs quick reference
- \`docs/WIP_PoNIFs.md\` — Full guide to WIP's 8 non-intuitive behaviours
- \`docs/WIP_DevGuardrails.md\` — UI stack, app skeleton, testing conventions
- \`docs/wip-guide.md\` — Operator-facing guide: install, deploy, harden, run alongside an app (consolidates 10 prior docs incl. containerization, auth, networking, storage)
- \`docs/technology-stack.md\` — **Canonical** v1 stack (React 19 + TS + Vite + TanStack Query + Tailwind 3 + Inter); required @wip/* libraries; forbidden choices. Read before any architecture call.
- \`docs/ui-guidance.md\` — **Canonical** v1 visual anchor: brand palette tokens (primary/accent/success/danger), typography hierarchy (text-2xl page titles, NOT text-3xl), component shapes (cards, modals, tinted callouts), accessibility floor. \`tailwind.config.js\` ships pre-extended with these tokens — use the named classes (\`bg-primary\`, \`text-text-muted\`), not inline hex.
- \`docs/ontology-support.md\` — Term relations, polyhierarchy, typed relations, traversal queries
- \`templates/bootstrap/*.template\` — Bootstrap pattern starting points (see "Namespace Bootstrap on Launch" above)

## Key Identity Concepts

- **Identity hash ≠ canonical ID.** Identity hash = uniqueness key for upsert *within a specific template* — same field values under two different templates are two different documents. Canonical ID / synonyms = deterministic identification of exactly one entity across the entire system (Registry-resolved). When calling \`createDocumentsBulk\`, the identity hash is scoped to the template you pass — never assume it is unique across templates.
- **The Registry is the identity authority.** All identity resolution goes through the Registry. Do not implement app-side identity resolution by hash lookups — use the document_id returned by the API.
- **WIP's primitives are your only data model — \`metadata.*\` is a throwaway scratchpad, never a model.** Namespaces, terminologies, terms, templates, documents, files, relationships are the toolkit; if a value needs structure or meaning, it has a home among them. \`metadata.custom.<field>\` is caller-attached context (loader hints, source-system tags, audit traces) the platform makes no commitments about — NOT a home for anything the platform commits to a meaning for (identity, sortable axes, FTS-indexed text, dedup keys), and NOT a place to persist app state your code reads back. The moment your code branches on metadata, sorts by it, queries it as identity, or treats its shape as a schema, you have built a **sidecar model** — the failure this discipline exists to stop. Logic-driving fields live in \`data.<field>\` declared on the template's schema, with \`identity_fields\` / \`full_text_indexed\` / etc. referencing them; config that matters is a config *document* (create the config template first); a controlled vocabulary is **terms**. If a field your app needs has no home in \`data\`, that is a design event — file a case asking the template owner (often APP-KB-YAC for the kb namespace, BE-YAC for shared templates) to update the schema; do not stash it in \`metadata.custom\` as a workaround, and if you're unsure where it belongs, discuss it rather than inventing a shape. The platform hard-rejects \`metadata.*\` in declarative slots (\`identity_fields\`, \`full_text_indexed\`, \`sort_by\`), but **deliberately leaves the free-form path open**: filters on \`POST /documents/query\` stay free (ad-hoc reads, not declarative commitments), so the sidecar route is *not* blocked by the platform — the discipline is the guard. Enforced as a checkpoint in \`/wip-implement\` Step 0 and \`/wip-improve\` Rule 6.
- **Empty \`identity_fields\` is a first-class append-only mode**, not a degenerate config. The schema declares the contract: empty list = "every doc is its own logical entity, version-by-document_id-only." Use this deliberately for event logs and audit traces where every write is a fresh entity. Don't use it as a way to skip thinking about identity — if your records have a stable atomic identifier (case_number, ISBN, lot_id, tracking_id), declare it in \`data\` and reference it in \`identity_fields\`. **PATCH on an identity-less template fails with \`append_only\`** — you cannot update a document with no logical identity; create a new one instead. Relatedly, \`versioned: false\` templates (edge types included) must declare \`identity_fields\` explicitly (e.g. \`[source_ref, target_ref]\`) — there is no implicit default.

## MCP

WIP is accessed exclusively via MCP tools (94 tools, 5 resources). Before starting:
- Read \`wip://conventions\` — bulk-first API, identity hashing, versioning
- Read \`wip://data-model\` — terminologies, templates, documents, fields, term-relations
- Read \`wip://ponifs\` — 8 behaviours that trip up every new developer

\`wip://development-guide\` provides the full 4-phase workflow reference if needed.
\`wip://query-assistant-prompt\` provides a complete system prompt for NL query agents (used by --preset query apps).

**Query preset — runtime Anthropic key (CASE-509).** The \`--preset query\` agent resolves its Anthropic key in priority order: a key set at runtime via the admin \`/settings\` page → \`ANTHROPIC_API_KEY_FILE\` (0600, survives restart) → \`ANTHROPIC_API_KEY\` (frozen at process start). So an operator can set/rotate the key from the UI with no redeploy. Two deploy requirements for this to persist: (1) declare \`ANTHROPIC_API_KEY_FILE\` in \`apps/<name>/wip-app.yaml\` pointing at a **writable, persistent mount** (otherwise a UI-set key reverts on restart); (2) the \`/settings\` config endpoint is admin-gated via \`ADMIN_GROUPS\` (default \`wip-admins\`) — open only in dev mode (no \`OIDC_ISSUER\`). The key is a secret: never put it in a WIP document, and the server returns only configured/source/last-4, never the value.

## Client Libraries

For Phase 4 (app building), use @wip/client, @wip/react, and @wip/proxy:
- \`libs/wip-client-README.md\` — TypeScript client (6 services, error hierarchy, bulk abstraction)
- \`libs/wip-react-README.md\` — React hooks (TanStack Query, 30+ hooks)
- \`libs/wip-proxy-README.md\` — Express middleware for WIP API proxying with auth injection

**Phase 4 begins with:**
\`\`\`bash
npm install ./libs/wip-client-*.tgz ./libs/wip-react-*.tgz ./libs/wip-proxy-*.tgz @tanstack/react-query
\`\`\`
The WIP libs are tarballs in \`libs/\`. \`@tanstack/react-query\` is the peer dependency that powers \`@wip/react\`'s hooks — install it explicitly; the scaffold's \`package.json\` does not pre-declare it.

## Dev Setup Gotchas

**TLS:** WIP uses a self-signed cert on whichever hostname the install runs at — \`https://localhost:8443\` for compose dev, \`https://<ingress-hostname>\` for k8s (e.g. \`https://kb.internal\`). Node.js \`fetch()\` rejects self-signed certs; add \`NODE_TLS_REJECT_UNAUTHORIZED=0\` to your \`dev:server\` script (NOT \`start\`/production). The python wip_mcp client uses \`WIP_VERIFY_TLS=false\` (already set in \`.mcp.json\`). Production with proper certs needs no workaround.

**@wip/client baseUrl:** In browser apps behind a Vite proxy, use \`baseUrl: '/wip'\` (resolved to \`window.location.origin + '/wip'\`). Do NOT use a bare relative path without the client resolving it — \`new URL('/wip/...')\` throws without a protocol.

**@wip/react providers:** Hooks require BOTH \`QueryClientProvider\` (from \`@tanstack/react-query\`) AND \`WipProvider\` (from \`@wip/react\`). Missing either causes silent failure — hooks mount but never fetch, no errors.

## Tool use — Bash timeouts and waits

- **Never set Bash \`timeout > 60000\` ms.** Use \`run_in_background: true\` for any command that may exceed 60 s. Use \`Monitor\` for streaming output, or wait for the auto-completion notification when the background task finishes. A user-scoped PreToolUse hook (\`~/.claude/hooks/block-long-bash-timeout.sh\`) mechanically rejects calls with \`timeout > 60000\` — the discipline rule still applies even if the hook is disabled or absent. *Origin: this rule once lived only in feedback memory and still failed to prevent recurrence twice in 90 minutes within one session — hence the mechanical hook.*
- **Verify-before-wait.** Before scheduling any wait on a long-running command, verify the prerequisites that command depends on can succeed. For npm/test runs that hit a backend cluster: check the host-bound port (e.g., \`nc -z localhost 8443\`) before kicking the wait off. The class of failure is *waiting on an action that depends on unverified state* — the wait then can't complete and burns wall time on a hang. *Origin: an agent once waited 10 minutes for tests that couldn't finish because the deployer no longer exposed the relevant port.*
- **Bash hygiene — don't prefix commands with \`cd\`.** Your commands already run from the project root, so a \`cd\` prefix is unnecessary *and* trips approval prompts: \`cd "\${CLAUDE_PROJECT_DIR:-\$PWD}" && …\` forces an *expansion* prompt (shell expansion can't be statically verified against the allowlist), and \`cd dir && … > file\` forces a *path-bypass* prompt (the redirect could land outside an allowlisted path). Both are avoidable — use explicit / relative-to-root paths for reading **and** writing. Keeps inspection and file writes prompt-free *and* safer.

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
wip-toolkit --host kb.internal --proxy export kb /tmp/kb-backup.zip
\`\`\`

## Session Awareness

You will be replaced. This session — including everything you learn, every correction Peter makes, every insight you gain — ends when your context fills or the task completes. The next agent starts from scratch with no memory of this conversation.

**Consequence:** Anything worth knowing must be encoded into a durable artifact before this session ends. If Peter corrects your approach, consider whether the correction belongs in:
- A \`/wip-lesson\` entry (quick, structured, for future gene pool review)
- A session report "Dead Ends" section (for the next YAC continuing this work)
- A CLAUDE.md update (if Peter agrees it's universal)

Do not say "got it, won't happen again" unless you have written the lesson down. The next agent will make the same mistake unless you leave a trace.

## Scope Budget

Most tasks should complete within a predictable number of commits. If you find yourself significantly exceeding expectations, something is wrong — a misunderstanding, a rabbit hole, or a task that needs decomposition.

**Commit heuristics:**
- A bug fix: 1-3 commits. If you're past 5, stop and report what's blocking you.
- A feature addition: 3-7 commits. If you're past 10, stop and reassess scope with Peter.
- A refactor: 2-5 commits. If you're past 8, you're probably changing too much at once.

**Context window awareness:** You can check your own context usage:
\`\`\`bash
cat .claude-context-pct
\`\`\`
This file is written to your project directory by the status line. Check it periodically — especially before starting a new subtask.
- **Past 50%:** Ensure your session report and dead ends section are written. You are halfway to replacement.
- **Past 75%:** Stop working and write your session summary. Do not push through hoping to finish — the next YAC picks up faster from a clean summary than from a half-finished sprawl.

When stopping for any reason, write a clear status report: what's done, what's left, what's blocking, and what didn't work (dead ends).

## YAC Reporting

You are a YAC (Yet Another Claude). You report your work to the Field Reporter by writing files to a shared directory. This reporting is also useful for the *next* YAC — your session reports are input for future agents resuming your work.

**Getting the current time:** Always use \`date '+%Y-%m-%d %H:%M'\` for timestamps. Do not guess.

**Off the record:** If Peter says "off the record" or "don't report this," skip reporting for that segment. Resume when told.

### Session Identity

Your session ID is minted by \`/wip-setup\` (fresh start) or \`/wip-wake\` (continuation after \`/clear\` or compaction) and stored in \`.claude/.session-id\`. **Read it; never hand-mint or rotate it** — \`cat "\$CLAUDE_PROJECT_DIR/.claude/.session-id"\`. Those commands also create \`reports/<session-id>/\`, write the initial \`session.md\`, and (for \`/wip-wake\`) auto-close the prior session with \`continues_from\` linkage.

Your role prefix is read from \`.claude/.session-role\` (e.g. \`APP-KB\`), written at scaffold time by \`create-app-project.sh --prefix\`. **Do not** run \`date\`-based ID assignment yourself.

The \`session.md\` these commands create carries this frontmatter — the **local-first identity contract** (\`.claude/.session-id\` + this frontmatter are authoritative; the kb SESSION record is a derived mirror that catches up on the next reachable write):

\`\`\`yaml
---
session_id: APP-<X>-YYYYMMDD-HHMMSS
role: APP-<X>
started_at: YYYY-MM-DDTHH:MM:SS
status: active                      # flipped to \`closed\` by /wip-report session-end or /wip-wake
continues_from: <prior-session-id>  # present only on a /wip-wake continuation
---
\`\`\`

Seconds precision (\`HHMMSS\`) eliminates the same-minute collision class. Record the app, phase, and task list in the \`session.md\` body as you go; don't add a hand-written \`continues:\` field — \`/wip-wake\` writes \`continues_from\` as part of the rollover.

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
- Peter runs \`/wip-report session-end\`
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

When Peter initiates a design discussion, architecture debate, or scope conversation, use the \`/wip-report\` slash command to capture it. These are the high-value narrative moments — not just what was decided, but why, what alternatives were considered, and what Peter said.

### Running Log

For session-meaningful work that is **neither a change, an end-state, nor a fireside-grade decision**, append to \`session-updates.md\` via \`/wip-report update-session [terse note]\`. Three trigger categories:

1. **Discoveries without a commit anchor** — e.g., "scaffold imports \`./wip-api.js\` which doesn't exist anywhere."
2. **Scope-trim decisions mid-session** — why you're doing less than originally pitched, when the rationale matters for reading the resulting commit but isn't architectural enough for a fireside.
3. **Block/unblock state and pre-\`/compact\` snapshots** — written when context is filling so the post-compaction same-agent self has more than just the last commit message and a stale session.md.

**\`/compact\` vs \`/clear\`:** before \`/compact\` (same agent continues, conversation just summarized) write a running-log entry — this mode. Before \`/clear\` or end-of-day (next agent starts cold from durable artifacts) run \`/wip-report session-end\`. The two events look similar but have different recovery semantics.

Append-only — distinct from \`session.md\` (overwritten at end) and \`report-<slug>.md\` (per-decision). Each entry is **timestamp + short headline + one paragraph**.

Discipline test before writing: *"Would future-me, after a compaction, want to know this in 6 hours?"* If yes, write. If "this is just thinking out loud," don't.

The four files together — \`session.md\` + \`commits.md\` + \`session-updates.md\` + any \`report-*.md\` — are what \`/wip-wake\` reads to rebuild context.
EOF

# --- Tier filter (CASE-463) ---
# <!--TIER3--> ... <!--/TIER3--> regions in the heredocs are KB-collaboration
# content. Tier 3 keeps the content (markers stripped); tier 2 drops the
# regions. Markers never reach the emitted file.
if $TIER3; then
    sed -i '' '/^<!--TIER3-->$/d;/^<!--\/TIER3-->$/d' "$CLAUDE_TARGET"
else
    sed -i '' '/^<!--TIER3-->$/,/^<!--\/TIER3-->$/d' "$CLAUDE_TARGET"
fi
echo "   Written: ${CLAUDE_TARGET##*/}"
if $CLAUDE_PREEXISTS; then
    echo ""
    echo "   ============================================================"
    echo "   NOTICE (CASE-418): existing CLAUDE.md left untouched — it"
    echo "   carries app-authored content. A fresh scaffold render was"
    echo "   written to CLAUDE.md.refresh: merge the platform-owned"
    echo "   sections you want, then delete it. To overwrite outright,"
    echo "   re-run with --force-claude-md."
    echo "   ============================================================"
    echo ""
fi

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
