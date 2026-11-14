"""A lookup never makes the Registry fetch a URL taken from stored entry data.

An entry's source_info.endpoint_url is plain stored data: a client may follow
it with its own credentials. The Registry does not request it on a reader's
behalf — doing so let anyone with read on a namespace make the Registry, from
inside the platform network, GET whatever URL a writer stored and return the
answer (or an error text telling reachable hosts from unreachable ones).
`fetch_source_data` is therefore not a lookup option: both lookup routes
refuse it (422, the request models forbid unknown fields), and a lookup
without it returns the entry, endpoint_url included, and no source_data.
The spy asserts the entries module holds no HTTP client and records any
client created during a request (CASE-1266, 06668a40 R2: the former spy
patched a module attribute that does not exist, so it could see nothing).
"""

import uuid

import httpx
import pytest

from registry.api import entries as entries_api


@pytest.fixture
def spy(monkeypatch):
    """Records every httpx client created while a test runs.

    The entries module imports no HTTP client at all (asserted here: the
    Registry has nothing to call an endpoint_url with), and any client made
    anywhere during the request is recorded by name of its constructor. The
    test's own client exists before the fixture patches, so it is not
    counted."""
    assert not hasattr(entries_api, "httpx")
    created: list[tuple] = []
    original = httpx.AsyncClient.__init__

    def recording_init(self, *args, **kwargs):
        created.append((args, kwargs))
        original(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", recording_init)

    class _Spy:
        asked = created

    return _Spy


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
