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
# readers see *why* the convention doesn't apply. An entry is a function
# NAME and exempts every endpoint so named in every service: count routes,
# not entries, when re-auditing the list (reconcile_synonym_lifecycle_endpoint
# alone covers three, in def-store, document-store and template-store).
EXEMPT_FUNCTIONS = {
    "create_api_key": (
        "Singleton by design — API key provisioning happens 1-2 at a time "
        "during namespace bootstrap. Bulk shape can be added when demand "
        "emerges. (CASE-335 Angle B)"
    ),
    "migrate_documents": (
        "The request body is an operation descriptor (template, target "
        "version, dry_run) and the operation is already bulk over the "
        "template's documents with per-document results. A List[...] "
        "envelope would be bulk-of-operations nobody has asked for."
    ),
    "add_edge_type_endpoints": (
        "Single-entity amendment: additively widens ONE edge type's "
        "source/target allow-lists, and the payload already is two lists. "
        "The documented happy path is the MCP tool of the same name; a "
        "multi-template envelope can be added when demand emerges."
    ),
    "rehash_identities_endpoint": (
        "The request body is an operation descriptor (namespace, dry_run, "
        "template filter) and the operation is already bulk over the "
        "namespace's documents with per-document results — the same shape "
        "as migrate_documents above."
    ),
    "reconcile_synonym_lifecycle_endpoint": (
        "Maintenance pass (def-store, document-store, template-store): the "
        "body is an operation descriptor (namespace, dry_run) and the pass "
        "is bulk over the namespace's retired entities, with per-entity "
        "results — the rehash_identities_endpoint shape."
    ),
    "heal_endpoints_endpoint": (
        "Maintenance pass: operation descriptor (namespace, dry_run), bulk "
        "over the namespace's edge types with per-entry results."
    ),
    "registry_ghosts": (
        "Maintenance pass: query-parameter descriptor (namespace, dry_run), "
        "bulk over the namespace's Registry entries with per-entry results."
    ),
    "reconcile_terminology": (
        "Snapshot reconcile: the body IS the complete desired term set of one "
        "terminology, and the response is the per-term diff. A list of "
        "snapshots would be bulk-of-operations nobody has asked for."
    ),
    "delete_namespace_storage_objects": (
        "Service-to-service cleanup during namespace deletion: the body is "
        "one namespace plus its storage keys, with per-key outcomes."
    ),
    "add_key_namespace": (
        "Single-key amendment addressed by path (key name, namespace prefix): "
        "widens ONE runtime key by one namespace, like add_edge_type_endpoints."
    ),
    "system_backup": (
        "Whole-Registry export: the request is a descriptor (scope, "
        "attachments) and the answer is one archive."
    ),
    "system_restore": (
        "Whole-Registry import: the body is one archive, applied as a unit "
        "with dry_run / mode, reporting per-record outcomes."
    ),
}

# One-off exemptions from the pagination rule, with the rationale literal.
PAGINATION_EXEMPT_FUNCTIONS = {
    "get_reservations": (
        "Admin-only list with one row per reserved namespace — the same size "
        "as the Registry's namespace list, which is unpaginated too."
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
    "is_superadmin",                # wip_auth.groups: the public platform-admin check
    # wip_auth.permissions helpers built on the ones above.
    "check_entity_permission",      # wip_auth: can_read, then check_namespace_permission
    "entity_write_refusals",        # wip_auth: write_refusals for the namespaces of the entities a bulk write names
    "can_read",                     # wip_auth: resolve_permission + permission_sufficient. Generic name;
                                    # the walker matches attribute calls too, so any .can_read(...) counts.
    # A name here is a gate the rule looks for. It is not yet enough: the
    # rule also requires that the gate is reached on every path to an answer
    # (see the gate-reachability helpers below). A gate under `if existing:`,
    # one sitting in an operand that a short-circuit may skip, or one that only
    # runs after the endpoint has already answered, is found by name and still
    # reported.
    # Local helper wrappers that delegate to the canonical helpers.
    # When a new wrapper lands, add the name here so the AST walker
    # treats the wrapper-call as a valid gate without needing to
    # recurse into module-local definitions.
    "_enforce_replay_admin",        # document_store.api.replay
    "check_readable",               # wip_auth.permissions: read, 404 text of an unknown id
    "_check_job_permission",        # document_store.api.backup: every namespace a job spans
    "write_refusals",               # wip_auth.permissions: write on every namespace a bulk write spans
    "_batch_write_refusals",        # document_store.api.documents: write_refusals for the documents a batch names
    "_require_write_on_template",   # template_store.api.templates: write on the template row's own namespace
    "_gate_unresolvable",           # template_store.api.templates: check_readable on the raw template's namespace, or 404
    "_audited_namespace",           # def_store.api.audit: can_read on the audited entity's namespace
    "_bulk_item_terminology",       # def_store.api.terms: can_read on the resolved terminology's namespace
    # Not registered: def_store.api.terms._terminology_namespace. It reads a
    # namespace out of an NS:VALUE argument with no permission check, so it is
    # a lookup, not a gate; a door relying on it alone must be reported.
})

