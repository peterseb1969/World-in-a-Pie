"""Cross-service test contracts every WIP suite can enforce.

The platform rule: every model that parses caller input declares
``extra='forbid'``. A lenient request model silently DROPS unknown keys —
the caller believes an option took effect while the server never saw it
(a retired ``latest_only`` once stamped a backup manifest as latest-only
while the archive carried every version). The rule previously lived only
in per-component ``StrictModel`` bases inside ``api_models.py`` files, so
request models born in other files escaped it by construction. This
helper is the enforcement: each service's suite asserts it over its own
FastAPI app, so a new lax request model fails CI wherever the file lives.

A second contract lives here for the same reason (one place, every
suite): ``assert_no_factory_dependencies`` — no route dependency may be
a dependency FACTORY used uncalled. wip-auth's group gates
(``require_identity`` / ``require_groups`` / ``require_admin`` /
``optional_identity``) are factories; written as ``Depends(require_admin)``
FastAPI calls the zero-arg factory at request time and hands the
returned closure to the endpoint as the dependency's VALUE. Nothing
raises, the check never executes, every authenticated caller passes.
The Registry shipped exactly that for eight months across its whole
admin surface, privilege escalation included — and happy-path tests
cannot see it, because an authorized caller passes either way.

A third contract, ``assert_anonymous_refused``, is shape-agnostic: it
fires a request carrying NO credentials at every route of the real app
and requires a refusal (401/403) unless the route is on the service's
explicit public allowlist. Wiring checks only see dependency-shaped
enforcement; this one sees the outcome, so a missing auth middleware, a
missing route dependency and a mis-allowlisted path all fail it alike.
Two services (reporting-sync, ingest-gateway) once served every route
anonymously while their wiring tests stayed green — that is what this
gate exists to make impossible.

A fourth contract, ``assert_error_items_carry_codes``, is a source check:
every bulk error item a service constructs carries a machine-readable
``error_code``, so callers branch on the code and never on the English
message. The rule was written down in the API conventions, and a
template-store-wide fix had just applied it, when the next change to a
neighbouring service added two new code-less error items — one for a
condition template-store already reported with a code. A written rule
does not reach the next change; this gate does.
"""

import ast
import collections.abc
import inspect
import typing
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_args

from fastapi.routing import APIRoute
from pydantic import BaseModel


def _body_models(annotation: Any):
    """Yield BaseModel classes reachable from a body annotation.

    Unwraps the containers request bodies actually use — ``list[Model]``
    (the bulk-first envelope), ``Model | None``, nested unions. Non-model
    leaves (primitives, dicts) are not the gate's concern.
    """
    if annotation is None:
        return
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
        return
    for arg in get_args(annotation):
        yield from _body_models(arg)


def _iter_api_routes(routes: Any):
    """Yield every APIRoute reachable from a route list, at any nesting.

    ``app.routes`` is NOT flat: FastAPI >= 0.139 registers
    ``include_router`` output as a lazy ``_IncludedRouter`` whose real
    APIRoutes live on its ``original_router``; mounts and routers carry
    theirs on ``.routes``. A walker that only checks the top level sees
    health/docs routes and nothing else — which made the first version
    of this gate pass green while examining zero request bodies (an
    environment-dependent no-op: older FastAPI flattens eagerly and the
    same gate genuinely checked things there). Hence also the collected
    counter in the collector and the canary in the assert below.
    """
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
            continue
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _iter_api_routes(inner.routes)
            continue
        sub = getattr(route, "routes", None)
        if sub:
            yield from _iter_api_routes(sub)


def collect_lax_request_models(app: Any) -> tuple[list[tuple[str, str, Any]], int]:
    """Return (offenders, collected_count) over every top-level
    request-body model reachable on ``app``.

    Offenders are (route path, model name, extra-setting) for models that
    do not declare extra='forbid'; collected_count is how many body
    models the walk examined in total — callers must treat zero as
    suspicious, not as success (see _iter_api_routes).
    """
    offenders: list[tuple[str, str, Any]] = []
    seen: set[tuple[str, str]] = set()
    collected = 0
    for route in _iter_api_routes(app.routes):
        for param in route.dependant.body_params:
            annotation = getattr(param.field_info, "annotation", None)
            for model in _body_models(annotation):
                collected += 1
                extra = model.model_config.get("extra")
                if extra != "forbid":
                    key = (route.path, model.__name__)
                    if key not in seen:
                        seen.add(key)
                        offenders.append((route.path, model.__name__, extra))
    return offenders, collected


