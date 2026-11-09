"""A lookup never makes the Registry fetch a URL taken from stored entry data.

An entry's source_info.endpoint_url is plain stored data: a client may follow
it with its own credentials. The Registry does not request it on a reader's
behalf — doing so let anyone with read on a namespace make the Registry, from
inside the platform network, GET whatever URL a writer stored and return the
answer (or an error text telling reachable hosts from unreachable ones).
`fetch_source_data` is therefore not a lookup option: both lookup routes
refuse it (422, the request models forbid unknown fields), and a lookup
without it returns the entry, endpoint_url included, and no source_data.
"""

import uuid
from typing import ClassVar

import pytest

from registry.api import entries as entries_api


class _SpyClient:
    """Stands in for httpx.AsyncClient in the entries module: records every
    outbound GET the lookup routes make."""

    asked: ClassVar[list[str]] = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        _SpyClient.asked.append(url)

        class _R:
            def raise_for_status(self):
                return None

            def json(self):
                return {"internal": "answer"}

        return _R()


@pytest.fixture
def spy(monkeypatch):
    _SpyClient.asked = []
    if hasattr(entries_api, "httpx"):
        monkeypatch.setattr(entries_api.httpx, "AsyncClient", _SpyClient)
    return _SpyClient


async def _register(client, headers, tag: str) -> str:
    r = await client.post("/api/registry/entries/register", headers=headers, json=[{
        "namespace": "default", "entity_type": "terms", "composite_key": {"k": tag},
        "source_info": {"system_id": "src", "endpoint_url": "http://wip-def-store:8002/health"},
    }])
    assert r.status_code == 200, r.text
    return r.json()["results"][0]["registry_id"]


@pytest.mark.asyncio
async def test_lookup_by_id_refuses_fetch_source_data(client, auth_headers, spy):
    entry_id = await _register(client, auth_headers, uuid.uuid4().hex[:8])

    r = await client.post("/api/registry/entries/lookup/by-id", headers=auth_headers,
                          json=[{"entry_id": entry_id, "fetch_source_data": True}])

    assert r.status_code == 422, r.text
    assert spy.asked == []


@pytest.mark.asyncio
async def test_lookup_by_key_refuses_fetch_source_data(client, auth_headers, spy):
    tag = uuid.uuid4().hex[:8]
    await _register(client, auth_headers, tag)

    r = await client.post("/api/registry/entries/lookup/by-key", headers=auth_headers, json=[{
        "namespace": "default", "entity_type": "terms", "composite_key": {"k": tag},
        "fetch_source_data": True,
    }])

    assert r.status_code == 422, r.text
    assert spy.asked == []


@pytest.mark.asyncio
async def test_lookup_returns_endpoint_url_as_data_and_fetches_nothing(client, auth_headers, spy):
    tag = uuid.uuid4().hex[:8]
    entry_id = await _register(client, auth_headers, tag)

    by_id = (await client.post("/api/registry/entries/lookup/by-id", headers=auth_headers,
                               json=[{"entry_id": entry_id}])).json()["results"][0]
    by_key = (await client.post("/api/registry/entries/lookup/by-key", headers=auth_headers, json=[{
        "namespace": "default", "entity_type": "terms", "composite_key": {"k": tag},
    }])).json()["results"][0]

    for result in (by_id, by_key):
        assert result["status"] == "found", result
        assert result["source_info"]["endpoint_url"] == "http://wip-def-store:8002/health"
        assert "source_data" not in result
    assert spy.asked == []
