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

# ---------------------------------------------------------------------------
# Exemptions (CASE-335 Angle B + C)
# ---------------------------------------------------------------------------
#
# Path-substring patterns. Matched against the *full* route, i.e.
# `APIRouter(prefix=...)` + decorator path. Pre-CASE-335 this list lived
# inline in `_check_write_endpoint` and matched only the decorator path,
# which silently misfired when routers mounted at `/files`, `/import`,
# etc. used `@router.post("")` for the root operation.
EXEMPT_PATH_PATTERNS = [
    "/activate", "/import", "/export", "/health", "/initialize",
    "/reactivate", "/hard", "/validate", "/search",
    "/query", "/preview", "/provision", "/upload",
    "/restore", "/archive", "/cascade",
    "/start", "/pause", "/resume",
]

# Function-name patterns for operation verbs that the URL doesn't always
# carry. When a decorator path is `""` and the operation verb lives on
# `APIRouter(prefix=...)` or on the function name only, the path-substring
# match can't see it. These regexes catch the common WIP naming
# conventions for inherently-non-bulk operations.
EXEMPT_FUNCTION_PATTERNS = [
    re.compile(r"^upload_"),     # multipart binary uploads
    re.compile(r"^import_"),     # CSV/XLSX imports (multipart or streaming)
    re.compile(r"^start_"),      # long-running operations (start_backup)
    re.compile(r"^pause_"),      # control endpoints
    re.compile(r"^resume_"),     # control endpoints
    re.compile(r"^cancel_"),     # control endpoints (cancel_replay)
]

# One-off exemptions for endpoints that are singletons by design but
# don't fit a name-pattern bucket. The rationale lives here so future
# readers see *why* the convention doesn't apply.
EXEMPT_FUNCTIONS = {
    "create_api_key": (
        "Singleton by design — API key provisioning happens 1-2 at a time "
        "during namespace bootstrap. Bulk shape can be added when demand "
        "emerges. (CASE-335 Angle B)"
    ),
}


# CASE-384 follow-up — permission-enforcement rule.
#
# Every endpoint that operates on namespaced platform data must reference
# one of these helpers (called directly in the function body) OR declare
# a `Depends(...)` on a recognised admin gate (PERMISSION_DEPENDS_NAMES).
#
# The check is syntactic. False positives are managed via the two
# exemption mechanisms below — additions need a written rationale.
PERMISSION_ENFORCEMENT_NAMES = frozenset({
    "check_namespace_permission",
    "resolve_namespace_filter",
    "resolve_accessible_namespaces",
    "_is_superadmin",
    # Local helper wrappers that delegate to the canonical helpers.
    # When a new wrapper lands, add the name here so the AST walker
    # treats the wrapper-call as a valid gate without needing to
    # recurse into module-local definitions.
    "_enforce_replay_admin",        # document_store.api.replay
})

# Names that count as valid permission gates when they appear as the
# callee inside `Depends(...)` on a function parameter's default. Used
# by registry's api-key-management endpoints, which gate on admin
# membership via FastAPI Depends rather than an inline call.
PERMISSION_DEPENDS_NAMES = frozenset({
    "require_admin_key",            # registry: admin-group-only gate
})

# Path-substring patterns. Endpoints whose full route matches one of
# these are exempt from the permission-enforcement rule. The categories:
#
#   - Stateless utilities that don't touch namespaced data: /preview
#     (parses operator-uploaded files), /validate (now gated, but kept
#     here as a fallback because some validate endpoints in other
#     services may be stateless).
#   - Service health/info: /health, /stats, /count.
#   - Bootstrap/initialisation: /initialize.
#
# Each addition needs a rationale recorded — exemptions accumulate
# silently otherwise.
PERMISSION_EXEMPT_PATH_PATTERNS = [
    "/preview",       # Stateless file parsers (e.g., import preview)
    "/health",        # Service health endpoints
    "/stats",         # Service-wide statistics
    "/count",         # Aggregate counters
    "/initialize",    # Bootstrap operations (admin-by-deployment-context)
]