def assert_strict_request_models(app: Any, *, expect_request_bodies: bool = True) -> None:
    """Fail if any request-body model on ``app`` accepts unknown keys.

    Deliberately no allow-list parameter: a request model either forbids
    unknown keys or it is a bug. Retiring a field is done with a
    tombstone (keep the field, reject it loudly with the story) — never
    by loosening the model.

    ``expect_request_bodies`` is the canary against the gate itself going
    quiet: when True (default), a walk that collected ZERO body models
    fails — an empty walk means the route traversal broke (new FastAPI
    nesting, an app wired differently), not that the service is clean.
    Pass False only for services that genuinely have no request bodies.
    """
    offenders, collected = collect_lax_request_models(app)
    if expect_request_bodies:
        assert collected > 0, (
            "The strictness gate collected ZERO request-body models — the "
            "route walk is broken (or this service lost its request "
            "bodies). A gate that examines nothing must not pass: fix "
            "_iter_api_routes for this app's routing shape, or pass "
            "expect_request_bodies=False if the service genuinely has no "
            "request bodies."
        )
    assert not offenders, (
        "Request models that silently drop unknown keys (need "
        "extra='forbid' — derive from the component's StrictModel):\n"
        + "\n".join(
            f"  {path}: {name} (extra={extra!r})"
            for path, name, extra in offenders
        )
    )


def _wip_auth_factories() -> tuple[Any, ...]:
    from .dependencies import (
        optional_identity,
        require_admin,
        require_groups,
        require_identity,
    )

    return (require_identity, require_groups, require_admin, optional_identity)


def _returns_a_callable(call: Any) -> bool:
    """True when the dependency's declared return type is itself a
    Callable — the signature of "returns a dependency" rather than "is
    a dependency". Annotation resolution is best-effort: anything
    unresolvable counts as not-a-factory (the identity check against
    wip-auth's own factories still applies regardless)."""
    try:
        hints = typing.get_type_hints(call)
    except Exception:
        try:
            annotation = inspect.signature(call).return_annotation
        except (TypeError, ValueError):
            return False
        if annotation is inspect.Signature.empty:
            return False
        hints = {"return": annotation}
    ret = hints.get("return")
    if ret is None:
        return False
    return (
        ret is collections.abc.Callable
        or typing.get_origin(ret) is collections.abc.Callable
    )


def collect_factory_dependencies(app: Any) -> tuple[list[str], int]:
    """Return (offenders, examined_count) over every route dependency on
    ``app``. Offenders are "METHODS path -> callable" strings for
    dependencies that are factory-shaped; examined_count is how many
    dependency callables the walk saw in total — zero means the walk is
    broken, never that the app is clean (same canary doctrine as the
    strictness gate above)."""
    factories = _wip_auth_factories()
    offenders: list[str] = []
    examined = 0
    for route in _iter_api_routes(app.routes):
        stack = [route.dependant]
        while stack:
            dependant = stack.pop()
            stack.extend(dependant.dependencies)
            call = dependant.call
            if call is None or call is route.endpoint:
                continue
            examined += 1
            if any(call is f for f in factories) or _returns_a_callable(call):
                methods = ",".join(sorted(route.methods or []))
                name = getattr(call, "__name__", repr(call))
                offenders.append(f"{methods} {route.path} -> {name}")
    return sorted(set(offenders)), examined


