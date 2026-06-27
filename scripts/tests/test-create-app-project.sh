#!/usr/bin/env bash
#
# Smoke test for create-app-project.sh — the single end-to-end "clone → run →
# assert" path that CASE-535 introduced (and whose absence let CASE-531/532/534
# ship). Exercises the auto-detecting setup command end to end:
#
#   • create        — empty dir → full scaffold, tier 2, /wip-setup guidance
#   • set-up-in-place — re-run the SAME populated dir with NO flag → auto-detected,
#                       does not error "already exists", preserves CLAUDE.md
#   • tier 3        — scheme-less --kb normalizes to https and writes kb.json
#
# NOT isolated here: CASE-534's exact abort path (podman running but ZERO
# wip-deploy installs) needs a host with no install — these runs have one, so the
# grep matches. The `|| true` guard for it is verified by inspection; the smoke
# only confirms no regression on a host that DOES have an install.
#
# Run from the WIP repo root:  ./scripts/tests/test-create-app-project.sh
# Branch-guard is bypassed (ALLOW_NON_DEVELOP) so the test runs on any branch;
# it scaffolds throwaway dirs under mktemp, never a real app.

set -uo pipefail

WIP_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$WIP_ROOT/scripts/create-app-project.sh"
export ALLOW_NON_DEVELOP=1

PASS=0
FAIL=0
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

ok()   { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

run() { # run <logfile> <args...> — returns the script's exit code
    local log="$1"; shift
    "$SCRIPT" "$@" >"$log" 2>&1
}

# ── Scenario A: create (tier 2) ────────────────────────────────────────────
echo "Scenario A — create on an empty directory (tier 2)"
A="$WORK/app-a"
run "$WORK/a.log" "$A" --name "Smoke A" --prefix APP-SMK
check "exit 0"                       "[ $? -eq 0 ]"
check "slash commands copied"        "ls '$A'/.claude/commands/*.md >/dev/null 2>&1"
check ".mcp.json written"            "[ -f '$A/.mcp.json' ]"
check ".env written"                 "[ -f '$A/.env' ]"
check "CLAUDE.md written"            "[ -f '$A/CLAUDE.md' ]"
check ".session-role written"        "[ -f '$A/.claude/.session-role' ]"
check "git initialised"              "[ -d '$A/.git' ]"
check "tier 2: no kb.json"           "[ ! -e '$A/.claude/kb.json' ]"
check "guidance points at /wip-setup" "grep -q '/wip-setup' '$WORK/a.log'"
check "guidance NOT /wip-wake"        "! grep -q '/wip-wake ' '$WORK/a.log'"

# ── Scenario B: set-up-in-place auto-detect (no flag, populated dir) ────────
echo "Scenario B — re-run the SAME populated dir with no flag"
# Simulate a prior session so the guidance should flip to /wip-wake.
printf 'APP-SMK-20260101-000000\n' > "$A/.claude/.session-id"
run "$WORK/b.log" "$A"
check "exit 0 (no 'already exists' refusal)" "[ $? -eq 0 ] && ! grep -qi 'already exists' '$WORK/b.log'"
check "CLAUDE.md preserved (CLAUDE.md.refresh written)" "[ -f '$A/CLAUDE.md.refresh' ]"
check "guidance flips to /wip-wake (session-id present)" "grep -q '/wip-wake' '$WORK/b.log'"
check ".mcp.json still present"       "[ -f '$A/.mcp.json' ]"

# ── Scenario C: tier 3 via scheme-less --kb ────────────────────────────────
# Deliberately-unreachable host (127.0.0.1:9): scheme normalization + kb.json are
# written BEFORE any network call, so CASE-531 is exercised without POSTing a real
# SESSION_ROLE term to a live KB. enable_kb's curls fail fast and are non-fatal.
echo "Scenario C — create with scheme-less --kb (CASE-531 normalization)"
C="$WORK/app-c"
run "$WORK/c.log" "$C" --name "Smoke C" --prefix APP-SMK --kb 127.0.0.1:9
check "exit 0"                        "[ $? -eq 0 ]"
check "kb.json written (tier 3)"      "[ -f '$C/.claude/kb.json' ]"
check "kb url normalized to https"    "grep -q '\"kb_app_url\": \"https://127.0.0.1:9\"' '$C/.claude/kb.json'"

# ── Summary ────────────────────────────────────────────────────────────────
echo ""
echo "create-app-project smoke: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
