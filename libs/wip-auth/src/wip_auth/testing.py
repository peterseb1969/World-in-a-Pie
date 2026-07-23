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
"""

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