# Names that count as valid permission gates when they appear as the
# callee inside `Depends(...)` on a function parameter's default. Used
# by registry's api-key-management endpoints, which gate on admin
# membership via FastAPI Depends rather than an inline call.
PERMISSION_DEPENDS_NAMES = frozenset({
    "require_admin_key",            # registry: admin-group-only gate
})

# Factory gates: they count only when CALLED with one of the named group
# constants, usually in the decorator's dependencies=[...]:
# Depends(require_groups(PRIVILEGED_GROUPS)). Those groups bypass namespace
# grants by design (privileged service and operator calls). The factory
# uncalled (Depends(require_groups) hands FastAPI the closure as the value
# and checks nothing) or called with any other group list is not a gate.
PERMISSION_DEPENDS_FACTORIES = {
    "require_groups": frozenset({"PRIVILEGED_GROUPS"}),
}


def _is_depends_gate(gate: ast.expr) -> bool:
    """Whether the argument of a Depends(...) is a recognised gate: a name in
    PERMISSION_DEPENDS_NAMES, or a factory of PERMISSION_DEPENDS_FACTORIES
    called with one of its group constants as its only argument."""
    if isinstance(gate, ast.Name):
        return gate.id in PERMISSION_DEPENDS_NAMES
    if isinstance(gate, ast.Call) and isinstance(gate.func, ast.Name):
        allowed = PERMISSION_DEPENDS_FACTORIES.get(gate.func.id)
        return (
            allowed is not None
            and len(gate.args) == 1 and not gate.keywords
            and isinstance(gate.args[0], ast.Name) and gate.args[0].id in allowed
        )
    return False

# Path-substring patterns. Endpoints whose full route matches one of
# these are exempt from the permission-enforcement rule. The categories:
#
#   - Stateless utilities that don't touch namespaced data: /preview
#     (parses operator-uploaded files). /validate endpoints are not
#     exempt; they are gated like any other.
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

# Endpoints that do gate, from a branch this rule cannot prove always runs.
# Listing here does not waive the permission rule: the endpoint must still
# call a recognised helper, and an answer that escapes before any gate is
# still reported. What it accepts is only the reachability finding, with the
# reason the branch the walk skips writes nothing — said in prose, so the
# review is on the record and can be re-evaluated.
#
# Most of these are the same bulk idiom: the refusal map for the namespaces
# the request names is computed once behind `if ids:`, and each item consults
# it before it is written. The shape a checker *can* prove is the one the
# single-entity doors use — answer a missing entity, then gate unconditionally.
_BATCH_GUARD = (
    "Per-namespace gate over the whole batch, taken before any item is written: "
    "entity_write_refusals is asked about every namespace the request's ids "
    "resolve to, and the item loop consults the map at its head and answers a "
    "refused item without touching it. The branch the walk cannot follow is "
    "`if ids:` — an empty id list is nothing to gate, and an item that names no "
    "id misses its own lookup, so it comes back not_found."
)

PERMISSION_CONDITIONAL_GATE_FUNCTIONS = {
    "update_terminologies": _BATCH_GUARD,
    "delete_terminologies": _BATCH_GUARD,
    "update_terms": _BATCH_GUARD,
    "deprecate_terms": _BATCH_GUARD,
    "delete_terms": _BATCH_GUARD,
    "merge_terms": (
        "Both endpoints of every merge are gated: entity_write_refusals is asked "
        "about the namespaces of all survivor and absorbed ids the items name, "
        "and _merge_refusal refuses an item whose either end is refused before "
        "the merge runs. The branch the walk cannot follow is `if ids:` — an "
        "empty id list is nothing to gate, and such an item names no term to "
        "merge, so it is reported as a miss."
    ),
    "update_templates": _BATCH_GUARD,
    "delete_templates": _BATCH_GUARD,
    "start_restore": (
        "The gate splits by mode and the two halves together cover every "
        "namespace written. Every mode but one admin-checks effective_target "
        "here; the exception is a fresh restore given only namespace_map, where "
        "the URL namespace gates nothing and _parse_namespace_map admin-checks "
        "every target the map names before the job is queued."
    ),
}

