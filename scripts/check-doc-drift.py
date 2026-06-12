#!/usr/bin/env python3
"""Mechanical doc-drift detection (CASE-456).

Two checks, both grounded in source so they cannot rot:

1. TS lib export completeness — every *value* export (function, class,
   const, enum, hook) reachable from each lib's src/index.ts, plus every
   public service-class method in wip-client's services/, must be mentioned
   in that lib's README. Type-only exports (interface/type) are excluded:
   documenting every type in a README is typedoc territory (CASE-456 item 4),
   and requiring it here would bury real findings in noise. The CASE-450
   failure class (registry.listGrants existed, no doc mentioned it) is a
   service-method finding — index.ts symbols alone would not have caught it.

2. Count claims — the actual number of @mcp.tool()/@mcp.resource()
   decorations in the MCP server, diffed against every "<N> tools" /
   "<N> resources" literal in docs/ and scripts/. Hand-maintained counts of
   generated things drift with ordinary feature work (88 vs 91 vs 94 were
   all simultaneously claimed on 2026-06-12).

3. Retired paths (CASE-462) — docs/ and scripts/ must not instruct callers
   to invoke retired tooling. A behavioral check, not a consistency check:
   a stub that matches its source passes every diff while both copies are
   wrong about the world. Patterns cover the pre-CASE-440 FR-YAC kb tools
   and the pre-CASE-425 FS case allocator.

Exit 0 in default warn mode; --strict exits 1 on any drift.
"""

import argparse
import json
import re
import sys
from pathlib import Path

TS_LIBS = ["wip-client", "wip-react", "wip-proxy"]

EXPORT_BRACE_RE = re.compile(r"export\s+(type\s+)?\{([^}]*)\}", re.DOTALL)
EXPORT_DECL_RE = re.compile(
    r"export\s+(?:async\s+)?(function|class|const|let|var|enum)\s+(\w+)"
)
EXPORT_STAR_RE = re.compile(r"export\s+\*\s+from\s+['\"]([^'\"]+)['\"]")
SERVICE_METHOD_RE = re.compile(r"^\s{2}(?:public\s+)?async\s+(\w+)\(", re.MULTILINE)
COUNT_CLAIM_RE = re.compile(r"\b(\d+)\s+(tools|resources)\b")

# Tooling invocations retired by served-client cutovers (CASE-425/437/440/462).
# Live invocations go through ~/.cache/wip-kb-client/kb-client.sh.
RETIRED_PATH_PATTERNS = [
    ("FR-YAC/tools/", "kb tools moved to the served bundle (CASE-440/462)"),
    ('realpath yac-discussions)")/tools/', "case-fetch via FR-YAC checkout (CASE-462)"),
    ("case-helper.sh claim", "FS claim retired for served case_allocate (CASE-425/437)"),
]


def resolve_module(from_file: Path, spec: str) -> Path | None:
    """Resolve a './x.js' import spec to the .ts source file."""
    base = (from_file.parent / spec).resolve()
    candidates = [
        base.with_suffix(".ts"),
        base.with_suffix(".tsx"),
        base.parent / base.stem / "index.ts",
    ]
    # './x.js' resolves to a Path ending in '.js'; strip it for dir form
    if base.suffix == ".js":
        candidates.append(base.with_suffix("") / "index.ts")
    return next((c for c in candidates if c.is_file()), None)


def collect_value_exports(entry: Path, seen: set[Path] | None = None) -> set[str]:
    """Recursively collect value-export names reachable from a TS entry file."""
    seen = seen if seen is not None else set()
    if entry in seen or not entry.is_file():
        return set()
    seen.add(entry)
    src = entry.read_text()
    names: set[str] = set()

    for type_kw, body in EXPORT_BRACE_RE.findall(src):
        if type_kw:  # export type { ... } — type-only block
            continue
        body = re.sub(r"//[^\n]*", "", body)
        for item in body.split(","):
            item = item.strip()
            if not item or item.startswith("type "):
                continue
            # 'Name as Alias' exports Alias
            names.add(item.split(" as ")[-1].strip())

    for _kind, name in EXPORT_DECL_RE.findall(src):
        names.add(name)

    for spec in EXPORT_STAR_RE.findall(src):
        target = resolve_module(entry, spec)
        if target:
            names |= collect_value_exports(target, seen)

    return names


def collect_service_methods(services_dir: Path) -> dict[str, str]:
    """Public async methods of wip-client service classes: name -> file."""
    methods: dict[str, str] = {}
    for f in sorted(services_dir.glob("*.ts")):
        if f.name == "base.ts":
            continue
        src = f.read_text()
        # strip private methods before matching
        src = re.sub(r"^\s{2}private\s.*$", "", src, flags=re.MULTILINE)
        for name in SERVICE_METHOD_RE.findall(src):
            methods[name] = f.name
    return methods