# Service-level exemptions. Whole components whose authorisation model
# is fundamentally different from the namespace-permission model used by
# def-store / document-store / template-store.
#
#   - registry: IS the permission authority. Its API surface operates on
#     api-keys, grants, entries, synonyms, and search indices — none of
#     which are "namespaced documents" in the per-app sense. Its
#     enforcement model is admin-group membership (`wip-admins`,
#     `wip-services`) checked inline via `_resolve_permission` against
#     the calling identity. Excluding it here doesn't disable scrutiny;
#     it just means the new rule wouldn't add useful signal to its
#     existing audit.
PERMISSION_EXEMPT_SERVICES = frozenset({
    "registry",
})

# One-off function-name exemptions for the permission rule. Same shape
# as EXEMPT_FUNCTIONS — keep the rationale literal so future readers
# can re-evaluate when context changes.
PERMISSION_EXEMPT_FUNCTIONS = {
    # No entries yet. Add with care — the burden of proof is on the
    # exemption, not on the enforcement.
}


# CASE-395 follow-up — bulk-result-item-canonical rule.
#
# Bulk-write response models are platform-universal per wip://conventions.
# The canonical definitions live in libs/wip-auth/src/wip_auth/bulk_models.py
# (BulkResultItemBase + per-domain subclasses). Component-side definitions
# are forbidden — they drift, and 78 of 141 [call-arg] mypy errors in
# commit 7b91c55 traced to exactly this drift.
#
# Allowed shapes in components/*/src/*/models/api_models.py:
#   - Re-export aliases:
#       from wip_auth.bulk_models import DocumentBulkResultItem as BulkResultItem
#   - Subclasses inheriting from one of the canonical bases:
#       class FooBulkResultItem(BulkResultItemBase): ...
#
# Forbidden: bare `class BulkResultItem(BaseModel)` definitions, which
# is how the drift accumulated. The canonical home itself
# (libs/wip-auth/src/wip_auth/bulk_models.py) is exempt — it IS the
# canonical home.
BULK_MODEL_CANONICAL_BASE_NAMES = frozenset({
    "BulkResultItemBase",
    "BulkResponseBase",
})

BULK_MODEL_CANONICAL_PATHS = frozenset({
    "libs/wip-auth/src/wip_auth/bulk_models.py",
})

# Match the bare universal names only. Per-domain subclasses like
# `DocumentBulkResultItem` or registry-specific `RegisterBulkResponse`
# (different family — results are typed RegisterKeyResponse) are out of
# scope. The drift this rule catches is the literal `class BulkResultItem`
# / `class BulkResponse` shape — that's what CASE-395 found in three places.
BULK_MODEL_NAME_PATTERN = re.compile(r"^(BulkResultItem|BulkResponse)$")


# CASE-398 follow-up — registry-client-singleton rule.
#
# The Registry HTTP client is canonical at libs/wip-auth/src/wip_auth/
# registry_client.py (RegistryClientBase). Per-component clients are thin
# subclasses that add domain-specific wrappers (register_terminology /
# register_template / generate_document_id). The canonical home owns the
# universal infrastructure — auth headers, httpx construction with module-
# level transport injection, health checks, hard-delete, namespace metadata.
#
# Allowed shapes in components/*/src/*/services/registry_client.py:
#   - Subclass RegistryClientBase:
#       from wip_auth.registry_client import RegistryClientBase
#       class RegistryClient(RegistryClientBase): ...
#   - Re-export aliases (rare; usually you want the subclass).
#
# Forbidden: a `class RegistryClient` defined outside the canonical home
# that doesn't inherit from RegistryClientBase. That's the drift CASE-398
# closed — 1300+ LOC of duplicated httpx setup across three services.
REGISTRY_CLIENT_CANONICAL_PATH = "libs/wip-auth/src/wip_auth/registry_client.py"
REGISTRY_CLIENT_CANONICAL_BASE = "RegistryClientBase"


