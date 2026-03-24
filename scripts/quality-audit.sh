#!/usr/bin/env bash
set -euo pipefail

# WIP Quality Audit — Master orchestration script
# Runs all quality checks and generates a unified report.
#
# Usage:
#   ./scripts/quality-audit.sh              # Full audit (needs MongoDB for coverage)
#   ./scripts/quality-audit.sh --quick      # Skip coverage (no services needed)
#   ./scripts/quality-audit.sh --fix        # Auto-fix ruff + eslint issues
#   ./scripts/quality-audit.sh --ci         # Fail if issues exceed baseline
#   ./scripts/quality-audit.sh --update-baseline  # Write current counts to baseline

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Activate project venv if present and not already active
if [ -z "${VIRTUAL_ENV:-}" ] && [ -f "$ROOT_DIR/.venv/bin/activate" ]; then
    . "$ROOT_DIR/.venv/bin/activate"
fi
RAW_DIR="$ROOT_DIR/reports/quality-audit/raw"
REPORT_DIR="$ROOT_DIR/reports/quality-audit"

# Parse flags
QUICK=false
CI_MODE=false
FIX=false
UPDATE_BASELINE=false

for arg in "$@"; do
    case "$arg" in
        --quick) QUICK=true ;;
        --ci) CI_MODE=true ;;
        --fix) FIX=true ;;
        --update-baseline) UPDATE_BASELINE=true ;;
        --help|-h)
            echo "Usage: $0 [--quick] [--ci] [--fix] [--update-baseline]"
            echo ""
            echo "  --quick            Skip coverage steps (no MongoDB/services needed)"
            echo "  --ci               Exit non-zero if any dimension exceeds baseline"
            echo "  --fix              Auto-fix ruff and eslint issues"
            echo "  --update-baseline  Write current counts to baseline.json"
            exit 0
            ;;
        *) echo "Unknown flag: $arg"; exit 1 ;;
    esac
done

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail()  { echo -e "${RED}[FAIL]${NC} $*"; }

# Track timing
TOTAL_START=$(date +%s)
step_time() {
    local elapsed=$(( $(date +%s) - $1 ))
    echo "${elapsed}s"
}

mkdir -p "$RAW_DIR"

# ─── Step 0: Preflight ───────────────────────────────────────────────
info "Preflight: checking tool availability..."

MISSING=()
check_tool() {
    if ! command -v "$1" &>/dev/null; then
        MISSING+=("$1")
    fi
}

check_tool ruff
check_tool mypy
check_tool vulture
check_tool radon
check_tool shellcheck

