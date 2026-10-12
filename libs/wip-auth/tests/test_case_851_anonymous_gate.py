"""The credential-less refusal walker (``assert_anonymous_refused``).

Provenance: CASE-851 / CASE-855 — reporting-sync and ingest-gateway
served every route without credentials while their wiring tests stayed
green, because those tests only looked for dependency-shaped
enforcement. This gate checks the outcome instead: a request carrying no
credentials must be refused everywhere except an explicit, reasoned
public allowlist.

Each test builds a small FastAPI app inline. Routes are guarded with
``require_api_key`` and no auth middleware: with no middleware the
current identity is None, which is exactly the anonymous case.
"""

import re

import pytest
from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel, ConfigDict

from wip_auth import require_api_key
from wip_auth.testing import assert_anonymous_refused, collect_anonymous_responses

HEALTH = {"GET /health": "probe"}


def _app_with_health() -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


def test_unguarded_route_is_caught():
    app = _app_with_health()

    @app.get("/data")
    async def data():
        return {"rows": []}

    with pytest.raises(AssertionError) as exc:
        assert_anonymous_refused(app, public=HEALTH)
    assert "GET /data" in str(exc.value)
    assert "served without credentials" in str(exc.value)


def test_router_level_dependency_passes_with_prefix():
    """A prefixed router guarded at router level is addressed by its FULL
    path on every FastAPI generation (the schema renders the prefix even
    where APIRoute.path does not)."""
    app = _app_with_health()
    router = APIRouter(prefix="/api/svc", dependencies=[Depends(require_api_key)])

    @router.get("/items")
    async def items():
        return []

    @router.delete("/items/{item_id}")
    async def delete_item(item_id: str):
        return {}

    app.include_router(router)
    assert_anonymous_refused(app, public=HEALTH)

    keys = {p.key: p.status for p in collect_anonymous_responses(app)}
    assert keys["GET /api/svc/items"] == 401
    assert keys["DELETE /api/svc/items/{item_id}"] == 401


def test_dependency_runs_before_query_validation():
    """Pins the ordering assumption the gate relies on: a missing required
    query parameter must not turn an anonymous refusal into a 422."""
    app = _app_with_health()
    router = APIRouter(dependencies=[Depends(require_api_key)])

    @router.get("/search")
    async def search(namespace: str):
        return []

    app.include_router(router)
    assert_anonymous_refused(app, public=HEALTH)


def test_dependency_runs_before_body_validation():
    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        sql: str

    app = _app_with_health()
    router = APIRouter(dependencies=[Depends(require_api_key)])

    @router.post("/query")
    async def query(body: Payload):
        return {}

    app.include_router(router)
    assert_anonymous_refused(app, public=HEALTH)


def test_integer_path_parameter_is_filled_routably():
    app = _app_with_health()

    @app.get("/jobs/{job_id}")
    async def job(job_id: int):
        return {"job_id": job_id}

    probes = {p.key: p for p in collect_anonymous_responses(app)}
    probe = probes["GET /jobs/{job_id}"]
    assert probe.path == "/jobs/1"
    assert probe.status == 200  # routed and served — and therefore caught below
    with pytest.raises(AssertionError, match=re.escape("GET /jobs/{job_id}")):
        assert_anonymous_refused(app, public=HEALTH)


def test_nested_include_is_reached_by_full_path():
    app = _app_with_health()
    inner = APIRouter()

    @inner.get("/deep")
    async def deep():
        return {}

    outer = APIRouter(prefix="/api")
    outer.include_router(inner, prefix="/nested")
    app.include_router(outer)

    with pytest.raises(AssertionError) as exc:
        assert_anonymous_refused(app, public=HEALTH)
    assert "GET /api/nested/deep" in str(exc.value)


def test_raising_handler_is_reported_not_crashing():
    app = _app_with_health()

    @app.get("/fragile")
    async def fragile():
        raise RuntimeError("no database in tests")

    probes = {p.key: p.status for p in collect_anonymous_responses(app)}
    assert probes["GET /fragile"] == 500
    with pytest.raises(AssertionError, match="handler ran without credentials"):
        assert_anonymous_refused(app, public=HEALTH)


def test_public_route_that_refuses_fails():
    """Allowlisting a route that demands credentials is its own bug — a
    gated health probe."""
    app = FastAPI()

    @app.get("/health", dependencies=[Depends(require_api_key)])
    async def health():
        return {}

    with pytest.raises(AssertionError, match="refused or did not route"):
        assert_anonymous_refused(app, public=HEALTH)


def test_stale_allowlist_entry_fails():
    app = _app_with_health()
    with pytest.raises(AssertionError, match="stale"):
        assert_anonymous_refused(app, public={**HEALTH, "GET /gone": "old probe"})


def test_allowlist_entry_needs_a_reason():
    app = _app_with_health()
    with pytest.raises(AssertionError, match="need a reason"):
        assert_anonymous_refused(app, public={"GET /health": "  "})


def test_hidden_top_level_route_is_still_walked():
    app = _app_with_health()

    @app.get("/api/svc/", include_in_schema=False)
    async def root():
        return {"version": "x"}

    with pytest.raises(AssertionError, match="GET /api/svc/"):
        assert_anonymous_refused(app, public=HEALTH)
    assert_anonymous_refused(
        app, public={**HEALTH, "GET /api/svc/": "build-provenance probe"}
    )


def test_hidden_route_inside_a_router_is_never_silently_skipped():
    """FastAPI-generation-dependent by nature: eager include_router copies
    the route onto the app with its full path (so it is walked and caught
    as open); the lazy _IncludedRouter generation leaves it nested and
    unprefixed (so the walker refuses to guess its path). Either way the
    gate fails — what must never happen is a pass that skipped it."""
    app = _app_with_health()
    router = APIRouter(prefix="/api/svc")

    @router.get("/secret", include_in_schema=False)
    async def secret():
        return {}

    app.include_router(router)
    with pytest.raises(AssertionError) as exc:
        assert_anonymous_refused(app, public=HEALTH)
    message = str(exc.value)
    assert "cannot be addressed" in message or "GET /api/svc/secret" in message


def test_empty_app_trips_the_canary():
    with pytest.raises(AssertionError, match="ZERO routes"):
        assert_anonymous_refused(FastAPI(), public={})
