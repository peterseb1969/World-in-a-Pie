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
#   • local config  — .env / .mcp.json / kb.json are gitignored and untracked, the
#                     pre-commit gate is enabled, a refresh flags a tracked file
#   • no key file   — --kb without --kb-key fails before writing kb.json
#   • namespace     — a new app's namespace is its slug; an existing app keeps
#                     the one it was set up with (DEV_NAMESPACE in .app-meta, an
#                     old '## Dev Namespace' heading, a --name refresh)
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
check "local config not committed"   "[ -z \"\$(git -C '$A' ls-files -- .env .mcp.json .claude/kb.json)\" ]"
check ".mcp.json gitignored"          "grep -qx '.mcp.json' '$A/.gitignore'"
check "pre-commit gate shipped"       "[ -x '$A/.githooks/pre-commit' ]"
check "pre-commit gate enabled"       "[ \"\$(git -C '$A' config --local core.hooksPath)\" = .githooks ]"
check "namespace is the slug"         "grep -qx 'APP_NAMESPACE=\"smoke-a\"' '$A/.claude/.app-meta'"
check "CLAUDE.md names it under ## Namespace" "sed -n '/^## Namespace\$/,/^## /p' '$A/CLAUDE.md' | grep -q '\`smoke-a\`'"
check "no namespace written to the install" "! grep -qi 'upserted' '$WORK/a.log'"

# ── Scenario B: set-up-in-place auto-detect (no flag, populated dir) ────────
echo "Scenario B — re-run the SAME populated dir with no flag"
# Simulate a prior session so the guidance should flip to /wip-wake.
printf 'APP-SMK-20260101-000000\n' > "$A/.claude/.session-id"
run "$WORK/b.log" "$A"
check "exit 0 (no 'already exists' refusal)" "[ $? -eq 0 ] && ! grep -qi 'already exists' '$WORK/b.log'"
check "CLAUDE.md preserved (CLAUDE.md.refresh written)" "[ -f '$A/CLAUDE.md.refresh' ]"
check "guidance flips to /wip-wake (session-id present)" "grep -q '/wip-wake' '$WORK/b.log'"
check ".mcp.json still present"       "[ -f '$A/.mcp.json' ]"

# A clone made before the sentinels existed has .mcp.json committed: the
# refresh must name the untrack command, and the gate must refuse it.
echo "Scenario B2 — refresh flags tracked local config; the gate refuses it"
git -C "$A" add -f .mcp.json >/dev/null 2>&1
git -C "$A" commit -q --no-verify -m "simulate an old commit of .mcp.json"
run "$WORK/b2.log" "$A"
check "refresh warns about tracked .mcp.json" "grep -q 'rm --cached .mcp.json' '$WORK/b2.log'"
printf '\n' >> "$A/.mcp.json"
git -C "$A" add -f .mcp.json >/dev/null 2>&1
check "gate refuses a staged .mcp.json" "! git -C '$A' commit -q -m 'should be refused' >/dev/null 2>&1"
git -C "$A" reset -q

# ── Scenario C: tier 3 via scheme-less --kb ────────────────────────────────
# Deliberately-unreachable host (127.0.0.1:9): scheme normalization + kb.json are
# written BEFORE any network call, so CASE-531 is exercised without POSTing a real
# SESSION_ROLE term to a live KB. enable_kb's curls fail fast and are non-fatal.
echo "Scenario C — create with scheme-less --kb (CASE-531 normalization)"
C="$WORK/app-c"
printf 'smoke-kb-key-not-a-real-key\n' > "$WORK/kb-key"
run "$WORK/c.log" "$C" --name "Smoke C" --prefix APP-SMK --kb 127.0.0.1:9 --kb-key "$WORK/kb-key"
check "exit 0"                        "[ $? -eq 0 ]"
check "kb.json written (tier 3)"      "[ -f '$C/.claude/kb.json' ]"
check "kb url normalized to https"    "grep -q '\"kb_app_url\": \"https://127.0.0.1:9\"' '$C/.claude/kb.json'"
check "kb.json records the given key file" "grep -q '\"kb_api_key_file\": \"$WORK/kb-key\"' '$C/.claude/kb.json'"
check "kb.json.example shipped"       "[ -f '$C/.claude/kb.json.example' ]"
check "kb.json not committed"         "[ -z \"\$(git -C '$C' ls-files -- .claude/kb.json)\" ]"

# ── Scenario D: --kb without a key file fails before writing anything ──────
echo "Scenario D — --kb without --kb-key, and with an unreadable key file"
D="$WORK/app-d"
run "$WORK/d.log" "$D" --name "Smoke D" --prefix APP-SMK --kb 127.0.0.1:9
check "exit non-zero"                 "[ $? -ne 0 ]"
check "error names --kb-key"          "grep -q 'needs --kb-key' '$WORK/d.log'"
check "no kb.json written"            "[ ! -e '$D/.claude/kb.json' ]"
D2="$WORK/app-d2"
run "$WORK/d2.log" "$D2" --name "Smoke D2" --prefix APP-SMK --kb 127.0.0.1:9 --kb-key "$WORK/missing-key"
check "missing key file: exit non-zero" "[ $? -ne 0 ] && grep -q 'not readable' '$WORK/d2.log'"

# ── Scenario E: an existing app keeps its namespace ────────────────────────
echo "Scenario E — refreshes keep the namespace an app was set up with"
E="$WORK/app-e"
run "$WORK/e.log" "$E" --name "Smoke E" --prefix APP-SMK
# E1: .app-meta from before the rename records DEV_NAMESPACE.
sed -i.bak 's/^APP_NAMESPACE=.*/DEV_NAMESPACE="dev-smoke-e"/' "$E/.claude/.app-meta"
run "$WORK/e1.log" "$E"
check "E1 exit 0"                         "[ $? -eq 0 ]"
check "E1 keeps dev-smoke-e"              "grep -q 'Namespace: dev-smoke-e' '$WORK/e1.log'"
check "E1 records it as APP_NAMESPACE"    "grep -qx 'APP_NAMESPACE=\"dev-smoke-e\"' '$E/.claude/.app-meta'"
# E2: no .app-meta, a CLAUDE.md with the old heading (backfill).
rm "$E/.claude/.app-meta"
# shellcheck disable=SC2016  # literal backticks: a `code`-formatted namespace in markdown
printf '# Smoke E\n\n## Dev Namespace\n\nYour development namespace is `dev-old-e`.\n\n## Next\n' > "$E/CLAUDE.md"
run "$WORK/e2.log" "$E"
check "E2 exit 0"                         "[ $? -eq 0 ]"
check "E2 backfills dev-old-e"            "grep -q 'Namespace: dev-old-e' '$WORK/e2.log'"
# E3: a --name refresh with no .app-meta reads CLAUDE.md, never the slug default.
rm "$E/.claude/.app-meta"
run "$WORK/e3.log" "$E" --name "Smoke E"
check "E3 exit 0"                         "[ $? -eq 0 ]"
check "E3 keeps dev-old-e"                "grep -q 'Namespace: dev-old-e' '$WORK/e3.log'"

# ── Summary ────────────────────────────────────────────────────────────────
echo ""
echo "create-app-project smoke: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
