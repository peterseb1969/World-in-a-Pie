"""lookup_entry_namespaces — bulk entry_id -> namespace fetch (CASE-609).

Isolation enforcement needs each resolved reference's owning namespace;
this pins the request shape, the found/not_found filtering, and the
empty-input short-circuit.
"""

import pytest

from wip_auth.registry_client import RegistryClientBase

BASE = "http://registry.test:8001"


@pytest.mark.asyncio
async def test_empty_input_short_circuits_without_http():
    client = RegistryClientBase(base_url=BASE, api_key="k")
    assert await client.lookup_entry_namespaces([]) == {}


@pytest.mark.asyncio
async def test_found_entries_map_to_namespaces(httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE}/api/registry/entries/lookup/by-id",
        json={
            "results": [
                {"input_index": 0, "status": "found", "entry_id": "id-a", "namespace": "ns-a"},
                {"input_index": 1, "status": "not_found"},
                {"input_index": 2, "status": "found", "entry_id": "id-c", "namespace": "ns-c"},
            ]
        },
    )
    client = RegistryClientBase(base_url=BASE, api_key="k")
    result = await client.lookup_entry_namespaces(["id-a", "id-b", "id-c"])
    assert result == {"id-a": "ns-a", "id-c": "ns-c"}

    import json
    request = httpx_mock.get_requests()[0]
    payload = json.loads(request.content)
    assert payload == [{"entry_id": "id-a"}, {"entry_id": "id-b"}, {"entry_id": "id-c"}]


@pytest.mark.asyncio
async def test_non_200_returns_empty(httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE}/api/registry/entries/lookup/by-id",
        status_code=503,
    )
    client = RegistryClientBase(base_url=BASE, api_key="k")
    assert await client.lookup_entry_namespaces(["id-a"]) == {}
