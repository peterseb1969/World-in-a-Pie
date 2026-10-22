"""Writes resolve against the Registry, never the cache (CASE-927).

The resolution cache keeps a mapping for 5 minutes. A namespace deleted and
re-created under the same name kept resolving `ns:VALUE` to the deleted
terminology's id for those minutes, so writes (creating a term under it) failed
with "terminology <deleted id> not found". The rule "writes must hit the
Registry" was opt-in per route (bypass_cache) and routes forgot it. Now every
request other than GET/HEAD/OPTIONS bypasses the cache read automatically.

A stand-in Registry answers `paint:COLOR` with an id that changes between
calls, as a delete and re-create would.
"""

import json

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from wip_auth.resolve import (
    WriteRequestMiddleware,
    _write_request,
    clear_resolution_cache,
    resolve_entity_id,
    resolve_entity_ids,
)

RESOLVE_URL = "http://localhost:8001/api/registry/entries/resolve"
OLD_ID = "019a0000-0000-7000-8000-000000000001"
NEW_ID = "019a0000-0000-7000-8000-000000000002"


@pytest.fixture(autouse=True)
def clean_cache():
    clear_resolution_cache()
    yield
    clear_resolution_cache()


@pytest.fixture
def registry(httpx_mock):
    """Answers COLOR with `current[0]`; records how many lookups it served."""
    current = [OLD_ID]
    calls: list[dict] = []

    def resolve(request: httpx.Request) -> httpx.Response:
        items = json.loads(request.content)
        calls.extend(items)
        return httpx.Response(200, json={"results": [
            {"status": "found", "entry_id": current[0]} for _ in items
        ]})

    httpx_mock.add_callback(resolve, url=RESOLVE_URL, is_optional=True, is_reusable=True)
    return current, calls


@pytest.mark.asyncio
async def test_a_read_uses_the_cache(registry):
    current, calls = registry
    assert await resolve_entity_id("paint:COLOR", "terminology", "paint") == OLD_ID
    current[0] = NEW_ID
    assert await resolve_entity_id("paint:COLOR", "terminology", "paint") == OLD_ID
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_write_skips_a_stale_cache_entry_and_refreshes_it(registry):
    current, calls = registry
    await resolve_entity_id("paint:COLOR", "terminology", "paint")  # cached: OLD_ID
    current[0] = NEW_ID  # deleted and re-created

    token = _write_request.set(True)
    try:
        assert await resolve_entity_id("paint:COLOR", "terminology", "paint") == NEW_ID
    finally:
        _write_request.reset(token)

    # The fresh answer replaced the stale entry for later reads too.
    assert await resolve_entity_id("paint:COLOR", "terminology", "paint") == NEW_ID
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_a_bulk_write_skips_the_cache_too(registry):
    current, _ = registry
    await resolve_entity_ids(["paint:COLOR"], "terminology", "paint")
    current[0] = NEW_ID
    token = _write_request.set(True)
    try:
        resolved = await resolve_entity_ids(["paint:COLOR"], "terminology", "paint")
    finally:
        _write_request.reset(token)
    assert resolved["paint:COLOR"] == NEW_ID


def _probe_app():
    async def probe(request):
        return JSONResponse({"write": _write_request.get()})

    app = Starlette(routes=[Route("/probe", probe, methods=["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"])])
    app.add_middleware(WriteRequestMiddleware)
    return TestClient(app)


@pytest.mark.parametrize("method,write", [
    ("GET", False), ("OPTIONS", False),
    ("POST", True), ("PUT", True), ("PATCH", True), ("DELETE", True),
])
def test_the_middleware_marks_writes(method, write):
    response = _probe_app().request(method, "/probe")
    assert response.json() == {"write": write}


def test_the_flag_does_not_leak_out_of_the_request():
    _probe_app().post("/probe")
    assert _write_request.get() is False