def count_decorations(server_py: Path) -> dict[str, int]:
    src = server_py.read_text()
    return {
        "tools": len(re.findall(r"@mcp\.tool\(", src)),
        "resources": len(re.findall(r"@mcp\.resource\(", src)),
    }


def find_count_claims(root: Path) -> list[dict]:
    claims = []
    scan_dirs = [root / "docs", root / "scripts"]
    for d in scan_dirs:
        for f in sorted(d.rglob("*")):
            if f.suffix not in {".md", ".sh", ".py"} or not f.is_file():
                continue
            if f.name == Path(__file__).name:
                continue
            for lineno, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
                for n, kind in COUNT_CLAIM_RE.findall(line):
                    claims.append(
                        {
                            "file": str(f.relative_to(root)),
                            "line": lineno,
                            "claimed": int(n),
                            "kind": kind,
                            "text": line.strip()[:120],
                        }
                    )
    return claims


def find_retired_paths(root: Path) -> list[dict]:
    hits = []
    for d in [root / "docs", root / "scripts"]:
        for f in sorted(d.rglob("*")):
            if f.suffix not in {".md", ".sh", ".py"} or not f.is_file():
                continue
            if f.name == Path(__file__).name:
                continue
            for lineno, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
                # Retirement notices legitimately name the old path
                # ("X replaced case-helper.sh claim") — mention, not use.
                if re.search(r"\bretired?\b|\breplace[ds]?\b", line):
                    continue
                for pattern, why in RETIRED_PATH_PATTERNS:
                    if pattern in line:
                        hits.append(
                            {
                                "file": str(f.relative_to(root)),
                                "line": lineno,
                                "pattern": pattern,
                                "why": why,
                            }
                        )
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--output", type=Path, help="write JSON findings here")
    ap.add_argument("--strict", action="store_true", help="exit 1 on any drift")
    args = ap.parse_args()
    root = args.root

    findings = {
        "undocumented": {},
        "count_mismatches": [],
        "actual_counts": {},
        "retired_paths": [],
    }

    # ── Check 1: TS lib export completeness ──
    for lib in TS_LIBS:
        lib_dir = root / "libs" / lib
        readme = lib_dir / "README.md"
        index = lib_dir / "src" / "index.ts"
        if not readme.is_file() or not index.is_file():
            print(f"  SKIP {lib}: missing README.md or src/index.ts")
            continue
        readme_text = readme.read_text()
        symbols = {name: "index.ts" for name in collect_value_exports(index)}
        if lib == "wip-client":
            symbols.update(collect_service_methods(lib_dir / "src" / "services"))
        missing = {
            name: origin
            for name, origin in sorted(symbols.items())
            if not re.search(rf"\b{re.escape(name)}\b", readme_text)
        }
        findings["undocumented"][lib] = missing
        status = f"{len(missing)} undocumented of {len(symbols)} value exports"
        print(f"  {lib}: {status}")
        for name, origin in missing.items():
            print(f"    - {name}  ({origin})")

    # ── Check 2: count claims vs decorations ──
    server_py = root / "components" / "mcp-server" / "src" / "wip_mcp" / "server.py"
    actual = count_decorations(server_py)
    findings["actual_counts"] = actual
    print(f"  mcp-server actual: {actual['tools']} tools, {actual['resources']} resources")
    for claim in find_count_claims(root):
        # Per-category counts ("Document tools (13 tools)") are not total-count
        # claims; only flag numbers in the total's neighborhood (>= half actual).
        if claim["claimed"] < actual[claim["kind"]] * 0.5:
            continue
        if claim["claimed"] != actual[claim["kind"]]:
            findings["count_mismatches"].append(claim)
            print(
                f"  STALE COUNT {claim['file']}:{claim['line']} claims "
                f"{claim['claimed']} {claim['kind']} (actual {actual[claim['kind']]})"
            )

    # ── Check 3: retired tooling paths ──
    findings["retired_paths"] = find_retired_paths(root)
    for hit in findings["retired_paths"]:
        print(
            f"  RETIRED PATH {hit['file']}:{hit['line']} uses '{hit['pattern']}' "
            f"— {hit['why']}"
        )

    if args.output:
        args.output.write_text(json.dumps(findings, indent=2) + "\n")

    drift = (
        bool(findings["count_mismatches"])
        or bool(findings["retired_paths"])
        or any(findings["undocumented"].values())
    )
    if drift and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
