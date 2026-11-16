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


def test_gate_in_an_if_test_behind_a_short_circuit_is_reported():
    """`if existing and not await can_read(...)`: with no row the auth layer is
    never asked, and the request goes on to answer."""
    violations = _permission_violations("""
        @router.get("/{thing_id}")
        async def get_thing(thing_id: str, identity: str = Depends(require_api_key)):
            existing = await Thing.find_one({"thing_id": thing_id})
            if existing and not await can_read(identity, existing.namespace):
                raise HTTPException(404, "Thing not found")
            return await ThingService.export(thing_id)
    """)
    assert [v["function"] for v in violations] == ["get_thing"]


def test_gate_in_a_short_circuited_assignment_is_reported():
    """The `hidden = found and not await can_read(...)` idiom asks nothing when
    the lookup misses, so the read that follows is ungated."""
    violations = _permission_violations("""
        @router.get("/{thing_id}/children")
        async def get_children(thing_id: str, identity: str = Depends(require_api_key)):
            existing = await Thing.find_one({"thing_id": thing_id})
            hidden = existing is not None and not await can_read(identity, existing.namespace)
            if hidden:
                raise HTTPException(404, "Thing not found")
            children = await ThingService.children_of(thing_id)
            return ThingListResponse(items=children, total=len(children))
    """)
    assert [v["function"] for v in violations] == ["get_children"]


@pytest.mark.parametrize("body", [
    pytest.param("""
        @router.get("/{thing_id}")
        async def get_thing(thing_id: str, identity: str = Depends(require_api_key)):
            existing = await Thing.find_one({"thing_id": thing_id})
            if existing is None and not await can_read(identity, existing.namespace):
                raise HTTPException(404, "Thing not found")
            return await ThingService.export(thing_id)
    """, id="and-operand"),
    pytest.param("""
        @router.get("/{thing_id}")
        async def get_thing(thing_id: str, identity: str = Depends(require_api_key)):
            existing = await Thing.find_one({"thing_id": thing_id})
            if existing is None or not await can_read(identity, existing.namespace):
                if strict:
                    raise HTTPException(404, "Thing not found")
            return await ThingService.export(thing_id)
    """, id="or-operand-body-falls-through"),
    pytest.param("""
        @router.get("/{thing_id}")
        async def get_thing(thing_id: str, identity: str = Depends(require_api_key)):
            existing = await Thing.find_one({"thing_id": thing_id})
            if existing is None or not await can_read(identity, existing.namespace):
                return await ThingService.export(thing_id)
            return []
    """, id="or-operand-body-answers-data"),
    pytest.param("""
        @router.get("/{thing_id}")
        async def get_thing(thing_id: str, identity: str = Depends(require_api_key)):
            existing = await Thing.find_one({"thing_id": thing_id})
            if not (existing is not None or await can_read(identity, existing.namespace)):
                raise HTTPException(404, "Thing not found")
            return await ThingService.export(thing_id)
    """, id="not-of-or-operand"),
])
def test_gate_in_a_later_operand_without_a_certain_exit_is_reported(body: str) -> None:
    """A gate after the first operand counts only when the test cannot come out
    false without it and the body always ends the call: with `and`, a falsy
    first operand skips the gate; a body that can fall through, or that
    answers data, reaches the answer without it."""
    violations = _permission_violations(body)
    assert [v["function"] for v in violations] == ["get_thing"]


def test_method_call_returned_before_the_gate_is_reported():
    """`cached.model_dump()` takes no arguments, but emptiness is what a
    constructor is given, not what a method happens to be called on."""
    violations = _permission_violations(f"""
        @router.get("/list")
        async def list_things(identity: str = Depends(require_api_key)):
            if cached:
                return cached.model_dump()
            {GATE}
            return await ThingService.list()
    """)
    assert [v["function"] for v in violations] == ["list_things"]


def test_gate_in_try_body_with_an_ungated_handler_is_reported():
    """Whatever raises jumps to the handler, and it may do so before the body
    reached its gate — so the handler's data answer escapes."""
    violations = _permission_violations(f"""
        @router.get("/list")
        async def list_things(thing_id: str, identity: str = Depends(require_api_key)):
            try:
                row = await ThingService.get(thing_id)
                {GATE}
                return row.payload
            except LookupError:
                return await ThingService.fallback(thing_id)
    """)
    assert [v["function"] for v in violations] == ["list_things"]


def test_match_without_an_irrefutable_case_is_reported():
    """A subject matching no case walks past the statement, so gates in the
    cases it does have are conditional. (ast.Match has no `orelse`: a `case _`
    is an ordinary case.)"""
    violations = _permission_violations(f"""
        @router.get("/list")
        async def list_things(kind: str, identity: str = Depends(require_api_key)):
            match kind:
                case "a":
                    {GATE}
                case "b":
                    {GATE}
            return await ThingService.list()
    """)
    assert [v["function"] for v in violations] == ["list_things"]


def test_match_case_escaping_before_the_gate_is_reported():
    violations = _permission_violations(f"""
        @router.get("/list")
        async def list_things(kind: str, identity: str = Depends(require_api_key)):
            match kind:
                case "cached":
                    return await ThingService.cached_list()
                case _:
                    {GATE}
            return await ThingService.list()
    """)
    assert [v["function"] for v in violations] == ["list_things"]


def test_named_miss_answer_is_not_a_waiver():
    """A listed miss answer makes a branch emptily answered; it does not gate the
    ones that answer with data."""
    violations = _permission_violations("""
        @router.post("/{template_id}/validate")
        async def validate_template(template_id: str, identity: str = Depends(require_api_key)):
            if nowhere:
                return TemplateService.template_not_found_validation(template_id)
            return await TemplateService.validate_template(template_id)
    """)
    assert [v["function"] for v in violations] == ["validate_template"]