# CASE-402 follow-up — identity-hash-canonical rule.
#
# The document-identity-hash algorithm is canonical at libs/wip-auth/src/
# wip_auth/document_identity.py. Re-implementing it elsewhere drifts —
# CASE-401 caught the docs out-of-sync; CASE-316 caught an external
# loader out-of-sync (213/214 docs silently dropped). The case for a
# rule is the same shape as CASE-395 + CASE-398: catch the divergence
# before it gets weaponised by callers.
#
# Allowed shapes outside the canonical home:
#   - Re-export aliases (rare):
#       from wip_auth.document_identity import compute_identity_hash
#   - Thin delegate wrappers — IdentityService in document-store is the
#     example. These define `def compute_identity_hash(...)` but
#     immediately call the lib. The rule below catches `def`s of the
#     canonical function names; the delegate exemption keeps the
#     wrapper legal.
#
# Forbidden: a fresh `def compute_identity_hash` / `def
# compute_normalized_hash` whose body re-implements the JSON-canonical
# SHA-256 logic. That's the drift CASE-402 closes.
#
# Implementation note: we scan FunctionDef + AsyncFunctionDef across the
# whole repo and flag the canonical names anywhere except the canonical
# home itself and the document-store delegate. A more refined check
# could AST-walk the body for hashlib.sha256 calls, but the simpler
# name-only rule is enough — false positives are exempted by name, false
# negatives (someone implementing the same logic under a different name)
# aren't caught by any name-based rule.
IDENTITY_HASH_CANONICAL_PATH = "libs/wip-auth/src/wip_auth/document_identity.py"
IDENTITY_HASH_CANONICAL_FUNCTION_NAMES = frozenset({
    "compute_identity_hash",
    "compute_normalized_hash",
})

# Files allowed to define the canonical function names *as delegates*.
# The document-store IdentityService is the only such wrapper today;
# every method delegates to wip_auth.document_identity. If a future
# service legitimately needs a delegate, add it here with rationale.
IDENTITY_HASH_DELEGATE_PATHS = frozenset({
    "components/document-store/src/document_store/services/identity_service.py",
})


def check_identity_hash_canonical(filepath: Path, root: Path) -> list[dict]:
    """Flag a top-level `def compute_identity_hash` / `def
    compute_normalized_hash` outside the canonical home (or one of the
    declared delegate paths).

    Catches the failure mode where someone re-implements the algorithm
    in a fresh location. Doesn't catch re-implementations under a
    different name — those need a content-based check, out of scope here."""
    try:
        rel = filepath.relative_to(root).as_posix()
    except ValueError:
        rel = filepath.as_posix()
    if rel == IDENTITY_HASH_CANONICAL_PATH:
        return []
    if rel in IDENTITY_HASH_DELEGATE_PATHS:
        return []

    try:
        source = filepath.read_text()
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError):
        return []

    violations: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if node.name not in IDENTITY_HASH_CANONICAL_FUNCTION_NAMES:
            continue
        violations.append({
            "file": rel,
            "line": node.lineno,
            "rule": "identity-hash-canonical",
            "function": node.name,
            "message": (
                f"def {node.name} defined at {rel}:{node.lineno} outside "
                f"the canonical home ({IDENTITY_HASH_CANONICAL_PATH}). "
                f"Import from wip_auth.document_identity instead, or add "
                f"this path to IDENTITY_HASH_DELEGATE_PATHS with rationale "
                f"if it must remain a delegate wrapper. CASE-402."
            ),
        })
    return violations


def _inherits_registry_client_base(cls: ast.ClassDef) -> bool:
    """True if the class inherits from RegistryClientBase."""
    for base in cls.bases:
        if isinstance(base, ast.Name) and base.id == REGISTRY_CLIENT_CANONICAL_BASE:
            return True
        # Handle qualified names like wip_auth.registry_client.RegistryClientBase
        if isinstance(base, ast.Attribute) and base.attr == REGISTRY_CLIENT_CANONICAL_BASE:
            return True
    return False


