"""CASE-1322 — the permission-enforcement rule checks that every path to an
answer passes a gate, not just that a gate name appears in the function.

Run with:  .venv/bin/pytest scripts/tests/test_permission_gate_reachability.py
"""

import ast
import importlib.util
import textwrap
from pathlib import Path

import pytest

CHECKER = Path(__file__).resolve().parents[1] / "api-consistency-check.py"
_REPO_ROOT = CHECKER.parent.parent

_spec = importlib.util.spec_from_file_location("api_consistency_check", CHECKER)
assert _spec and _spec.loader
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)

# A def-store path: not on the service-level exemption, so the rule applies.
_FILE = "components/def-store/src/def_store/api/things.py"


def _permission_violations(source: str) -> list[dict]:
    """Endpoints in `source` the permission rule reports."""
    tree = ast_parse(source)
    visitor = checker.EndpointVisitor(_FILE, router_prefix="/things")
    visitor.visit(tree)
    return [v for v in visitor.violations if v["rule"] == "permission-enforcement"]


def ast_parse(source: str) -> ast.Module:
    return ast.parse(textwrap.dedent(source))


GATE = "await check_namespace_permission(identity, namespace, 'write')"

# --------------------------------------------------------------------------
# Reported: a path answers, or can, without reaching a gate.
# --------------------------------------------------------------------------


def test_gate_under_if_existing_is_reported():
    """The shape behind CASE-1274: entity absent falls through to the answer."""
    violations = _permission_violations(f"""
        @router.get("{{thing_id}}")
        async def get_thing(thing_id: str, identity: str = Depends(require_api_key)):
            existing = await Thing.find_one({{"thing_id": thing_id}})
            if existing:
                {GATE}
            return await ThingService.export(thing_id)
    """)
    assert [v["function"] for v in violations] == ["get_thing"]
    assert "no permission gate" in violations[0]["message"]


def test_gate_in_one_branch_of_try_is_reported():
    """The try body can finish without ever reaching the handler's gate."""
    violations = _permission_violations(f"""
        @router.get("/dump")
        async def dump(identity: str = Depends(require_api_key)):
            try:
                return await ThingService.all()
            except Exception:
                {GATE}
                return []
    """)
    assert [v["function"] for v in violations] == ["dump"]


def test_write_before_gate_in_bulk_loop_is_reported():
    """A gate that runs after the item is written gates nothing."""
    violations = _permission_violations(f"""
        @router.post("")
        async def create_things(items: list[Item], identity: str = Depends(require_api_key)):
            for item in items:
                await ThingService.write(item)
                {GATE}
            return BulkResponse(results=items)
    """)
    assert [v["function"] for v in violations] == ["create_things"]


def test_answer_before_gate_is_reported():
    violations = _permission_violations(f"""
        @router.get("/list")
        async def list_things(identity: str = Depends(require_api_key)):
            if cached:
                return cached.payload
            {GATE}
            return await ThingService.list()
    """)
    assert [v["function"] for v in violations] == ["list_things"]


def test_gate_only_in_helper_defined_inside_endpoint_is_reported():
    """The nested helper runs only if the body calls it, and it does not."""
    violations = _permission_violations(f"""
        @router.get("/list")
        async def list_things(identity: str = Depends(require_api_key)):
            async def _guard(ns):
                {GATE}
            return await ThingService.list()
    """)
    assert [v["function"] for v in violations] == ["list_things"]


def test_gate_behind_an_if_inside_the_loop_is_reported():
    """Only some items reach it, so no item is provably gated."""
    violations = _permission_violations(f"""
        @router.post("")
        async def create_things(items: list[Item], identity: str = Depends(require_api_key)):
            for item in items:
                if item.needs_check:
                    {GATE}
                await ThingService.write(item)
            return BulkResponse(results=items)
    """)
    assert [v["function"] for v in violations] == ["create_things"]


def test_no_gate_at_all_is_reported():
    violations = _permission_violations("""
        @router.get("/list")
        async def list_things(identity: str = Depends(require_api_key)):
            return await ThingService.list()
    """)
    assert [v["function"] for v in violations] == ["list_things"]