def test_parent_lookup_that_gates_only_when_it_finds_a_row_is_reported():
    """The `if term_parent:` shape this rule used to waive for create_terms: with
    no terminology row the write goes ahead ungated."""
    violations = _permission_violations("""
        @router.post("/terminologies/{terminology_id}/terms")
        async def create_terms(
            terminology_id: str, items: list[Item], identity: str = Depends(require_api_key)
        ):
            parent = await Terminology.find_one({"terminology_id": terminology_id})
            if parent:
                await check_entity_permission(identity, parent.namespace, "write", "Terminology not found")
            return await TerminologyService.create_term(terminology_id, items)
    """)
    assert [v["function"] for v in violations] == ["create_terms"]
    assert "create_terms" not in checker.PERMISSION_CONDITIONAL_GATE_FUNCTIONS


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
    pytest.param("""
        @router.get("/{thing_id}")
        async def ok_miss_then_can_read(thing_id: str, identity: str = Depends(require_api_key)):
            existing = await Thing.find_one({"thing_id": thing_id})
            if existing is None:
                raise HTTPException(404, "Thing not found")
            if not await can_read(identity, existing.namespace):
                raise HTTPException(404, "Thing not found")
            return await ThingService.export(thing_id)
    """.replace("{GATE}", GATE), id="miss-answered-then-gate-in-the-test"),
    pytest.param("""
        @router.get("/{thing_id}")
        async def ok_or_gate_raises(thing_id: str, identity: str = Depends(require_api_key)):
            existing = await Thing.find_one({"thing_id": thing_id})
            if existing is None or not await can_read(identity, existing.namespace):
                raise HTTPException(404, "Thing not found")
            return await ThingService.export(thing_id)
    """, id="gate-in-or-operand-body-raises"),
    pytest.param("""
        @router.get("/{thing_id}/children")
        async def ok_or_gate_returns_empty(thing_id: str, identity: str = Depends(require_api_key)):
            existing = await Thing.find_one({"thing_id": thing_id})
            if existing is None or not await can_read(identity, existing.namespace):
                if (miss := await resolution_miss(thing_id)) is not None:
                    raise miss
                return []
            return await ThingService.children_of(thing_id)
    """, id="gate-in-or-operand-body-returns-empty"),
    pytest.param("""
        @router.get("/{thing_id}")
        async def ok_or_gate_else(thing_id: str, identity: str = Depends(require_api_key)):
            existing = await Thing.find_one({"thing_id": thing_id})
            if existing is None or not await can_read(identity, existing.namespace):
                raise HTTPException(404, "Thing not found")
            else:
                return await ThingService.export(thing_id)
    """, id="gate-in-or-operand-answer-in-else"),
    pytest.param("""
        @router.get("/{thing_id}")
        async def ok_not_of_and(thing_id: str, identity: str = Depends(require_api_key)):
            existing = await Thing.find_one({"thing_id": thing_id})
            if not (existing is not None and await can_read(identity, existing.namespace)):
                raise HTTPException(404, "Thing not found")
            return await ThingService.export(thing_id)
    """, id="gate-in-not-of-and-operand"),
    pytest.param("""
        @router.post("/{template_id}/validate")
        async def ok_named_miss_answer(template_id: str, identity: str = Depends(require_api_key)):
            existing = await Template.find_one({"template_id": template_id})
            if existing is None:
                return TemplateService.template_not_found_validation(template_id)
            await check_readable(identity, existing.namespace, "Template not found")
            return await TemplateService.validate_template(template_id)
    """, id="listed-miss-answer-before-gate"),
    pytest.param(f"""
        @router.get("/list")
        async def ok_match_catch_all(kind: str, identity: str = Depends(require_api_key)):
            match kind:
                case "a":
                    {GATE}
                case _:
                    {GATE}
            return await ThingService.list()
    """, id="match-with-irrefutable-case"),
    pytest.param("""
        @router.post("/terminologies/{terminology_id}/terms")
        async def create_terms(
            terminology_id: str, items: list[Item], identity: str = Depends(require_api_key)
        ):
            parent = await Terminology.find_one({"terminology_id": terminology_id})
            if parent is None:
                raise HTTPException(404, "Terminology not found")
            await check_entity_permission(identity, parent.namespace, "write", "Terminology not found")
            return await TerminologyService.create_terms_bulk(terminology_id, items)
    """, id="parent-miss-answered-then-gate"),
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


# --------------------------------------------------------------------------
# Registered wrappers: a module-local gate counts; a lookup does not.
# --------------------------------------------------------------------------


def test_a_door_gated_only_by_gate_unresolvable_passes():
    """template-store's _gate_unresolvable always reaches check_readable (or
    404s) before it re-raises, so a door gated only by it is gated."""
    assert _permission_violations("""
        @router.get("/x")
        async def read(x: str, identity = Depends(require_api_key)):
            await _gate_unresolvable(exc, raw, "Template not found")
            return {}
    """) == []


def test_a_door_relying_only_on_terminology_namespace_is_reported():
    """def-store's _terminology_namespace returns the namespace of an
    NS:VALUE argument with no permission check: a lookup, not a gate."""
    assert _permission_violations("""
        @router.get("/x")
        async def search(terminology: str, identity = Depends(require_api_key)):
            namespace = await _terminology_namespace(identity, terminology)
            return {"namespace": namespace}
    """)
