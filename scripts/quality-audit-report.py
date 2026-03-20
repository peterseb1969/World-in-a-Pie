#!/usr/bin/env python3
"""Quality Audit Report Generator.

Reads raw JSON/text from reports/quality-audit/raw/ and produces a unified REPORT.md.
Optionally updates or checks against a baseline.
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def get_git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True
        ).strip()
    except Exception:
        return "unknown"


def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def load_text(path: Path) -> str:
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return ""


def count_ruff(raw_dir: Path) -> int:
    data = load_json(raw_dir / "ruff.json", [])
    return len(data) if isinstance(data, list) else 0


def count_shellcheck(raw_dir: Path) -> int:
    data = load_json(raw_dir / "shellcheck.json", [])
    return len(data) if isinstance(data, list) else 0


def count_vulture(raw_dir: Path) -> int:
    text = load_text(raw_dir / "vulture.txt")
    return len([l for l in text.splitlines() if l.strip()]) if text else 0


def count_ts_prune(raw_dir: Path) -> int:
    text = load_text(raw_dir / "ts-prune.txt")
    return len([l for l in text.splitlines() if l.strip()]) if text else 0


def count_mypy(raw_dir: Path) -> tuple[int, dict]:
    data = load_json(raw_dir / "mypy.json", {})
    total = sum(v.get("count", 0) for v in data.values())
    return total, data


def count_vue_tsc(raw_dir: Path) -> int:
    text = load_text(raw_dir / "vue-tsc.txt")
    if not text:
        return 0
    import re
    return len(re.findall(r"error TS\d+", text))


def count_eslint(raw_dir: Path) -> int:
    data = load_json(raw_dir / "eslint.json", [])
    if not isinstance(data, list):
        return 0
    return sum(len(f.get("messages", [])) for f in data)


def get_api_consistency(raw_dir: Path) -> dict:
    return load_json(raw_dir / "api-consistency.json", {"total_violations": 0, "services": {}})


def get_radon(raw_dir: Path) -> list:
    data = load_json(raw_dir / "radon.json", {})
    items = []
    for filepath, functions in data.items():
        for func in functions:
            items.append({
                "file": filepath,
                "name": func.get("name", "?"),
                "complexity": func.get("complexity", 0),
                "rank": func.get("rank", "?"),
                "lineno": func.get("lineno", 0),
                "type": func.get("type", "function"),
            })
    items.sort(key=lambda x: x["complexity"], reverse=True)
    return items


def get_ruff_breakdown(raw_dir: Path) -> dict:
    data = load_json(raw_dir / "ruff.json", [])
    if not isinstance(data, list):
        return {}
    breakdown = {}
    for item in data:
        code = item.get("code", "unknown")
        breakdown[code] = breakdown.get(code, 0) + 1
    return dict(sorted(breakdown.items(), key=lambda x: x[1], reverse=True))


def get_coverage_summary(raw_dir: Path) -> list:
    """Read pytest-cov JSON reports."""
    results = []
    import glob
    for f in sorted(glob.glob(str(raw_dir / "pytest-cov-*.json"))):
        if "-html" in f:
            continue
        name = os.path.basename(f).replace("pytest-cov-", "").replace(".json", "")
        data = load_json(Path(f), {})
        totals = data.get("totals", {})
        results.append({
            "component": name,
            "statements": totals.get("num_statements", 0),
            "missing": totals.get("missing_lines", 0),
            "coverage": totals.get("percent_covered", 0),
        })
    return results


def status_icon(count, baseline_count=None) -> str:
    if count == 0:
        return "PASS"
    if baseline_count is not None and count > baseline_count:
        return "FAIL"
    return "WARN"


def delta_str(count, baseline_count) -> str:
    if baseline_count is None:
        return "—"
    diff = count - baseline_count
    if diff == 0:
        return "0"
    elif diff > 0:
        return f"+{diff}"
    else:
        return str(diff)


def generate_report(raw_dir: Path, mode: str, baseline: dict | None) -> str:
    """Generate the REPORT.md content."""
    sha = get_git_sha()
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    dims = baseline.get("dimensions", {}) if baseline else {}

    # Gather counts
    ruff_count = count_ruff(raw_dir)
    shellcheck_count = count_shellcheck(raw_dir)
    vulture_count = count_vulture(raw_dir)
    ts_prune_count = count_ts_prune(raw_dir)
    mypy_count, mypy_details = count_mypy(raw_dir)
    vue_tsc_count = count_vue_tsc(raw_dir)
    eslint_count = count_eslint(raw_dir)
    api_data = get_api_consistency(raw_dir)
    radon_items = get_radon(raw_dir)
    ruff_breakdown = get_ruff_breakdown(raw_dir)
    coverage = get_coverage_summary(raw_dir)

    lines = []
    w = lines.append

    w("# WIP Quality Audit Report")
    w(f"Generated: {timestamp} | Commit: {sha} | Mode: {mode}")
    w("")

    # Summary table
    w("## Summary")
    w("")
    w("| Dimension | Status | Issues | Baseline | Delta |")
    w("|-----------|--------|--------|----------|-------|")

    dimensions = [
        ("Ruff (Python lint)", "ruff", ruff_count),
        ("mypy (Python types)", "mypy", mypy_count),
        ("Vulture (dead Python code)", "vulture", vulture_count),
        ("ShellCheck", "shellcheck", shellcheck_count),
        ("ESLint (Vue/TS lint)", "eslint", eslint_count),
        ("vue-tsc (Vue types)", "vue-tsc", vue_tsc_count),
        ("ts-prune (unused exports)", "ts-prune", ts_prune_count),
    ]

    ci_failures = []
    for label, key, count in dimensions:
        bl = dims.get(key, {}).get("count")
        status = status_icon(count, bl)
        delta = delta_str(count, bl)
        bl_str = str(bl) if bl is not None else "—"
        w(f"| {label} | {status} | {count} | {bl_str} | {delta} |")
        if status == "FAIL":
            ci_failures.append((label, count, bl))

    w("")

    # Section 1: Dead Code
    w("## 1. Dead Code")
    w("")
    w(f"### Python (vulture) — {vulture_count} issues")
    w("")
    vulture_text = load_text(raw_dir / "vulture.txt")
    if vulture_text:
        for line in vulture_text.splitlines()[:20]:
            w(f"- `{line}`")
        if vulture_count > 20:
            w(f"- ... and {vulture_count - 20} more")
    else:
        w("No dead code detected.")
    w("")

    w(f"### TypeScript (ts-prune) — {ts_prune_count} unused exports")
    w("")
    ts_prune_text = load_text(raw_dir / "ts-prune.txt")
    if ts_prune_text:
        for line in ts_prune_text.splitlines()[:15]:
            if line.strip():
                w(f"- `{line.strip()}`")
        if ts_prune_count > 15:
            w(f"- ... and {ts_prune_count - 15} more")
    else:
        w("No unused exports detected (or ts-prune not available).")
    w("")

    # Section 2: Type Safety
    w("## 2. Type Safety")
    w("")
    w(f"### Python (mypy) — {mypy_count} errors")
    w("")
    if mypy_details:
        w("| Component | Errors |")
        w("|-----------|--------|")
        for comp, data in sorted(mypy_details.items()):
            w(f"| {comp} | {data.get('count', 0)} |")
        w("")
        # Show top errors
        all_errors = []
        for comp, data in mypy_details.items():
            for err in data.get("errors", [])[:5]:
                all_errors.append(err)
        if all_errors:
            w("**Top errors:**")
            for err in all_errors[:15]:
                w(f"- `{err}`")
            w("")
    else:
        w("No mypy data available.")
        w("")

    w(f"### Vue/TypeScript (vue-tsc) — {vue_tsc_count} errors")
    w("")
    vue_tsc_text = load_text(raw_dir / "vue-tsc.txt")
    if vue_tsc_text and vue_tsc_count > 0:
        for line in vue_tsc_text.splitlines()[:15]:
            if "error TS" in line:
                w(f"- `{line.strip()}`")
    else:
        w("No vue-tsc errors (or not available).")
    w("")

    # Section 3: Linting
    w("## 3. Linting")
    w("")
    w(f"### Python (ruff) — {ruff_count} issues")
    w("")
    if ruff_breakdown:
        w("| Rule | Count |")
        w("|------|-------|")
        for rule, count in list(ruff_breakdown.items())[:15]:
            w(f"| {rule} | {count} |")
        w("")
    else:
        w("No ruff issues.")
        w("")

    w(f"### Vue/TypeScript (eslint) — {eslint_count} issues")
    w("")
    eslint_data = load_json(raw_dir / "eslint.json", [])
    if isinstance(eslint_data, list) and eslint_count > 0:
        rule_counts = {}
        for f in eslint_data:
            for msg in f.get("messages", []):
                rule = msg.get("ruleId", "unknown")
                rule_counts[rule] = rule_counts.get(rule, 0) + 1
        if rule_counts:
            w("| Rule | Count |")
            w("|------|-------|")
            for rule, count in sorted(rule_counts.items(), key=lambda x: x[1], reverse=True)[:10]:
                w(f"| {rule} | {count} |")
            w("")
    else:
        w("No ESLint issues (or not available).")
        w("")

    w(f"### Shell (shellcheck) — {shellcheck_count} issues")
    w("")
    sc_data = load_json(raw_dir / "shellcheck.json", [])
    if isinstance(sc_data, list) and sc_data:
        sc_codes = {}
        for item in sc_data:
            code = f"SC{item.get('code', '?')}"
            sc_codes[code] = sc_codes.get(code, 0) + 1
        w("| Code | Count |")
        w("|------|-------|")
        for code, count in sorted(sc_codes.items(), key=lambda x: x[1], reverse=True)[:10]:
            w(f"| {code} | {count} |")
        w("")
    else:
        w("No shellcheck issues.")
        w("")

    # Section 4: Test Coverage
    w("## 4. Test Coverage")
    w("")
    if mode == "quick":
        w("*Skipped in quick mode. Run without `--quick` for coverage data.*")
    else:
        w("### Python")
        w("")
        if coverage:
            w("| Component | Stmts | Miss | Cover% |")
            w("|-----------|-------|------|--------|")
            for c in coverage:
                w(f"| {c['component']} | {c['statements']} | {c['missing']} | {c['coverage']:.1f}% |")
        else:
            w("No coverage data available (requires MongoDB for service tests).")
        w("")

        w("### TypeScript")
        w("")
        # Check for vitest coverage
        has_ts_cov = False
        import glob
        for f in glob.glob(str(raw_dir / "vitest-cov-*" / "coverage-summary.json")):
            has_ts_cov = True
            lib_name = Path(f).parent.name.replace("vitest-cov-", "")
            data = load_json(Path(f), {})
            totals = data.get("total", {})
            stmts = totals.get("statements", {})
            branches = totals.get("branches", {})
            w(f"| {lib_name} | Stmts: {stmts.get('pct', 0):.1f}% | Branches: {branches.get('pct', 0):.1f}% |")
        if not has_ts_cov:
            w("No TypeScript coverage data available.")
    w("")

    # Section 5: Complexity
    w("## 5. Complexity Hotspots")
    w("")
    if radon_items:
        w(f"Top {min(20, len(radon_items))} functions by cyclomatic complexity (CC >= C):")
        w("")
        w("| Rank | Function | CC | File:Line |")
        w("|------|----------|----|-----------|")
        for item in radon_items[:20]:
            short_file = item["file"].split("components/")[-1] if "components/" in item["file"] else item["file"]
            w(f"| {item['rank']} | {item['name']} | {item['complexity']} | {short_file}:{item['lineno']} |")
        w("")
    else:
        w("No functions with complexity >= C (11+). Well done!")
        w("")

    # Section 6: API Consistency
    w("## 6. API Consistency")
    w("")
    api_violations = api_data.get("total_violations", 0)
    if api_violations == 0:
        w(f"All {api_data.get('total_endpoints', 0)} endpoints across {api_data.get('files_checked', 0)} files are compliant.")
    else:
        w(f"**{api_violations} violations** found:")
        w("")
        for svc, svc_data in api_data.get("services", {}).items():
            if svc_data.get("violation_count", 0) > 0:
                w(f"### {svc} ({svc_data['violation_count']} violations)")
                w("")
                for v in svc_data.get("violations", []):
                    w(f"- [{v.get('rule', '?')}] `{v.get('function', '?')}` (line {v.get('line', '?')}): {v.get('message', '')}")
                w("")
    w("")

    # Section 7: Dependency Health
    w("## 7. Dependency Health")
    w("")
    pip_audit = load_json(raw_dir / "pip-audit.json", [])
    if isinstance(pip_audit, list) and pip_audit:
        w(f"### pip-audit — {len(pip_audit)} vulnerabilities")
        w("")
        for vuln in pip_audit[:10]:
            w(f"- {vuln.get('name', '?')} {vuln.get('version', '?')}: {vuln.get('id', '?')}")
    else:
        w("### pip-audit — clean (or not installed)")
    w("")

    # npm outdated
    import glob as glob_mod
    npm_files = sorted(glob_mod.glob(str(raw_dir / "npm-outdated-*.json")))
    if npm_files:
        for f in npm_files:
            project = os.path.basename(f).replace("npm-outdated-", "").replace(".json", "")
            data = load_json(Path(f), {})
            if data:
                w(f"### npm outdated — {project} ({len(data)} packages)")
                w("")
                w("| Package | Current | Wanted | Latest |")
                w("|---------|---------|--------|--------|")
                for pkg, info in sorted(data.items()):
                    if isinstance(info, dict):
                        w(f"| {pkg} | {info.get('current', '?')} | {info.get('wanted', '?')} | {info.get('latest', '?')} |")
                w("")
    w("")

    # Section 8: Security
    w("## 8. Security Audit")
    w("")
    w("> Planned as a separate dedicated session.")
    w("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate WIP quality audit report")
    parser.add_argument("--raw-dir", required=True, help="Directory with raw tool output")
    parser.add_argument("--output", required=True, help="Output REPORT.md path")
    parser.add_argument("--mode", default="full", choices=["quick", "full"])
    parser.add_argument("--baseline", help="Path to baseline.json")
    parser.add_argument("--update-baseline", action="store_true", help="Update baseline with current counts")
    parser.add_argument("--ci", action="store_true", help="Fail if any dimension exceeds baseline")
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir)
    baseline = None
    if args.baseline and Path(args.baseline).exists():
        baseline = load_json(Path(args.baseline))

    report = generate_report(raw_dir, args.mode, baseline)

    # Write report
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report + "\n")
    print(f"Report written to {args.output}", file=sys.stderr)

    # Update baseline if requested
    if args.update_baseline and args.baseline:
        dims = baseline.get("dimensions", {}) if baseline else {}
        new_baseline = {
            "generated": datetime.now(timezone.utc).isoformat(),
            "commit": get_git_sha(),
            "dimensions": {
                "ruff": {"count": count_ruff(raw_dir)},
                "mypy": {"count": count_mypy(raw_dir)[0]},
                "vulture": {"count": count_vulture(raw_dir)},
                "eslint": {"count": count_eslint(raw_dir)},
                "shellcheck": {"count": count_shellcheck(raw_dir)},
                "vue-tsc": {"count": count_vue_tsc(raw_dir)},
                "ts-prune": {"count": count_ts_prune(raw_dir)},
            },
        }
        Path(args.baseline).write_text(json.dumps(new_baseline, indent=2) + "\n")
        print(f"Baseline updated: {args.baseline}", file=sys.stderr)

    # CI mode: check against baseline
    if args.ci and baseline:
        dims = baseline.get("dimensions", {})
        failures = []
        checks = [
            ("ruff", count_ruff(raw_dir)),
            ("mypy", count_mypy(raw_dir)[0]),
            ("vulture", count_vulture(raw_dir)),
            ("eslint", count_eslint(raw_dir)),
            ("shellcheck", count_shellcheck(raw_dir)),
            ("vue-tsc", count_vue_tsc(raw_dir)),
            ("ts-prune", count_ts_prune(raw_dir)),
        ]
        for key, current in checks:
            bl = dims.get(key, {}).get("count")
            if bl is not None and current > bl:
                failures.append(f"{key}: {current} (baseline: {bl})")

        if failures:
            print(f"CI FAILURE — regressions detected:", file=sys.stderr)
            for f in failures:
                print(f"  - {f}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