def assert_no_factory_dependencies(app: Any, *, expect_dependencies: bool = True) -> None:
    """Fail if any route dependency on ``app`` is a factory used
    uncalled — the silently-open-gate shape described in the module
    docstring.

    ``expect_dependencies`` is the canary against the gate itself going
    quiet: when True (default), a walk that examined ZERO dependency
    callables fails — an empty walk means the route traversal broke,
    not that the service is clean. Pass False only for an app that
    genuinely declares no non-endpoint dependencies.

    This is wiring-shape verification only. It does not replace refusal
    tests (a non-privileged credential must still get its 403/404 on
    every privileged route): a correctly wired gate guarding the wrong
    group is invisible here and loud there.
    """
    offenders, examined = collect_factory_dependencies(app)
    if expect_dependencies:
        assert examined > 0, (
            "The factory-dependency gate examined ZERO dependency "
            "callables — the route walk is broken (or this app really "
            "declares no dependencies). A gate that examines nothing "
            "must not pass: fix _iter_api_routes for this app's routing "
            "shape, or pass expect_dependencies=False if the app "
            "genuinely has no non-endpoint dependencies."
        )
    assert not offenders, (
        "Factory used uncalled as a FastAPI dependency — the returned "
        "closure becomes the dependency's VALUE and the check never "
        "runs (silently open gate):\n"
        + "\n".join(f"  {o}" for o in offenders)
        + "\nFix: call the factory — Depends(require_admin()) — or use "
        "a direct dependency such as wip_auth.require_admin_identity."
    )


# =========================================================================
# Error items carry an error_code
# =========================================================================


def _is_error_status(node: ast.AST | None) -> bool:
    """``"error"`` as a literal, or an ``<Enum>.ERROR`` member."""
    if isinstance(node, ast.Constant):
        return node.value == "error"
    return isinstance(node, ast.Attribute) and node.attr == "ERROR"


