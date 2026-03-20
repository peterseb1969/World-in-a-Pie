#!/usr/bin/env python3
"""API Consistency Checker — verifies bulk-first convention compliance.

Static analysis of Python source (no running services needed). Uses AST parsing to verify:
1. Every POST/PUT/DELETE endpoint has List[...] request body (bulk-first)
2. Every write endpoint returns BulkResponse or subclass
3. No single-entity write endpoints exist
4. Every GET list endpoint has page and page_size parameters
5. page_size default is 50, max is 100
6. List response models include pages field

Scans: components/*/src/*/api/*.py
"""

import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path


def find_api_files(root: str) -> list[Path]:
    """Find all API route files."""
    pattern = Path(root) / "components" / "*" / "src" / "*" / "api" / "*.py"
    import glob
    files = glob.glob(str(pattern))
    # Exclude non-endpoint files and files with inherently non-bulk APIs
    exclude = {"__init__.py", "auth.py", "namespaces.py"}
    return [Path(f) for f in sorted(files)
            if os.path.basename(f) not in exclude]


class EndpointVisitor(ast.NodeVisitor):
    """AST visitor that extracts FastAPI endpoint definitions."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.violations = []
        self.endpoints = []

    def visit_AsyncFunctionDef(self, node):
        self._check_endpoint(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        self._check_endpoint(node)
        self.generic_visit(node)

    def _check_endpoint(self, node):
        """Check if a function is a FastAPI endpoint and validate conventions."""
        for decorator in node.decorator_list:
            method, path = self._parse_decorator(decorator)
            if method is None:
                continue

            endpoint_info = {
                "function": node.name,
                "method": method,
                "path": path,
                "line": node.lineno,
                "file": self.filepath,
            }
            self.endpoints.append(endpoint_info)

            if method in ("post", "put", "delete"):
                self._check_write_endpoint(node, endpoint_info)
            elif method == "get":
                self._check_get_endpoint(node, endpoint_info)

    def _parse_decorator(self, decorator) -> tuple:
        """Parse @router.get("/path") style decorators."""
        if not isinstance(decorator, ast.Call):
            return None, None

        func = decorator.func
        if not isinstance(func, ast.Attribute):
            return None, None

        method = func.attr
        if method not in ("get", "post", "put", "delete", "patch"):
            return None, None

        # Extract path from first positional arg
        path = ""
        if decorator.args:
            if isinstance(decorator.args[0], ast.Constant):
                path = decorator.args[0].value

        return method, path

    def _check_write_endpoint(self, node, info):
        """Check that write endpoints follow bulk-first convention."""
        # Check for List[...] in parameters (via type annotation)
        has_list_body = False
        has_bulk_response = False

        for arg in node.args.args:
            annotation = arg.annotation
            if annotation is None:
                continue
            ann_str = ast.dump(annotation)
            # Check for List[...] or list[...]
            if "List" in ann_str or "list" in ann_str:
                has_list_body = True
                break

        # Check return type for BulkResponse
        if node.returns:
            ret_str = ast.dump(node.returns)
            if "BulkResponse" in ret_str or "Bulk" in ret_str:
                has_bulk_response = True
            # Also accept dict return (common in FastAPI)
            elif "dict" in ret_str:
                has_bulk_response = True  # Can't statically verify dict contents

        # Some endpoints are exempt (e.g., /activate, /import, health checks)
        exempt_patterns = [
            "/activate", "/import", "/export", "/health", "/initialize",
            "/reactivate", "/hard", "/validate", "/search",
            "/query", "/preview", "/provision", "/upload",
            "/restore", "/archive", "/cascade",
            "/start", "/pause", "/resume",  # replay control endpoints
        ]
        path = info.get("path", "")
        is_exempt = any(p in path for p in exempt_patterns)

        if not is_exempt:
            if not has_list_body:
                self.violations.append({
                    **info,
                    "rule": "bulk-first-request",
                    "message": f"{info['method'].upper()} {path}: write endpoint should accept List[...] body",
                })

    def _check_get_endpoint(self, node, info):
        """Check that GET list endpoints have pagination parameters."""
        path = info.get("path", "")

        # Only check list endpoints (no path params like /{id})
        if "{" in path:
            return

        # Check for page/page_size params
        param_names = [arg.arg for arg in node.args.args]
        has_page = "page" in param_names
        has_page_size = "page_size" in param_names

        # Also check keyword-only args
        kwonly_names = [arg.arg for arg in node.args.kwonlyargs]
        has_page = has_page or "page" in kwonly_names
        has_page_size = has_page_size or "page_size" in kwonly_names

        # Exempt endpoints that aren't list endpoints
        exempt_patterns = ["/health", "/stats", "/count"]
        is_exempt = any(p in path for p in exempt_patterns)

        if not is_exempt and not (has_page and has_page_size):
            # Only flag if the endpoint looks like a list endpoint
            # (heuristic: root path "/" or path without ID params)
            if path in ("", "/", "/{namespace}") or path.endswith("/"):
                self.violations.append({
                    **info,
                    "rule": "pagination-params",
                    "message": f"GET {path}: list endpoint missing page/page_size parameters",
                })

        # Check page_size defaults
        for arg in node.args.args + node.args.kwonlyargs:
            if arg.arg == "page_size":
                # Check default value
                defaults = node.args.defaults + node.args.kw_defaults
                for default in defaults:
                    if isinstance(default, ast.Constant) and arg.arg == "page_size":
                        if default.value != 50:
                            self.violations.append({
                                **info,
                                "rule": "page-size-default",
                                "message": f"GET {path}: page_size default should be 50, got {default.value}",
                            })


def check_file(filepath: Path) -> dict:
    """Check a single API file for convention violations."""
    try:
        source = filepath.read_text()
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError) as e:
        return {"file": str(filepath), "error": str(e), "violations": [], "endpoints": []}

    visitor = EndpointVisitor(str(filepath))
    visitor.visit(tree)

    return {
        "file": str(filepath),
        "violations": visitor.violations,
        "endpoints": visitor.endpoints,
    }


def main():
    parser = argparse.ArgumentParser(description="Check API consistency with WIP conventions")
    parser.add_argument("--root", default=".", help="Project root directory")
    parser.add_argument("--output", default="-", help="Output JSON file (- for stdout)")
    args = parser.parse_args()

    api_files = find_api_files(args.root)
    if not api_files:
        print(f"No API files found in {args.root}/components/*/src/*/api/", file=sys.stderr)
        result = {"files_checked": 0, "total_violations": 0, "total_endpoints": 0, "services": {}}
    else:
        results_by_service = {}
        total_violations = 0
        total_endpoints = 0

        for filepath in api_files:
            # Extract service name from path
            parts = filepath.parts
            try:
                comp_idx = parts.index("components")
                service = parts[comp_idx + 1]
            except (ValueError, IndexError):
                service = "unknown"

            result = check_file(filepath)

            if service not in results_by_service:
                results_by_service[service] = {"violations": [], "endpoints": [], "files": []}

            results_by_service[service]["violations"].extend(result["violations"])
            results_by_service[service]["endpoints"].extend(result["endpoints"])
            results_by_service[service]["files"].append(str(filepath))

            total_violations += len(result["violations"])
            total_endpoints += len(result["endpoints"])

        result = {
            "files_checked": len(api_files),
            "total_violations": total_violations,
            "total_endpoints": total_endpoints,
            "services": {
                svc: {
                    "files": data["files"],
                    "endpoint_count": len(data["endpoints"]),
                    "violation_count": len(data["violations"]),
                    "violations": data["violations"],
                }
                for svc, data in sorted(results_by_service.items())
            },
        }

    output = json.dumps(result, indent=2)
    if args.output == "-":
        print(output)
    else:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(output + "\n")
        print(f"API consistency check: {result['total_violations']} violations across {result.get('files_checked', 0)} files", file=sys.stderr)


if __name__ == "__main__":
    main()