# Calls that build a door's not-found answer, for a door whose miss is a
# response body rather than an HTTP error (a validation outcome, a rejected
# item). Each is a fixed failure that quotes only the id the caller sent, so
# the path that answers with it carries nothing of any stored entity — the
# scan may treat it like an empty return. The bodies are written by hand at
# the cited place, so the claim is checkable; it is not a waiver of any gate:
# a listed helper is still an answer, and a door that reaches a *data* answer
# on this path is still reported.
MISS_ANSWER_FUNCTIONS = {
    "template_not_found_validation":
        "template_store TemplateService: ValidateTemplateResponse(valid=False, "
        "template_id=<the submitted id>, errors=[one ValidationError with "
        "code='not_found']) — the /validate door's answer for an id that names "
        "no template, and for one its caller cannot see.",
}


# ---------------------------------------------------------------------------
# Gate reachability (CASE-1322).
#
# A named gate is only a gate if every path to an answer passes it. The scan
# walks an endpoint body and says, for the path a request takes, which of
# these comes first:
#
#   _GATE    a recognised helper ran
#   _ESCAPE  the endpoint answered the caller with data on a path where no
#            gate ran — the finding this rule exists to catch
#   _OPEN    neither, yet — the next statement decides
#
# Accepted gate positions:
#   - in the expression of a statement the path reaches: an assignment, a bare
#     call, a raise, or a return;
#   - in the *test* of an `if` or `while`, which runs whenever the statement
#     does — the shape of `if not is_superadmin(identity): raise ...`. Only
#     where the test's own evaluation cannot skip it counts: in
#     `if existing and not await can_read(...)`, a falsy `existing` never asks
#     the auth layer, so that gate is conditional like one under `if existing:`;
#   - in every branch of an `if`/`else`: one branch skipping the gate is
#     exactly the fall-through that must not pass;
#   - first in the body of a `for` over the items or namespaces a request
#     names, or after statements that only abort the iteration — the bulk
#     idiom, where each item is gated before anything of it is written;
#   - in a `with` body, a `try` body or a `finally`, all of which run when the
#     statement runs. A gate only in an `except` handler does NOT count: the
#     try body can finish without ever reaching it. Equally, a gate in the body
#     does not cover an `except` handler that answers with data: something can
#     raise before the body reaches the gate and leave the handler ungated.
#
# Before a gate, a path may only abort or answer emptily: `raise`, `continue`,
# or a `return` of a literal or an empty structure, or of one of the miss
# answers in MISS_ANSWER_FUNCTIONS. That is what keeps "read the row to learn
# its namespace, then gate, then answer" legal, while an early `return <the
# data>` is not — the lookup is not the answer, and a door that answers nothing
# leaked nothing.
#
# What the scan will not try to prove, and so reports for review:
#   - that a branch skipped by a condition writes nothing (`if ids:`); see
#     PERMISSION_CONDITIONAL_GATE_FUNCTIONS;
#   - that a service call behind the gate would have failed anyway — a door
#     with no entity answers its own miss instead, so this never has to be
#     assumed.
# ---------------------------------------------------------------------------

_GATE, _ESCAPE, _OPEN = "gate", "escape", "open"