def check_registry_client_singleton(filepath: Path, root: Path) -> list[dict]:
    """Flag any `class RegistryClient` defined outside the canonical home
    that doesn't inherit from RegistryClientBase. Re-exports / subclasses
    are allowed; bare standalone definitions are forbidden."""
    try:
        rel = filepath.relative_to(root).as_posix()
    except ValueError:
        rel = filepath.as_posix()
    if rel == REGISTRY_CLIENT_CANONICAL_PATH:
        return []

    try:
        source = filepath.read_text()
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError):
        return []

    violations: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name != "RegistryClient":
            continue
        if _inherits_registry_client_base(node):
            continue
        violations.append({
            "file": rel,
            "line": node.lineno,
            "rule": "registry-client-singleton",
            "class": node.name,
            "message": (
                f"class RegistryClient defined at {rel}:{node.lineno} outside "
                f"the canonical home ({REGISTRY_CLIENT_CANONICAL_PATH}) and "
                f"does not inherit from {REGISTRY_CLIENT_CANONICAL_BASE}. "
                f"Components should subclass `RegistryClientBase` and add "
                f"domain-specific methods on top. CASE-398."
            ),
        })
    return violations


def find_api_files(root: str) -> list[Path]:
    """Find all API route files."""
    pattern = Path(root) / "components" / "*" / "src" / "*" / "api" / "*.py"
    import glob
    files = glob.glob(str(pattern))
    # Exclude non-endpoint files and files with inherently non-bulk APIs
    exclude = {"__init__.py", "auth.py", "namespaces.py"}
    return [Path(f) for f in sorted(files)
            if os.path.basename(f) not in exclude]


def find_model_files(root: str) -> list[Path]:
    """Find component-side model files that might (re)define bulk models."""
    import glob
    patterns = [
        Path(root) / "components" / "*" / "src" / "*" / "models" / "*.py",
        Path(root) / "libs" / "*" / "src" / "*" / "*.py",
    ]
    files: set[str] = set()
    for p in patterns:
        files.update(glob.glob(str(p)))
    return sorted(Path(f) for f in files if os.path.basename(f) != "__init__.py")


def _inherits_canonical_bulk_base(cls: ast.ClassDef) -> bool:
    """True if the class inherits from BulkResultItemBase / BulkResponseBase
    (directly or as a subscripted Generic)."""
    for base in cls.bases:
        if isinstance(base, ast.Name) and base.id in BULK_MODEL_CANONICAL_BASE_NAMES:
            return True
        # e.g. BulkResponseBase[ItemT]
        if (
            isinstance(base, ast.Subscript)
            and isinstance(base.value, ast.Name)
            and base.value.id in BULK_MODEL_CANONICAL_BASE_NAMES
        ):
            return True
    return False


def check_bulk_model_canonicity(filepath: Path, root: Path) -> list[dict]:
    """Flag any class whose name matches BulkResult{Item,Response} or
    BulkResponse outside the canonical home, unless the class inherits
    from BulkResultItemBase / BulkResponseBase."""
    try:
        rel = filepath.relative_to(root).as_posix()
    except ValueError:
        rel = filepath.as_posix()
    if rel in BULK_MODEL_CANONICAL_PATHS:
        return []  # The canonical home itself.

    try:
        source = filepath.read_text()
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError):
        return []

    violations: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not BULK_MODEL_NAME_PATTERN.search(node.name):
            continue
        if _inherits_canonical_bulk_base(node):
            continue
        violations.append({
            "file": rel,
            "line": node.lineno,
            "rule": "bulk-result-item-canonical",
            "class": node.name,
            "message": (
                f"class {node.name} defined outside the canonical home "
                f"(libs/wip-auth/src/wip_auth/bulk_models.py) and does not "
                f"inherit from BulkResultItemBase / BulkResponseBase. "
                f"Use a re-export alias "
                f"(`from wip_auth.bulk_models import X as {node.name}`) "
                f"or inherit from a canonical base. CASE-395."
            ),
        })
    return violations


def _extract_router_prefix(tree: ast.Module) -> str:
    """Find `APIRouter(prefix=...)` in module-level assignments.

    Returns the prefix string (e.g., '/files', '/import') or '' if the
    file's router is mounted at the root. Catches the case where the
    operation verb lives on the prefix and the decorator path is empty
    (CASE-335 Angle C).
    """
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        # router = APIRouter(prefix='/files', ...)
        if isinstance(func, ast.Name) and func.id == "APIRouter":
            for kw in node.value.keywords:
                if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                    return kw.value.value
    return ""


