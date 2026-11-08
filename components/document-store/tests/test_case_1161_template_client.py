"""TemplateStoreClient.get_template's fetch and caches, pinned before its
refactor.

Provenance: CASE-1161. The suite replaces the template-store client wholesale,
so get_template's own paths never ran: the pinned-version and "latest"
caches (and the TTL that expires the latter), the URL forms (by id, by
value, raw), the 404 / 409 / other-status / transport-error answers, and the
cache fill (a latest fetch also fills its version's entry). These drive the
real method against an httpx MockTransport.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from document_store.services import template_store_client as tsc
from document_store.services.template_store_client import TemplateStoreClient, TemplateStoreError


class _Store:
    """A template store answering from ``responses`` by path, recording calls."""

    def __init__(self, responses: dict[str, httpx.Response]):
        self.responses = responses
        self.calls: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.path == "/boom":
            raise httpx.ConnectError("refused", request=request)
        return self.responses.get(request.url.path, httpx.Response(404))


@pytest.fixture
def store(monkeypatch):
    holder: dict = {}
    real = httpx.AsyncClient

    def client(*args, **kwargs):
        return real(transport=httpx.MockTransport(holder["store"].handler))

    monkeypatch.setattr(tsc.httpx, "AsyncClient", client)

    def make(responses):
        holder["store"] = _Store(responses)
        return holder["store"]
    return make


def _client() -> TemplateStoreClient:
    client = TemplateStoreClient(base_url="http://ts", api_key="k")
    client._namespace_is_fresh = AsyncMock(return_value=None)
    return client


T = "/api/template-store/templates/T1"


def _ok(version: int = 2) -> httpx.Response:
    return httpx.Response(200, json={"template_id": "T1", "version": version, "namespace": "wip"})


@pytest.mark.asyncio
async def test_a_pinned_version_is_fetched_once(store):
    s = store({T: _ok(3)})
    client = _client()
    assert (await client.get_template(template_id="T1", version=3))["version"] == 3
    assert (await client.get_template(template_id="T1", version=3))["version"] == 3
    assert len(s.calls) == 1 and s.calls[0].url.params["version"] == "3"
    client._namespace_is_fresh.assert_awaited()


@pytest.mark.asyncio
async def test_latest_is_cached_for_the_ttl_and_fills_its_versions_entry(store, monkeypatch):
    s = store({T: _ok(2)})
    client = _client()
    now = [1000.0]
    monkeypatch.setattr(tsc.time, "monotonic", lambda: now[0])
    await client.get_template(template_id="T1")
    await client.get_template(template_id="T1")
    assert len(s.calls) == 1
    # The latest fetch filled the pinned entry of the version it returned.
    await client.get_template(template_id="T1", version=2)
    assert len(s.calls) == 1
    now[0] += tsc.LATEST_TTL_SECONDS + 1
    await client.get_template(template_id="T1")
    assert len(s.calls) == 2


@pytest.mark.asyncio
async def test_the_url_follows_the_identifier_and_inheritance(store):
    s = store({
        "/api/template-store/templates/by-value/PERSON": _ok(),
        "/api/template-store/templates/T1/raw": _ok(),
    })
    client = _client()
    assert await client.get_template(template_value="PERSON") is not None
    assert await client.get_template(template_id="T1", resolve_inheritance=False) is not None
    assert [c.url.path for c in s.calls] == [
        "/api/template-store/templates/by-value/PERSON",
        "/api/template-store/templates/T1/raw",
    ]
    assert await client.get_template() is None


@pytest.mark.asyncio
async def test_a_missing_template_is_none_and_not_cached(store):
    s = store({})
    client = _client()
    assert await client.get_template(template_id="T1") is None
    assert await client.get_template(template_id="T1") is None
    assert len(s.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(("body", "message", "code"), [
    (json.dumps({"detail": "chain broken", "error_code": "inheritance_unresolved"}),
     "chain broken", "inheritance_unresolved"),
    ("not json", "not json", None),
    (json.dumps(["a list"]), '["a list"]', None),
])
async def test_a_409_carries_the_stores_reason(store, body, message, code):
    store({T: httpx.Response(409, content=body.encode())})
    with pytest.raises(TemplateStoreError) as exc:
        await _client().get_template(template_id="T1")
    assert str(exc.value) == message and exc.value.code == code


@pytest.mark.asyncio
async def test_other_failures_are_template_store_errors(store):
    store({T: httpx.Response(500, text="oops")})
    with pytest.raises(TemplateStoreError, match="Failed to get template: 500 - oops"):
        await _client().get_template(template_id="T1")
    # The stand-in refuses the connection for any path under /boom.
    client = _client()
    client.base_url = "http://ts/boom#"
    with pytest.raises(TemplateStoreError, match="Request failed"):
        await client.get_template(template_value="x")