def _callee_name(call: ast.Call) -> str | None:
    """The helper name a call reads as — `can_read(...)` and `auth.can_read(...)`
    alike, so an attribute call counts like the plain one."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _gate_in_expr(expr: ast.expr | None) -> bool:
    """Whether evaluating this expression certainly calls a recognised gate.

    Certain means the gate is not behind something the evaluation may skip: a
    `BoolOp` short-circuits, so a gate in an operand after the first may never
    run — in `if existing and not await can_read(...)`, a falsy `existing` asks
    nothing of the auth layer, and the path that skips the test still answers.
    A gate inside a comprehension counts: the comprehension runs when the
    expression does, and the gate covers what it feeds the caller.
    """
    if expr is None:
        return False
    if isinstance(expr, ast.BoolOp):
        return _gate_in_expr(expr.values[0])
    if isinstance(expr, ast.IfExp):
        return _gate_in_expr(expr.test)
    if isinstance(expr, ast.Call):
        return (_callee_name(expr) in PERMISSION_ENFORCEMENT_NAMES
                or any(_gate_in_expr(part)
                      for part in [*expr.args, *(kw.value for kw in expr.keywords)]))
    return any(_gate_in_expr(child) for child in ast.iter_child_nodes(expr))


def _gate_when_false(expr: ast.expr) -> bool:
    """Whether a test that came out false certainly called a gate on the way.

    `X1 or … or Xn` is false only once every operand ran and was false, so a
    gate in any of them counts. `X1 and …` may be false through any operand,
    so only X1 certainly ran. `not X` is false when X is true."""
    if isinstance(expr, ast.BoolOp):
        if isinstance(expr.op, ast.Or):
            return any(_gate_when_false(value) for value in expr.values)
        return _gate_in_expr(expr.values[0])
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not):
        return _gate_when_true(expr.operand)
    return _gate_in_expr(expr)


def _gate_when_true(expr: ast.expr) -> bool:
    """The mirror of `_gate_when_false`: `X1 and … and Xn` is true only once
    every operand ran, while `X1 or …` may be true through X1 alone."""
    if isinstance(expr, ast.BoolOp):
        if isinstance(expr.op, ast.And):
            return any(_gate_when_true(value) for value in expr.values)
        return _gate_in_expr(expr.values[0])
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not):
        return _gate_when_false(expr.operand)
    return _gate_in_expr(expr)


def _never_falls_through(stmts: list) -> bool:
    """Whether running these statements always ends the call: some statement
    in them is a `raise`, a `return`, or an `if` whose body and `else` both end
    it. Unlike `_aborts`, an `if` with no `else` does not count here — its
    false path goes on to the statements after it."""
    for st in stmts:
        if isinstance(st, (ast.Raise, ast.Return)):
            return True
        if (isinstance(st, ast.If) and st.orelse
                and _never_falls_through(st.body) and _never_falls_through(st.orelse)):
            return True
    return False


def _calls_a_gate(node: ast.AST) -> bool:
    """Whether this subtree calls a recognised gate, looking past the endpoint's
    own statements but not into a helper defined inside it — one of those runs
    only if the endpoint calls it, which the scan itself decides."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(child, ast.Call) and _callee_name(child) in PERMISSION_ENFORCEMENT_NAMES:
            return True
        if _calls_a_gate(child):
            return True
    return False


def _empty_answer(node: ast.expr | None) -> bool:
    """Whether a returned value carries no data: a literal (None, "", 0), a
    container of empties, or a model built from empties. `return
    BulkResponse(results=[], total=0, succeeded=0, failed=0)` is the empty bulk
    answer; `return JSONResponse(content=result)` is not.

    Only a constructor reads as a model: `cached.model_dump()` and
    `report.to_dict()` are method calls on whatever the endpoint holds, and the
    scan cannot know they build nothing. A call that does answer with nothing is
    named in MISS_ANSWER_FUNCTIONS."""
    if node is None or isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_empty_answer(elt) for elt in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            key is not None and _empty_answer(key) and _empty_answer(value)
            for key, value in zip(node.keys, node.values, strict=True)
        )
    if isinstance(node, ast.Call):
        if _callee_name(node) in MISS_ANSWER_FUNCTIONS:
            return True
        return isinstance(node.func, ast.Name) and all(
            _empty_answer(part)
            for part in [*node.args, *(kw.value for kw in node.keywords)]
        )
    return False


def _answers_here(st: ast.stmt) -> bool:
    """Whether this statement may return data to the caller, wherever in it
    that return sits. A helper defined inside the endpoint is not the
    endpoint answering."""
    if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return False
    if isinstance(st, ast.Return):
        return not _empty_answer(st.value)
    return any(_answers_here(child) for child in ast.iter_child_nodes(st))


def _gates_before_answering(stmts: list) -> bool:
    """The lenient twin of `_scan_seq`: a gate on any branch of a statement
    counts as reached. Used only to judge a reviewed waiver, where the strict
    walk has already said the branch is not provably taken. What the waiver
    still requires is that the endpoint gates somewhere ahead of its answer,
    so a listing can never hide a function that stopped gating, or one that
    answers first and checks after."""
    for st in stmts:
        if _calls_a_gate(st):
            return True
        if _answers_here(st):
            return False
    return True


