"""api_route_methods: every route an app serves, schema-hidden ones included.

A completeness gate built from ``app.openapi()`` misses a route registered
with ``include_in_schema=False``, so the walk reads the routes themselves,
carrying each include_router prefix.
"""

from __future__ import annotations

import pytest
from fastapi import APIRouter, FastAPI

from wip_auth.testing import api_route_methods


def _app() -> FastAPI:
    app = FastAPI()
    inner = APIRouter()

    @inner.get("/items/{item_id}")
    async def item(item_id: str):
        return {}

    @inner.post("/hidden", include_in_schema=False)
    async def hidden():
        return {}

    outer = APIRouter()
    outer.include_router(inner, prefix="/inner")
    app.include_router(outer, prefix="/api/x")

    @app.get("/livez", include_in_schema=False)
    async def livez():
        return {}

    return app


def test_lists_schema_routes_with_their_prefixes_and_hidden_routes():
    assert api_route_methods(_app()) == {
        ("GET", "/api/x/inner/items/{item_id}"),
        ("POST", "/api/x/inner/hidden"),
        ("GET", "/livez"),
    }


def test_a_walk_that_misses_schema_paths_raises(monkeypatch):
    import wip_auth.testing as testing

    monkeypatch.setattr(testing, "_walk_route_paths", lambda routes, prefix="": iter(()))
    with pytest.raises(AssertionError, match="routing shape changed"):
        api_route_methods(_app())
