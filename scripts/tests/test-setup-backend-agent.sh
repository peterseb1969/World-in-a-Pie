#!/usr/bin/env bash
#
# Smoke test for setup-backend-agent.sh — the "clone → run → assert" path the
# backend scaffold lacked (CASE-537, the sibling of test-create-app-project.sh).
# Exercises the auto-detecting setup command end to end:
#
#   • first setup   — fresh clone (no .mcp.json) → set up, tier 2, /wip-setup guidance
#   • re-sync       — re-run the SAME clone with NO flag → auto-detected "Refreshing",
#                     does not error, guidance flips to /wip-wake once a session exists
#   • tier 3        — scheme-less --kb normalizes to https and writes kb.json (CASE-531),
#                     folding enablement into the one run (no --enable-kb)
#   • kb.json preserved — a re-sync WITHOUT --kb does not rewrite an existing kb.json
#   • removed flags — --refresh / --enable-kb are gone → unknown-option error (CASE-537)
#   • CASE-539 guard — the podman key-resolution line stays lockstep with the app scaffold
#
# In-situ model: the backend scaffold has NO target-dir arg — it writes into the
# WIP_ROOT it self-locates from its own path. So each scenario runs a COPY of the
# script inside a throwaway fixture WIP_ROOT (mktemp), seeded with the real
# slash-command sources and a stub .venv/bin/python so step 1 short-circuits
# (no real venv build, no network). The developer's real clone is never touched.
#
# Run from the WIP repo root:  ./scripts/tests/test-setup-backend-agent.sh

set -uo pipefail

WIP_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SRC="$WIP_ROOT/scripts/setup-backend-agent.sh"
export ALLOW_NON_DEVELOP=1

PASS=0
FAIL=0
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

ok()   { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

# mk_fixture — build a throwaway WIP_ROOT and echo its path.
mk_fixture() {
    local f; f="$(mktemp -d "$WORK/fixt.XXXXXX")"
    mkdir -p "$f/scripts" "$f/docs/slash-commands/backend" "$f/.claude" "$f/.venv/bin"
    cp "$SRC" "$f/scripts/"
    cp "$WIP_ROOT/docs/slash-commands/backend/"*.md "$f/docs/slash-commands/backend/"
    # Stub python: step 1's "venv exists" check short-circuits on --version, and
    # the wip_mcp import check + step-5 verify just need exit 0 (no real install).
    cat > "$f/.venv/bin/python" <<'PY'
#!/bin/sh
case "$1" in
  --version) echo "Python 3.13.0 (fixture stub)" ;;
  *) exit 0 ;;
esac
PY
    chmod +x "$f/.venv/bin/python"
    echo "$f"
}

run() { # run <fixture> <logfile> <args...> — returns the script's exit code
    local f="$1" log="$2"; shift 2
    "$f/scripts/setup-backend-agent.sh" "$@" >"$log" 2>&1
}

# ── Scenario A: first setup (tier 2) ───────────────────────────────────────
echo "Scenario A — first setup on a fresh clone (tier 2)"
A="$(mk_fixture)"
run "$A" "$WORK/a.log"
check "exit 0"                          "[ $? -eq 0 ]"
check "auto-detected first setup (not Refreshing)" "grep -q 'Setting up backend agent' '$WORK/a.log' && ! grep -q 'Refreshing' '$WORK/a.log'"
check ".mcp.json written"               "[ -f '$A/.mcp.json' ]"
check "CLAUDE.md written"               "[ -f '$A/CLAUDE.md' ]"
check "slash commands copied"           "ls '$A'/.claude/commands/*.md >/dev/null 2>&1"
check ".session-role == BE-YAC"         "[ \"\$(cat '$A/.claude/.session-role')\" = BE-YAC ]"
check "tier 2: no kb.json"              "[ ! -e '$A/.claude/kb.json' ]"
check "tier 2: no /wip-case stub"       "[ ! -e '$A/.claude/commands/wip-case.md' ]"
check "guidance points at /wip-setup"   "grep -q '/wip-setup' '$WORK/a.log'"
check "guidance NOT /wip-wake (no session yet)" "! grep -q '/wip-wake' '$WORK/a.log'"
check ".venv presence alone did NOT trip re-sync" "grep -q 'Setting up backend agent' '$WORK/a.log'"