def _aborts(stmts: list) -> bool:
    """Whether these statements only end the iteration or the call: `raise`,
    `continue`, or an `if` whose branches both abort. `break` is not one of
    them — it leaves a loop whose remaining items were never gated, and the
    code after the loop runs with no gate passed."""
    for st in stmts:
        if isinstance(st, (ast.Raise, ast.Continue)):
            continue
        if isinstance(st, ast.If) and _aborts(st.body) and _aborts(st.orelse):
            continue
        return False
    return True


def _combine(*paths: str) -> str:
    """Merge alternative paths. One escape makes it an escape; the gate counts
    only when every path reaches it; a path that neither gates nor answers —
    the fall-through of an `if` with no `else` — leaves it open."""
    if _ESCAPE in paths:
        return _ESCAPE
    if paths and all(path == _GATE for path in paths):
        return _GATE
    return _OPEN


def _scan_loop_body(body: list) -> bool:
    """Whether a loop body passes its gate before it can touch the item: gate
    first, or preceded only by guards that abort the iteration. A write placed
    before the gate inside the loop is what this catches."""
    if not body:
        return False
    head, *rest = body
    if _scan_stmt(head) == _GATE:
        return True
    return _aborts([head]) and _scan_loop_body(rest)


def _scan_loop(body: list) -> str:
    """A loop gates when its body gates before touching the item. Otherwise a
    gate inside is only conditional — the body may not run at all — while an
    ungated answer inside stays an answer."""
    if _scan_loop_body(body):
        return _GATE
    verdict = _scan_seq(body)
    return _OPEN if verdict == _GATE else verdict


def _scan_seq(stmts: list) -> str:
    """Walk a statement list in order; the first verdict that is not _OPEN is
    the verdict of the sequence, because the statements before it neither
    gated nor answered."""
    for st in stmts:
        verdict = _scan_stmt(st)
        if verdict != _OPEN:
            return verdict
    return _OPEN


def _scan_try(st: ast.Try) -> str:
    head = _scan_seq(st.body)
    others = [_scan_seq(handler.body) for handler in st.handlers]
    others.append(_scan_seq(st.orelse))
    # A handler runs on the path where the body raised, which may be before the
    # body reached its gate — so an answer with data in a handler escapes even
    # when the body gates.
    if head == _ESCAPE or _ESCAPE in others:
        return _ESCAPE
    if head == _GATE:
        return _GATE
    # A `finally` runs whatever the body and the handlers did — its gate is
    # passed on every path out of the statement.
    if st.finalbody and _scan_seq(st.finalbody) == _GATE:
        return _GATE
    return _OPEN


