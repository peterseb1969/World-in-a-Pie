#!/usr/bin/env bash
# Tier-2 purity gate (CASE-463): scaffold a throwaway tier-2 app and assert
# the EMITTED tree carries no KB plumbing. Static source checks can't see
# what a generator emits — only generating and looking can (the behavioral
# half of check-doc-drift.py's retired-path check).
#
# The line this gate draws: per-clone GENERATED artifacts (CLAUDE.md, the
# command-set composition, .claude/kb.json) must be tier-pure. Tier-SHARED
# command bodies (wip-setup/wake/report) legitimately name kb.json inside
# their own tier gates ("skip if absent") — those are conditionals, not
# plumbing, and are exempt.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP_DIR="$(mktemp -d /tmp/tier2-purity.XXXXXX)"
trap 'rm -rf "$TMP_DIR"' EXIT

APP_DIR="$TMP_DIR/app"
echo "Scaffolding tier-2 app into $APP_DIR ..."
"$SCRIPT_DIR/create-app-project.sh" "$APP_DIR" --name "Purity Probe" --prefix APP-PP > "$TMP_DIR/scaffold.log" 2>&1 || {
    echo "FAIL: tier-2 scaffold itself failed; last lines:"
    tail -5 "$TMP_DIR/scaffold.log"
    exit 1
}

FAILS=0
fail() { echo "FAIL: $1"; FAILS=$((FAILS + 1)); }

[ -e "$APP_DIR/.claude/commands/wip-case.md" ] \
    && fail "tier-2 tree contains the /wip-case stub (tier-3 artifact)"
[ -e "$APP_DIR/docs/playbooks/case-workflow.md" ] \
    && fail "tier-2 tree contains a case-workflow.md copy (retired lane)"
[ -e "$APP_DIR/.claude/kb.json" ] \
    && fail "tier-2 scaffold wrote .claude/kb.json (tier flag without --kb)"

if grep -qE "wip-kb-client|yac-discussions|wip-case" "$APP_DIR/CLAUDE.md"; then
    fail "tier-2 CLAUDE.md carries KB references (tier filter broken):"
    grep -nE "wip-kb-client|yac-discussions|wip-case" "$APP_DIR/CLAUDE.md" | head -3
fi

if grep -rq "TIER3" "$APP_DIR" --include="*.md" 2>/dev/null; then
    fail "TIER3 markers leaked into emitted files (filter did not run):"
    grep -rln "TIER3" "$APP_DIR" --include="*.md" | head -3
fi

if [ "$FAILS" -eq 0 ]; then
    echo "OK: tier-2 emitted tree is pure (no KB plumbing)."
else
    echo "$FAILS purity violation(s)."
    exit 1
fi
