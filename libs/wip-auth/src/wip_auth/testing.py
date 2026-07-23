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


def collect_lax_request_models(app: Any) -> list[tuple[str, str, Any]]:
    """Return (route path, model name, extra-setting) for every top-level
    request-body model on ``app`` that does not declare extra='forbid'."""
    offenders: list[tuple[str, str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for param in route.dependant.body_params:
            annotation = getattr(param.field_info, "annotation", None)
            for model in _body_models(annotation):
                extra = model.model_config.get("extra")
                if extra != "forbid":
                    key = (route.path, model.__name__)
                    if key not in seen:
                        seen.add(key)
                        offenders.append((route.path, model.__name__, extra))
    return offenders


def assert_strict_request_models(app: Any) -> None:
    """Fail if any request-body model on ``app`` accepts unknown keys.

    Deliberately no allow-list parameter: a request model either forbids
    unknown keys or it is a bug. Retiring a field is done with a
    tombstone (keep the field, reject it loudly with the story) — never
    by loosening the model.
    """
    offenders = collect_lax_request_models(app)
    assert not offenders, (
        "Request models that silently drop unknown keys (need "
        "extra='forbid' — derive from the component's StrictModel):\n"
        + "\n".join(
            f"  {path}: {name} (extra={extra!r})"
            for path, name, extra in offenders
        )
    )