def _scan_stmt(st: ast.stmt) -> str:
    """Verdict for one statement, for the path that runs it."""
    if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        # A helper defined inside an endpoint is not run by the request.
        return _OPEN
    if isinstance(st, ast.Return):
        if _gate_in_expr(st.value):
            return _GATE
        return _OPEN if _empty_answer(st.value) else _ESCAPE
    if isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr)):
        return _GATE if _gate_in_expr(st.value) else _OPEN
    if isinstance(st, ast.Raise):
        return _GATE if _gate_in_expr(st.exc) else _OPEN
    if isinstance(st, ast.If):
        if _gate_in_expr(st.test):
            return _GATE
        body = _scan_seq(st.body)
        # A body that always ends the call leaves only the false path to go
        # on: if the test cannot come out false without calling a gate, the
        # `else` and everything after the statement run with it passed.
        if (body != _ESCAPE and _never_falls_through(st.body)
                and _gate_when_false(st.test)):
            return _GATE
        return _combine(body, _scan_seq(st.orelse))
    if isinstance(st, ast.While):
        if _gate_in_expr(st.test):
            return _GATE
        return _scan_loop(st.body)
    if isinstance(st, (ast.For, ast.AsyncFor)):
        return _scan_loop(st.body)
    if isinstance(st, (ast.With, ast.AsyncWith)):
        return _scan_seq(st.body)
    if isinstance(st, ast.Try):
        return _scan_try(st)
    if isinstance(st, ast.Match):
        # `ast.Match` has no `orelse` — a `case _` is an ordinary entry in
        # `cases`. Unless some case is irrefutable the subject may match none of
        # them and the statement is passed by, so what its cases gate is
        # conditional however uniform they look.
        paths = [_scan_seq(case.body) for case in st.cases]
        if _ESCAPE in paths:
            return _ESCAPE
        irrefutable = any(
            isinstance(case.pattern, ast.MatchAs) and case.pattern.pattern is None
            for case in st.cases
        )
        if irrefutable and all(path == _GATE for path in paths):
            return _GATE
        return _OPEN
    return _OPEN


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
    # A verbatim copy, not a delegate: wip-archive checks archives offline and
    # depends on pydantic alone, so it cannot import wip_auth (whose __init__
    # pulls in FastAPI and httpx). Guarded by
    # components/document-store/tests/test_archive_identity_parity.py, which
    # fails when the copy's source or outputs differ from the original.
    "libs/wip-archive/src/wip_archive/identity.py",
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

        Two parts. First, a `Depends(...)` gate on a function argument
        (`_admin: str = Depends(require_admin_key)`) or in the route
        decorator's dependencies=[...] gates the call before its body
        runs. Second, the body is walked for a gate reached on every path
        to an answer — see the gate-reachability helpers. A name match is
        the raw material, not the verdict: a gate under `if existing:`, a
        gate only in an `except` handler, or an answer returned before the
        gate is reached is reported even though the helper is called.

        Exemption mechanisms:
          1. Component-level: PERMISSION_EXEMPT_SERVICES (e.g., registry).
          2. Path-substring against the full route — for stateless
             utilities (/preview, /health, /stats, /count, /initialize).
          3. One-off function-name entries with literal rationale:
             PERMISSION_EXEMPT_FUNCTIONS (no gate needed at all) and
             PERMISSION_CONDITIONAL_GATE_FUNCTIONS (gated, on a branch the
             walk cannot prove; the helper call is still required).
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

        # 1. Depends-style gates on function arguments
        #    (`_admin: str = Depends(require_admin_key)`) or in the route
        #    decorator's dependencies=[...]. The gate is a name
        #    (Depends(require_admin_key)) or a factory call
        #    (Depends(require_groups([...]))).
        candidates = list(node.args.defaults) + list(node.args.kw_defaults)
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call):
                for kw in decorator.keywords:
                    if kw.arg == "dependencies" and isinstance(kw.value, (ast.List, ast.Tuple)):
                        candidates.extend(kw.value.elts)
        for default in candidates:
            if not (
                isinstance(default, ast.Call)
                and isinstance(default.func, ast.Name)
                and default.func.id == "Depends"
                and default.args
            ):
                continue
            if _is_depends_gate(default.args[0]):
                return  # Endpoint is gated via Depends.

        # 2. The body must reach a gate on every path to an answer.
        verdict = _scan_seq(node.body)
        if verdict == _GATE:
            return

        # 3. Gated on a reviewed branch: the helper is called, ahead of the
        #    answer, but from where the walk cannot prove it always runs. An
        #    endpoint listed here that stopped gating is still reported.
        if (
            func_name in PERMISSION_CONDITIONAL_GATE_FUNCTIONS
            and _calls_a_gate(node)
            and _gates_before_answering(node.body)
        ):
            return

        gated_somewhere = _calls_a_gate(node)
        if verdict == _ESCAPE:
            reason = (
                "answers the caller with data on a path that reaches no "
                "permission gate"
            )
        elif gated_somewhere:
            reason = (
                "calls a permission helper only conditionally, so a request can "
                "reach the answer without passing it"
            )
        else:
            reason = (
                "does not call any namespace permission helper "
                f"({sorted(PERMISSION_ENFORCEMENT_NAMES)})"
            )

        self.violations.append({
            **info,
            "rule": "permission-enforcement",
            "message": (
                f"{info['method'].upper()} {path}: {reason}. "
                f"Gate every path, or list the function in "
                f"PERMISSION_CONDITIONAL_GATE_FUNCTIONS (gated on a branch, "
                f"with the reason that branch is safe) or "
                f"PERMISSION_EXEMPT_FUNCTIONS (no gate applies) — with rationale."
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
        is_exempt = (
            any(p in path for p in exempt_patterns)
            or info.get("function", "") in PAGINATION_EXEMPT_FUNCTIONS
        )

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