def _is_none(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def collect_codeless_error_items(package_dir: str | Path) -> tuple[list[str], int]:
    """Return (offenders, examined_count) over every error item constructed
    in the Python sources under ``package_dir``.

    An error item is a call with ``status="error"`` (or ``status=X.ERROR``)
    among its keyword arguments, or a dict literal with a ``"status":
    "error"`` entry. It is an offender when it names no ``error_code`` and
    does not unpack a mapping (``**fields`` — the ``error_fields(exc)``
    helper's shape, which always supplies a code), or when it passes
    ``error_code=None`` outright. A code taken from a variable is trusted:
    a source walk cannot see its runtime value, so a path that may hand
    over None needs a behaviour test of the error item it produces. Offenders are
    ``"relative/path.py:line"`` strings; examined_count is how many error
    items the walk saw — zero means the walk found nothing, never that the
    service is clean (same canary doctrine as the gates above).
    """
    root = Path(package_dir)
    offenders: list[str] = []
    examined = 0
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                kwargs = {k.arg: k.value for k in node.keywords}
                if not _is_error_status(kwargs.get("status")):
                    continue
                coded = ("error_code" in kwargs and not _is_none(kwargs["error_code"])) \
                    or None in kwargs
            elif isinstance(node, ast.Dict):
                entries = {
                    k.value if isinstance(k, ast.Constant) else k: v
                    for k, v in zip(node.keys, node.values, strict=True)
                }
                if not _is_error_status(entries.get("status")):
                    continue
                coded = ("error_code" in entries and not _is_none(entries["error_code"])) \
                    or None in entries
            else:
                continue
            examined += 1
            if not coded:
                offenders.append(f"{path.relative_to(root)}:{node.lineno}")
    return offenders, examined


def assert_error_items_carry_codes(
    package_dir: str | Path, *, expect_error_items: bool = True
) -> None:
    """Fail if any error item constructed under ``package_dir`` lacks an
    ``error_code``.

    Deliberately no allow-list parameter: an error item without a code is
    a caller left matching English. Use the service's ``error_fields(exc)``
    helper, which falls back to ``invalid_request`` for an uncategorised
    refusal, or name the code inline.

    ``expect_error_items`` is the canary: when True (default), a walk that
    found ZERO error items fails — the source path is wrong or the
    detection broke. Pass False only for a service that genuinely builds
    no error items.
    """
    offenders, examined = collect_codeless_error_items(package_dir)
    if expect_error_items:
        assert examined > 0, (
            f"The error-code gate found ZERO error items under {package_dir} — "
            "the path is wrong or the detection broke. A gate that examines "
            "nothing must not pass; pass expect_error_items=False only if the "
            "service genuinely builds no error items."
        )
    assert not offenders, (
        "Error items without an error_code (callers can only match the "
        "message). Build them with the service's error_fields(exc), or name "
        "the code:\n" + "\n".join(f"  {o}" for o in offenders)
    )


# =========================================================================
# Credential-less refusal walker
# =========================================================================

_SKIPPED_METHODS = frozenset({"HEAD", "OPTIONS"})
_REFUSED = frozenset({401, 403})
_OPENAPI_METHODS = frozenset(
    {"get", "put", "post", "delete", "patch", "head", "options", "trace"}
)


@dataclass(frozen=True)
class AnonymousProbe:
    """One credential-less request the walker sent, and what came back."""

    method: str
    template: str  # full path template, e.g. "/api/svc/items/{item_id}"
    path: str  # the concrete path requested
    status: int
    body_excerpt: str

    @property
    def key(self) -> str:
        """Allowlist key: "METHOD /full/path/template"."""
        return f"{self.method} {self.template}"


def _probe_value(param_schema: Mapping[str, Any]) -> str:
    """A routable placeholder for one path parameter, from its OpenAPI
    schema: integers and numbers need digits, UUID-typed params a UUID,
    anything else a plain token."""
    kind = param_schema.get("type")
    if kind in ("integer", "number"):
        return "1"
    if param_schema.get("format") == "uuid":
        return str(uuid.UUID(int=0))
    return "walker-probe"


def _convertor_value(convertor: Any) -> str:
    """Same as _probe_value, for a route addressed by its Starlette path
    convertors instead of the OpenAPI schema."""
    name = type(convertor).__name__
    if name in ("IntegerConvertor", "FloatConvertor"):
        return "1"
    if name == "UUIDConvertor":
        return str(uuid.UUID(int=0))
    return "walker-probe"


def _fill(template: str, values: Mapping[str, str]) -> str:
    path = template
    for name, value in values.items():
        path = path.replace("{" + name + "}", value)
        path = path.replace("{" + name + ":path}", value)
    return path


def _addressable_operations(app: Any) -> list[tuple[str, str, str]]:
    """Return (method, full path template, concrete path) for every route
    operation on ``app``.

    Full paths come from the OpenAPI schema, which renders include_router
    prefixes on every FastAPI generation — unlike ``APIRoute.path``, which
    the lazy ``_IncludedRouter`` generation leaves unprefixed (see
    _iter_api_routes). Routes hidden from the schema are addressed by
    their own path only when they sit directly on the app, where that
    path is the full path on every generation. A hidden route inside an
    included router cannot be addressed reliably, and a schema whose
    operation count disagrees with the route walk means some route is
    unaddressable; both fail loudly rather than shrinking the walk.
    """
    top_level = {id(r) for r in app.routes if isinstance(r, APIRoute)}
    unaddressable: list[str] = []
    expected_schema_ops = 0
    operations: list[tuple[str, str, str]] = []

    for route in _iter_api_routes(app.routes):
        methods = sorted((route.methods or set()) - _SKIPPED_METHODS)
        if route.include_in_schema:
            expected_schema_ops += len(methods)
            continue
        if id(route) not in top_level:
            unaddressable.append(f"{','.join(methods)} {route.path}")
            continue
        values = {
            name: _convertor_value(conv)
            for name, conv in route.param_convertors.items()
        }
        for method in methods:
            operations.append((method, route.path, _fill(route.path, values)))

    schema_ops = 0
    for template, item in (app.openapi().get("paths") or {}).items():
        for method, operation in item.items():
            if method not in _OPENAPI_METHODS:
                continue
            upper = method.upper()
            if upper in _SKIPPED_METHODS:
                continue
            schema_ops += 1
            values = {
                p["name"]: _probe_value(p.get("schema") or {})
                for p in (operation.get("parameters") or [])
                if p.get("in") == "path"
            }
            operations.append((upper, template, _fill(template, values)))

    assert not unaddressable, (
        "Routes hidden from the OpenAPI schema inside an included router "
        "cannot be addressed by the anonymous-refusal walker on every "
        "FastAPI generation. Register them on the app directly, or include "
        "them in the schema:\n" + "\n".join(f"  {r}" for r in unaddressable)
    )
    assert schema_ops == expected_schema_ops, (
        f"The OpenAPI schema lists {schema_ops} operations but the route "
        f"walk found {expected_schema_ops} schema-visible route methods — "
        "some route cannot be addressed by the walker (duplicate "
        "method+path, or a routing shape the walk does not understand). "
        "A gate that silently skips routes must not pass."
    )
    return operations


def collect_anonymous_responses(app: Any) -> list[AnonymousProbe]:
    """Send one credential-less request per route operation on ``app`` and
    return what each answered.

    The request carries no headers, no query string and no body, and the
    app's lifespan is NOT run (no databases, no background workers). An
    enforcing route refuses before validation or handler code runs; a
    route that reaches its handler may fail on missing state, which is
    reported as the status it produced (exceptions become 500) rather
    than crashing the walk.
    """
    import asyncio

    import httpx

    operations = _addressable_operations(app)

    async def _walk() -> list[AnonymousProbe]:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        probes: list[AnonymousProbe] = []
        async with httpx.AsyncClient(
            transport=transport, base_url="http://walker"
        ) as client:
            for method, template, path in operations:
                response = await client.request(method, path)
                probes.append(AnonymousProbe(
                    method=method,
                    template=template,
                    path=path,
                    status=response.status_code,
                    body_excerpt=response.text[:120],
                ))
        return probes

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_walk())
    raise RuntimeError(
        "collect_anonymous_responses runs its own event loop; call it from "
        "a synchronous test, not from inside an async one."
    )