# ── Scenario B: re-sync auto-detect (no flag, .mcp.json present) ────────────
echo "Scenario B — re-run the SAME clone with no flag → re-sync"
printf 'BE-YAC-20260101-000000\n' > "$A/.claude/.session-id"   # simulate a prior session
run "$A" "$WORK/b.log"
B_RC=$?
check "exit 0 (no refusal)"             "[ $B_RC -eq 0 ] && ! grep -qi 'already' '$WORK/b.log'"
check "auto-detected re-sync (Refreshing)" "grep -q 'Refreshing backend agent environment' '$WORK/b.log'"
check "guidance flips to /wip-wake (session-id present)" "grep -q '/wip-wake' '$WORK/b.log'"
check ".mcp.json still present"          "[ -f '$A/.mcp.json' ]"

# ── Scenario C: tier 3 via scheme-less --kb (CASE-531 + folded enablement) ──
echo "Scenario C — first setup with scheme-less --kb (tier 3, CASE-531)"
C="$(mk_fixture)"
run "$C" "$WORK/c.log" --kb 127.0.0.1:9
check "exit 0 (unreachable KB is non-fatal)" "[ $? -eq 0 ]"
check "kb.json written (tier 3)"        "[ -f '$C/.claude/kb.json' ]"
check "kb url normalized to https"      "grep -q '\"kb_app_url\": \"https://127.0.0.1:9\"' '$C/.claude/kb.json'"
check "/wip-case stub dropped (tier 3)" "[ -f '$C/.claude/commands/wip-case.md' ]"

# ── Scenario D: existing kb.json preserved on a re-sync WITHOUT --kb ────────
echo "Scenario D — re-sync without --kb must NOT rewrite kb.json (KB_OPT_IN gate)"
cp "$C/.claude/kb.json" "$WORK/kb-before.json"
run "$C" "$WORK/d.log"
check "exit 0"                          "[ $? -eq 0 ]"
check "kb.json unchanged (not rewritten)" "diff -q '$WORK/kb-before.json' '$C/.claude/kb.json' >/dev/null"

# ── Scenario E: removed mode flags now error (CASE-537) ─────────────────────
echo "Scenario E — --refresh / --enable-kb are gone → unknown-option error"
E="$(mk_fixture)"
run "$E" "$WORK/e1.log" --refresh
check "--refresh rejected (non-zero + Unknown option)" "[ $? -ne 0 ] && grep -q 'Unknown option' '$WORK/e1.log'"
run "$E" "$WORK/e2.log" --enable-kb
check "--enable-kb rejected (non-zero + Unknown option)" "[ $? -ne 0 ] && grep -q 'Unknown option' '$WORK/e2.log'"

# ── Scenario F: CASE-539 lockstep (both scaffolds share the byte-identical line) ─
echo "Scenario F — CASE-539 podman line is lockstep with the app scaffold"
# shellcheck disable=SC2034  # both vars are read inside the eval'd check strings below
BE_LINE="$(grep -oE "podman ps --format '\{\{\.Labels\}\}'.*sort -u \|\| true" "$SRC" || true)"
APP_LINE="$(grep -oE "podman ps --format '\{\{\.Labels\}\}'.*sort -u \|\| true" "$WIP_ROOT/scripts/create-app-project.sh" || true)"
check "CASE-539 fix present in backend"  "[ -n \"\$BE_LINE\" ]"
check "CASE-539 fix lockstep with app"   "[ \"\$BE_LINE\" = \"\$APP_LINE\" ]"

# ── Summary ────────────────────────────────────────────────────────────────
echo ""
echo "setup-backend-agent smoke: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