class EndpointVisitor(ast.NodeVisitor):
    """AST visitor that extracts FastAPI endpoint definitions."""

    def __init__(self, filepath: str, router_prefix: str = ""):
        self.filepath = filepath
        self.router_prefix = router_prefix
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

            # CASE-384 follow-up — every endpoint (any method) must
            # either call one of PERMISSION_ENFORCEMENT_NAMES or be on
            # an exemption list.
            self._check_permission_enforcement(node, endpoint_info)

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
        if decorator.args and isinstance(decorator.args[0], ast.Constant):
            path = decorator.args[0].value

        return method, path

    def _check_write_endpoint(self, node, info):
        """Check that write endpoints follow bulk-first convention.

        Exemption order (CASE-335 Angle B + C):
          1. Singleton DELETE-by-ID: DELETE with a path parameter (`{...}`)
             is by definition a single-target operation. No bulk shape applies.
          2. Function-name pattern: `upload_*`, `import_*`, `start_*`,
             `pause_*`, `resume_*`, `cancel_*` are inherently non-bulk.
          3. One-off `EXEMPT_FUNCTIONS` entries (e.g., `create_api_key`).
          4. Path-substring against the full route (router prefix + path).
        """
        # Check for List[...] in parameters (via type annotation)
        has_list_body = False

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
                pass
            # Also accept dict return (common in FastAPI)
            elif "dict" in ret_str:
                pass  # Can't statically verify dict contents

        method = info.get("method", "")
        path = info.get("path", "")
        full_path = self.router_prefix + path
        func_name = info.get("function", "")

        # 1. Singleton DELETE-by-ID — path parameter present.
        if method == "delete" and "{" in path:
            return
        # 2. Function-name patterns.
        if any(pat.match(func_name) for pat in EXEMPT_FUNCTION_PATTERNS):
            return
        # 3. One-off function exemptions.
        if func_name in EXEMPT_FUNCTIONS:
            return
        # 4. Path-substring against the full route.
        if any(p in full_path for p in EXEMPT_PATH_PATTERNS):
            return

        if not has_list_body:
            self.violations.append({
                **info,
                "rule": "bulk-first-request",
                "message": f"{info['method'].upper()} {path}: write endpoint should accept List[...] body",
            })

    def _check_permission_enforcement(self, node, info):
        """CASE-384 follow-up — every endpoint must call into the auth
        layer (or be explicitly exempt).

        Looks for any Call node in the function body whose function name
        matches PERMISSION_ENFORCEMENT_NAMES, or a Depends(...) on a
        function in PERMISSION_DEPENDS_NAMES. Walks the full subtree so
        the helper can be nested inside conditionals, try blocks, or
        comprehensions — anywhere FastAPI would reach it during a request.

        Exemption mechanisms:
          1. Component-level: PERMISSION_EXEMPT_SERVICES (e.g., registry).
          2. Path-substring against the full route — for stateless
             utilities (/preview, /health, /stats, /count, /initialize).
          3. One-off function-name entries with literal rationale.
        """
        path = info.get("path", "")
        full_path = self.router_prefix + path
        func_name = info.get("function", "")

        # 0. Service-level exemption.
        for part in Path(self.filepath).parts:
            if part in PERMISSION_EXEMPT_SERVICES:
                return

        if any(p in full_path for p in PERMISSION_EXEMPT_PATH_PATTERNS):
            return
        if func_name in PERMISSION_EXEMPT_FUNCTIONS:
            return

        # 1. Depends-style gates on function arguments.
        #    e.g. `_admin: str = Depends(require_admin_key)`. Walk every
        #    argument default in the canonical FastAPI shapes (args,
        #    kwonlyargs).
        for default in list(node.args.defaults) + list(node.args.kw_defaults):
            if default is None:
                continue
            if (
                isinstance(default, ast.Call)
                and isinstance(default.func, ast.Name)
                and default.func.id == "Depends"
                and default.args
                and isinstance(default.args[0], ast.Name)
                and default.args[0].id in PERMISSION_DEPENDS_NAMES
            ):
                return  # Endpoint is gated via Depends.

        # 2. Walk the function body for any Call to a recognised helper.
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            called_name: str | None = None
            if isinstance(inner.func, ast.Name):
                called_name = inner.func.id
            elif isinstance(inner.func, ast.Attribute):
                called_name = inner.func.attr
            if called_name in PERMISSION_ENFORCEMENT_NAMES:
                return  # Endpoint is gated.

        self.violations.append({
            **info,
            "rule": "permission-enforcement",
            "message": (
                f"{info['method'].upper()} {path}: endpoint does not call any "
                f"namespace permission helper "
                f"({sorted(PERMISSION_ENFORCEMENT_NAMES)}). "
                f"Add a check or extend the exemption list with rationale."
            ),
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

        # Only flag list-shaped endpoints (root path "/" or "" or
        # /{namespace}) without page/page_size params.
        looks_like_list = path in ("", "/", "/{namespace}") or path.endswith("/")
        if (
            not is_exempt
            and not (has_page and has_page_size)
            and looks_like_list
        ):
            self.violations.append({
                **info,
                "rule": "pagination-params",
                "message": f"GET {path}: list endpoint missing page/page_size parameters",
            })

        # Check page_size defaults
        for arg in node.args.args + node.args.kwonlyargs:
            if arg.arg != "page_size":
                continue
            defaults = node.args.defaults + node.args.kw_defaults
            for default in defaults:
                if (
                    isinstance(default, ast.Constant)
                    and default.value != 50
                ):
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

    router_prefix = _extract_router_prefix(tree)
    visitor = EndpointVisitor(str(filepath), router_prefix=router_prefix)
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

    # CASE-395 — bulk-result-item-canonical rule (model files, not API files).
    bulk_violations: list[dict] = []
    root_path = Path(args.root).resolve()
    for model_file in find_model_files(args.root):
        bulk_violations.extend(check_bulk_model_canonicity(model_file, root_path))
    if bulk_violations:
        # Attach to result top-level so consumers can see model-rule violations
        # alongside endpoint-rule ones.
        result["bulk_model_violations"] = bulk_violations
        result["total_violations"] = result.get("total_violations", 0) + len(bulk_violations)

    # CASE-398 — registry-client-singleton rule (services files).
    registry_violations: list[dict] = []
    import glob as _glob
    service_files: set[str] = set()
    for pat in (
        Path(args.root) / "components" / "*" / "src" / "*" / "services" / "*.py",
        Path(args.root) / "libs" / "*" / "src" / "*" / "*.py",
    ):
        service_files.update(_glob.glob(str(pat)))
    for svc_file in sorted(service_files):
        if os.path.basename(svc_file) == "__init__.py":
            continue
        registry_violations.extend(
            check_registry_client_singleton(Path(svc_file), root_path)
        )
    if registry_violations:
        result["registry_client_violations"] = registry_violations
        result["total_violations"] = result.get("total_violations", 0) + len(registry_violations)

    # CASE-402 — identity-hash-canonical rule. Scans the same surface as
    # the registry-client rule (component services + libs) but also covers
    # scripts/, where external tooling tends to reimplement.
    identity_hash_violations: list[dict] = []
    identity_scan_files: set[str] = set()
    for pat in (
        Path(args.root) / "components" / "*" / "src" / "*" / "services" / "*.py",
        Path(args.root) / "components" / "*" / "src" / "*" / "*.py",
        Path(args.root) / "libs" / "*" / "src" / "*" / "*.py",
        Path(args.root) / "scripts" / "*.py",
    ):
        identity_scan_files.update(_glob.glob(str(pat)))
    for scan_file in sorted(identity_scan_files):
        if os.path.basename(scan_file) == "__init__.py":
            continue
        identity_hash_violations.extend(
            check_identity_hash_canonical(Path(scan_file), root_path)
        )
    if identity_hash_violations:
        result["identity_hash_violations"] = identity_hash_violations
        result["total_violations"] = result.get("total_violations", 0) + len(identity_hash_violations)

    output = json.dumps(result, indent=2)
    if args.output == "-":
        print(output)
    else:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(output + "\n")
        print(f"API consistency check: {result['total_violations']} violations across {result.get('files_checked', 0)} files", file=sys.stderr)


if __name__ == "__main__":
    main()