if [ ${#MISSING[@]} -gt 0 ]; then
    fail "Missing tools: ${MISSING[*]}"
    echo ""
    echo "Install with:"
    echo "  pip install ruff mypy vulture radon"
    echo "  brew install shellcheck  # or: pip install shellcheck-py"
    exit 1
fi

# Check npm tools (optional — skip steps if not available)
HAS_ESLINT=false
HAS_VUE_TSC=false
HAS_TS_PRUNE=false
HAS_VITEST=false

for _ed in ui/wip-console libs/wip-client libs/wip-react; do
    if [ -f "$ROOT_DIR/$_ed/node_modules/.bin/eslint" ]; then HAS_ESLINT=true; break; fi
done
if [ -f "$ROOT_DIR/ui/wip-console/node_modules/.bin/vue-tsc" ]; then HAS_VUE_TSC=true; fi
if command -v npx &>/dev/null && [ -d "$ROOT_DIR/libs/wip-client/node_modules" ]; then HAS_TS_PRUNE=true; fi
if [ -f "$ROOT_DIR/libs/wip-client/node_modules/.bin/vitest" ]; then HAS_VITEST=true; fi

ok "Preflight complete"

# ─── Step 1: Ruff (Python lint) ──────────────────────────────────────
info "Step 1: Ruff..."
STEP_START=$(date +%s)

RUFF_TARGETS=(
    "$ROOT_DIR/components/registry/src"
    "$ROOT_DIR/components/def-store/src"
    "$ROOT_DIR/components/template-store/src"
    "$ROOT_DIR/components/document-store/src"
    "$ROOT_DIR/components/reporting-sync/src"
    "$ROOT_DIR/components/ingest-gateway/src"
    "$ROOT_DIR/components/mcp-server/src"
    "$ROOT_DIR/libs/wip-auth/src"
)

if $FIX; then
    ruff check --fix --config "$ROOT_DIR/pyproject.toml" "${RUFF_TARGETS[@]}" 2>&1 || true
fi

ruff check --config "$ROOT_DIR/pyproject.toml" --output-format=json "${RUFF_TARGETS[@]}" \
    > "$RAW_DIR/ruff.json" 2>&1 || true

RUFF_COUNT=$(python3 -c "import json; print(len(json.load(open('$RAW_DIR/ruff.json'))))" 2>/dev/null || echo "?")
ok "Ruff: $RUFF_COUNT issues ($(step_time $STEP_START))"

# ─── Step 2: ShellCheck ──────────────────────────────────────────────
info "Step 2: ShellCheck..."
STEP_START=$(date +%s)

SHELL_FILES=$(find "$ROOT_DIR/scripts" -name '*.sh' -type f 2>/dev/null || true)
if [ -n "$SHELL_FILES" ]; then
    # ShellCheck reads .shellcheckrc from the file's directory or parents
    shellcheck --format=json $SHELL_FILES > "$RAW_DIR/shellcheck.json" 2>&1 || true
    SHELLCHECK_COUNT=$(python3 -c "import json; print(len(json.load(open('$RAW_DIR/shellcheck.json'))))" 2>/dev/null || echo "?")
else
    echo "[]" > "$RAW_DIR/shellcheck.json"
    SHELLCHECK_COUNT=0
fi
ok "ShellCheck: $SHELLCHECK_COUNT issues ($(step_time $STEP_START))"

# ─── Step 3: Vulture (dead Python code) ──────────────────────────────
info "Step 3: Vulture..."
STEP_START=$(date +%s)

vulture \
    "$ROOT_DIR/components/registry/src" \
    "$ROOT_DIR/components/def-store/src" \
    "$ROOT_DIR/components/template-store/src" \
    "$ROOT_DIR/components/document-store/src" \
    "$ROOT_DIR/components/reporting-sync/src" \
    "$ROOT_DIR/components/ingest-gateway/src" \
    "$ROOT_DIR/libs/wip-auth/src" \
    "$ROOT_DIR/vulture_allowlist.py" \
    --min-confidence 80 \
    > "$RAW_DIR/vulture.txt" 2>&1 || true

VULTURE_COUNT=$(wc -l < "$RAW_DIR/vulture.txt" | tr -d ' ')
ok "Vulture: $VULTURE_COUNT issues ($(step_time $STEP_START))"

# ─── Step 4: ts-prune (unused TS exports) ────────────────────────────
if $HAS_TS_PRUNE; then
    info "Step 4: ts-prune..."
    STEP_START=$(date +%s)

    TS_PRUNE_OUTPUT=""
    for lib_dir in "$ROOT_DIR/libs/wip-client" "$ROOT_DIR/libs/wip-react"; do
        if [ -d "$lib_dir" ]; then
            cd "$lib_dir"
            TS_PRUNE_OUTPUT+="$(npx -y ts-prune 2>/dev/null | grep -v '(used in module)' | grep -v 'index.ts' || true)"$'\n'
        fi
    done
    cd "$ROOT_DIR"
    echo "$TS_PRUNE_OUTPUT" > "$RAW_DIR/ts-prune.txt"
    TS_PRUNE_COUNT=$(echo "$TS_PRUNE_OUTPUT" | grep -c . || echo "0")
    ok "ts-prune: $TS_PRUNE_COUNT unused exports ($(step_time $STEP_START))"
else
    warn "Step 4: ts-prune — skipped (not installed)"
    echo "" > "$RAW_DIR/ts-prune.txt"
    TS_PRUNE_COUNT="skipped"
fi

# ─── Step 5: Radon (complexity) ──────────────────────────────────────
info "Step 5: Radon..."
STEP_START=$(date +%s)

radon cc \
    "$ROOT_DIR/components/registry/src" \
    "$ROOT_DIR/components/def-store/src" \
    "$ROOT_DIR/components/template-store/src" \
    "$ROOT_DIR/components/document-store/src" \
    "$ROOT_DIR/components/reporting-sync/src" \
    "$ROOT_DIR/components/ingest-gateway/src" \
    "$ROOT_DIR/libs/wip-auth/src" \
    --min C --json \
    > "$RAW_DIR/radon.json" 2>&1 || true

RADON_COUNT=$(python3 -c "
import json
data = json.load(open('$RAW_DIR/radon.json'))
count = sum(len(v) for v in data.values())
print(count)
" 2>/dev/null || echo "?")
ok "Radon: $RADON_COUNT complex functions (CC >= C) ($(step_time $STEP_START))"

# ─── Step 6: mypy (Python type checking) ─────────────────────────────
info "Step 6: mypy..."
STEP_START=$(date +%s)

MYPY_TOTAL=0
MYPY_RESULTS="{}"

for component_dir in \
    "$ROOT_DIR/components/registry/src" \
    "$ROOT_DIR/components/def-store/src" \
    "$ROOT_DIR/components/template-store/src" \
    "$ROOT_DIR/components/document-store/src" \
    "$ROOT_DIR/components/reporting-sync/src" \
    "$ROOT_DIR/components/ingest-gateway/src" \
    "$ROOT_DIR/libs/wip-auth/src"; do

    component_name=$(echo "$component_dir" | sed "s|$ROOT_DIR/||" | sed 's|/src||' | sed 's|/|-|g')
    mypy "$component_dir" --config-file "$ROOT_DIR/pyproject.toml" \
        --no-error-summary 2>&1 \
        > "$RAW_DIR/mypy-${component_name}.txt" || true
    count=$(grep -c '^.*: error:' "$RAW_DIR/mypy-${component_name}.txt" 2>/dev/null || echo "0")
    MYPY_TOTAL=$((MYPY_TOTAL + count))
done

# Combine into single JSON summary
python3 -c "
import json, glob, re, os
results = {}
for f in sorted(glob.glob('$RAW_DIR/mypy-*.txt')):
    name = os.path.basename(f).replace('mypy-', '').replace('.txt', '')
    with open(f) as fh:
        errors = [l.strip() for l in fh if ': error:' in l]
    results[name] = {'count': len(errors), 'errors': errors[:20]}
json.dump(results, open('$RAW_DIR/mypy.json', 'w'), indent=2)
" 2>/dev/null || true

ok "mypy: $MYPY_TOTAL errors ($(step_time $STEP_START))"

# ─── Step 7: vue-tsc (Vue/TS type checking) ──────────────────────────
if $HAS_VUE_TSC; then
    info "Step 7: vue-tsc..."
    STEP_START=$(date +%s)

    cd "$ROOT_DIR/ui/wip-console"
    npx vue-tsc --noEmit 2>&1 > "$RAW_DIR/vue-tsc.txt" || true
    cd "$ROOT_DIR"

    VUE_TSC_COUNT=$(grep -c '^.*error TS' "$RAW_DIR/vue-tsc.txt" 2>/dev/null || echo "0")
    ok "vue-tsc: $VUE_TSC_COUNT errors ($(step_time $STEP_START))"
else
    warn "Step 7: vue-tsc — skipped (not installed)"
    echo "" > "$RAW_DIR/vue-tsc.txt"
    VUE_TSC_COUNT="skipped"
fi

# ─── Step 8: ESLint ──────────────────────────────────────────────────
if $HAS_ESLINT; then
    info "Step 8: ESLint..."
    STEP_START=$(date +%s)

    ESLINT_ARGS=("--format" "json")
    if $FIX; then
        ESLINT_ARGS+=("--fix")
    fi

    # Lint each project separately (each has its own node_modules/eslint)
    ESLINT_ALL="[]"

    for eslint_project in "ui/wip-console" "libs/wip-client" "libs/wip-react"; do
        eslint_dir="$ROOT_DIR/$eslint_project"
        if [ -f "$eslint_dir/node_modules/.bin/eslint" ]; then
            cd "$eslint_dir"
            npx eslint "${ESLINT_ARGS[@]}" "src" \
                > "$RAW_DIR/eslint-$(basename "$eslint_project").json" 2>&1 || true
            cd "$ROOT_DIR"
        else
            echo "[]" > "$RAW_DIR/eslint-$(basename "$eslint_project").json"
        fi
    done

    # Merge all ESLint JSON outputs
    python3 -c "
import json, glob
merged = []
for f in sorted(glob.glob('$RAW_DIR/eslint-*.json')):
    try:
        data = json.load(open(f))
        if isinstance(data, list):
            merged.extend(data)
    except (json.JSONDecodeError, FileNotFoundError):
        pass
json.dump(merged, open('$RAW_DIR/eslint.json', 'w'), indent=2)
" 2>/dev/null || true

    ESLINT_COUNT=$(python3 -c "
import json
data = json.load(open('$RAW_DIR/eslint.json'))
print(sum(len(f.get('messages', [])) for f in data))
" 2>/dev/null || echo "?")
    ok "ESLint: $ESLINT_COUNT issues ($(step_time $STEP_START))"
else
    warn "Step 8: ESLint — skipped (not installed; run npm install in ui/wip-console)"
    echo "[]" > "$RAW_DIR/eslint.json"
    ESLINT_COUNT="skipped"
fi

# ─── Step 9: pytest-cov (Python coverage) ────────────────────────────
if ! $QUICK; then
    info "Step 9: pytest-cov..."
    STEP_START=$(date +%s)

    PYTEST_COV_RESULTS=()
    for component in registry def-store template-store document-store reporting-sync ingest-gateway; do
        component_dir="$ROOT_DIR/components/$component"
        if [ ! -d "$component_dir/tests" ]; then continue; fi

        # Determine the Python package name (replace - with _)
        pkg_name=$(echo "$component" | tr '-' '_')

        # ingest-gateway needs tests/ on PYTHONPATH for shared fixtures
        local_pypath="src"
        if [ "$component" = "ingest-gateway" ]; then local_pypath="src:tests"; fi

        info "  pytest-cov: $component..."
        cd "$component_dir"
        PYTHONPATH="$local_pypath" python3 -m pytest tests/ \
            --cov="src/$pkg_name" \
            --cov-report=json:"$RAW_DIR/pytest-cov-${component}.json" \
            --cov-report=html:"$RAW_DIR/pytest-cov-${component}-html" \
            -q --tb=no 2>&1 || true
        cd "$ROOT_DIR"
    done

    ok "pytest-cov complete ($(step_time $STEP_START))"
else
    warn "Step 9: pytest-cov — skipped (--quick mode)"
fi

# ─── Step 10: vitest coverage (TS libraries) ─────────────────────────
if ! $QUICK && $HAS_VITEST; then
    info "Step 10: vitest coverage..."
    STEP_START=$(date +%s)

    for lib in wip-client wip-react; do
        lib_dir="$ROOT_DIR/libs/$lib"
        if [ -d "$lib_dir" ]; then
            cd "$lib_dir"
            npx vitest run --coverage --coverage.reporter=json \
                --coverage.reportsDirectory="$RAW_DIR/vitest-cov-${lib}" \
                2>&1 || true
            cd "$ROOT_DIR"
        fi
    done

    ok "vitest coverage complete ($(step_time $STEP_START))"
elif ! $QUICK; then
    warn "Step 10: vitest coverage — skipped (vitest not installed)"
else
    warn "Step 10: vitest coverage — skipped (--quick mode)"
fi

# ─── Step 11: API consistency check ──────────────────────────────────
info "Step 11: API consistency..."
STEP_START=$(date +%s)

python3 "$SCRIPT_DIR/api-consistency-check.py" \
    --root "$ROOT_DIR" \
    --output "$RAW_DIR/api-consistency.json" \
    2>&1 || true

API_VIOLATIONS=$(python3 -c "
import json
data = json.load(open('$RAW_DIR/api-consistency.json'))
print(data.get('total_violations', '?'))
" 2>/dev/null || echo "?")
ok "API consistency: $API_VIOLATIONS violations ($(step_time $STEP_START))"

# ─── Step 12: Dependency health ──────────────────────────────────────
info "Step 12: Dependency health..."
STEP_START=$(date +%s)

DEP_RESULTS="{}"

# pip-audit (if available)
if command -v pip-audit &>/dev/null; then
    pip-audit --format json 2>/dev/null > "$RAW_DIR/pip-audit.json" || true
else
    echo "[]" > "$RAW_DIR/pip-audit.json"
fi

# npm outdated for each JS project
for project_dir in "$ROOT_DIR/ui/wip-console" "$ROOT_DIR/libs/wip-client" "$ROOT_DIR/libs/wip-react"; do
    if [ -d "$project_dir/node_modules" ]; then
        project_name=$(basename "$project_dir")
        cd "$project_dir"
        npm outdated --json > "$RAW_DIR/npm-outdated-${project_name}.json" 2>&1 || true
        cd "$ROOT_DIR"
    fi
done

ok "Dependency health ($(step_time $STEP_START))"

# ─── Step 13: Generate report ────────────────────────────────────────
info "Step 13: Generating report..."
STEP_START=$(date +%s)

MODE="full"
if $QUICK; then MODE="quick"; fi

python3 "$SCRIPT_DIR/quality-audit-report.py" \
    --raw-dir "$RAW_DIR" \
    --output "$REPORT_DIR/REPORT.md" \
    --mode "$MODE" \
    $(if $UPDATE_BASELINE; then echo "--update-baseline --baseline $REPORT_DIR/baseline.json"; fi) \
    $(if $CI_MODE; then echo "--ci --baseline $REPORT_DIR/baseline.json"; fi) \
    2>&1

ok "Report generated ($(step_time $STEP_START))"

# ─── Summary ─────────────────────────────────────────────────────────
TOTAL_ELAPSED=$(( $(date +%s) - TOTAL_START ))
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
info "Quality audit complete in ${TOTAL_ELAPSED}s"
info "Report: $REPORT_DIR/REPORT.md"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