# --------------------------------------------------------------------------
# Accepted: gates that do stand on every path.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("body", [
    pytest.param(f"""
        @router.get("/list")
        async def ok_top_level(identity: str = Depends(require_api_key)):
            {GATE}
            return await ThingService.list()
    """, id="top-level"),
    pytest.param("""
        @router.get("/list")
        async def ok_if_test(identity: str = Depends(require_api_key)):
            if not is_superadmin(identity):
                raise HTTPException(403, "admins only")
            return await ThingService.list()
    """, id="gate-is-the-if-test"),
    pytest.param(f"""
        @router.get("/list")
        async def ok_both_branches(identity: str = Depends(require_api_key)):
            if namespace:
                {GATE}
            else:
                within = await resolve_accessible_namespaces(identity)
            return await ThingService.list(within)
    """, id="both-branches"),
    pytest.param(f"""
        @router.post("")
        async def ok_bulk_loop(items: list[Item], identity: str = Depends(require_api_key)):
            for item in items:
                {GATE}
                await ThingService.write(item)
            return BulkResponse(results=[])
    """, id="per-item-gate-first-in-loop"),
    pytest.param(f"""
        @router.post("")
        async def ok_loop_after_continue(items: list[Item], identity: str = Depends(require_api_key)):
            for item in items:
                if item.skipped:
                    continue
                {GATE}
                await ThingService.write(item)
            return BulkResponse(results=[])
    """, id="loop-gate-after-abort"),
    pytest.param("""
        @router.post("")
        async def ok_empty_first(items: list[Item], identity: str = Depends(require_api_key)):
            if not items:
                return BulkResponse(results=[], total=0, succeeded=0, failed=0)
            for item in items:
                {GATE}
                await ThingService.write(item)
            return BulkResponse(results=items)
    """.replace("{GATE}", GATE), id="empty-answer-before-gate"),
    pytest.param(f"""
        @router.get("/list")
        async def ok_with(identity: str = Depends(require_api_key)):
            with trace("list"):
                {GATE}
            return await ThingService.list()
    """, id="inside-with"),
    pytest.param(f"""
        @router.get("/list")
        async def ok_try(identity: str = Depends(require_api_key)):
            try:
                {GATE}
                return await ThingService.list()
            except ValueError as e:
                raise HTTPException(404, str(e)) from e
    """, id="inside-try-body"),
    pytest.param(f"""
        @router.get("/list")
        async def ok_finally(identity: str = Depends(require_api_key)):
            try:
                rows = await ThingService.list()
            finally:
                {GATE}
            return rows
    """, id="inside-finally"),
    pytest.param("""
        @router.get("/list")
        async def ok_missing_first(identity: str = Depends(require_api_key)):
            existing = await Thing.find_one({"thing_id": thing_id})
            if existing is None:
                raise HTTPException(404, "Thing not found")
            {GATE}
            return await ThingService.export(existing)
    """.replace("{GATE}", GATE), id="raise-miss-then-gate"),
    pytest.param("""
        @router.get("/list")
        async def ok_missing_and_branches(identity: str = Depends(require_api_key)):
            if job.kind == "backup":
                await _check_job_permission(identity, job, "read")
            else:
                await _check_job_permission(identity, job, "admin")
            return job.archive
    """, id="both-branches-wrapped"),
    pytest.param("""
        @router.delete("/cleanup", dependencies=[Depends(require_groups(PRIVILEGED_GROUPS))])
        async def ok_depends_factory():
            return await cleanup()
    """, id="depends-factory-in-decorator"),
])
def test_gated_shapes_pass(body: str) -> None:
    assert _permission_violations(body) == []


def test_depends_admin_gate_passes():
    violations = _permission_violations("""
        @router.post("/keys")
        async def create_key(items: list[Item], _admin: str = Depends(require_admin_key)):
            return await KeyService.create(items)
    """)
    assert violations == []


def test_exempt_paths_and_services_still_pass():
    violations = _permission_violations("""
        @router.get("/health")
        async def health():
            return {"ok": True}
    """)
    assert violations == []
    tree = ast_parse("""
        @router.post("/x")
        async def ungated():
            return await ThingService.write()
    """)
    visitor = checker.EndpointVisitor(
        "components/registry/src/registry/api/x.py", router_prefix="/x")
    visitor.visit(tree)
    assert [v for v in visitor.violations if v["rule"] == "permission-enforcement"] == []


# --------------------------------------------------------------------------
# The reviewed list waives reachability, never the gate itself.
# --------------------------------------------------------------------------


def test_listed_function_with_conditional_gate_passes():
    source = """
        @router.put("")
        async def update_terms(items: list[Item], identity: str = Depends(require_api_key)):
            ids = [item.term_id for item in items if item.term_id]
            refused = {}
            if ids:
                id_to_namespace = {d.term_id: d.namespace for d in await Term.find({}).to_list()}
                refused = await entity_write_refusals(identity, id_to_namespace, "Term not found")
            results = []
            for i, item in enumerate(items):
                if refused.get(item.term_id) is not None:
                    continue
                await TermService.update(item)
            return BulkResponse(results=results)
    """
    assert "update_terms" in checker.PERMISSION_CONDITIONAL_GATE_FUNCTIONS
    assert _permission_violations(source) == []


def test_listed_function_that_stopped_gating_is_reported():
    source = """
        @router.put("")
        async def update_terms(items: list[Item], identity: str = Depends(require_api_key)):
            results = [await TermService.update(item) for item in items]
            return BulkResponse(results=results)
    """
    assert [v["function"] for v in _permission_violations(source)] == ["update_terms"]


def test_listed_function_answering_before_its_gate_is_reported():
    source = f"""
        @router.put("")
        async def update_terms(items: list[Item], identity: str = Depends(require_api_key)):
            if items:
                return BulkResponse(results=items)
            ids = [item.term_id for item in items if item.term_id]
            if ids:
                {GATE}
            return BulkResponse(results=[])
    """
    assert [v["function"] for v in _permission_violations(source)] == ["update_terms"]


def test_every_listed_function_is_a_real_bulk_or_split_shape():
    """The list is short and each entry states a reason; a rationale-less or
    stale entry (no such endpoint any more) is a defect in the list."""
    endpoints = set()
    for path in _REPO_ROOT.glob("components/*/src/*/api/*.py"):
        tree = ast_parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.decorator_list:
                endpoints.add(node.name)
    assert endpoints, "no endpoints found — the scan path is wrong"
    for name, rationale in checker.PERMISSION_CONDITIONAL_GATE_FUNCTIONS.items():
        assert name in endpoints, f"{name} is no longer an endpoint"
        assert len(rationale) > 60, f"{name} has no real rationale"


# --------------------------------------------------------------------------
# The repository itself is at the baseline the rule is meant to hold.
# --------------------------------------------------------------------------


def test_repository_is_at_zero_permission_violations():
    total = 0
    for path in _REPO_ROOT.glob("components/*/src/*/api/*.py"):
        result = checker.check_file(path)
        total += sum(1 for v in result["violations"] if v["rule"] == "permission-enforcement")
    assert total == 0, "the permission rule found ungated paths in the repo"
