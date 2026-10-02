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
"""

import collections.abc
import inspect
import typing
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