def _explain(status: int) -> str:
    if status == 404:
        return ("404 — the walker built a path the app does not route, or "
                "the route answers 404 before checking credentials")
    if status == 422:
        return "422 — request validation ran before authentication"
    if status >= 500:
        return f"{status} — the handler ran without credentials and failed"
    if 200 <= status < 300:
        return f"{status} — served without credentials"
    return f"{status} — not a refusal"


def assert_anonymous_refused(
    app: Any,
    *,
    public: Mapping[str, str],
    expect_routes: bool = True,
) -> None:
    """Fail unless every route on ``app`` refuses a credential-less request.

    ``public`` is the service's explicit allowlist: keys are
    ``"METHOD /full/path/template"`` (as in the OpenAPI schema), values a
    non-empty reason. A public route must NOT refuse (a gated health probe
    is its own bug) and must be routable. Every allowlist entry must match
    a route the walk visited — a stale entry fails, so the list cannot
    quietly outlive the route it excused.

    Every other route must answer 401 or 403. Anything else fails with an
    explanation of what the status means.

    ``expect_routes`` is the canary (same doctrine as the other gates): a
    walk that visited zero routes fails.
    """
    empty_reasons = sorted(k for k, reason in public.items() if not reason.strip())
    assert not empty_reasons, (
        "Public allowlist entries need a reason:\n"
        + "\n".join(f"  {k}" for k in empty_reasons)
    )

    probes = collect_anonymous_responses(app)
    if expect_routes:
        assert probes, (
            "The anonymous-refusal walker visited ZERO routes — the route "
            "walk is broken (or the app has no routes). A gate that "
            "examines nothing must not pass."
        )

    seen = {p.key for p in probes}
    stale = sorted(set(public) - seen)
    open_routes = [
        f"  {p.key} -> {_explain(p.status)}"
        for p in probes
        if p.key not in public and p.status not in _REFUSED
    ]
    gated_public = [
        f"  {p.key} -> {p.status} (allowlisted as public: {public[p.key]})"
        for p in probes
        if p.key in public and (p.status in _REFUSED or p.status == 404)
    ]

    problems: list[str] = []
    if open_routes:
        problems.append(
            "Routes that did not refuse a request without credentials:\n"
            + "\n".join(open_routes)
        )
    if gated_public:
        problems.append(
            "Allowlisted public routes that refused or did not route:\n"
            + "\n".join(gated_public)
        )
    if stale:
        problems.append(
            "Public allowlist entries that match no route (stale):\n"
            + "\n".join(f"  {k}" for k in stale)
        )
    assert not problems, "\n\n".join(problems)
