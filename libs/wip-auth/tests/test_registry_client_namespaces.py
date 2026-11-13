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
                {"index": 0, "status": "found", "entry_id": "id-a", "namespace": "ns-a"},
                {"index": 1, "status": "not_found"},
                {"index": 2, "status": "found", "entry_id": "id-c", "namespace": "ns-c"},
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


# place_entries — an entry's namespace whatever its status. The bulk lookup
# sees active entries only; a merged-away or deactivated target still lives
# in a namespace, and a caller deciding who may see it needs that namespace.


@pytest.mark.asyncio
async def test_place_entries_reads_entries_the_bulk_lookup_misses(httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE}/api/registry/entries/lookup/by-id",
        json={"results": [
            {"index": 0, "status": "found", "entry_id": "id-a", "namespace": "ns-a"},
            {"index": 1, "status": "not_found"},
            {"index": 2, "status": "not_found"},
        ]},
    )
    httpx_mock.add_response(
        url=f"{BASE}/api/registry/entries/id-merged",
        json={"entry_id": "id-merged", "namespace": "ns-c", "status": "inactive"},
    )
    httpx_mock.add_response(url=f"{BASE}/api/registry/entries/id-gone", status_code=404)
    client = RegistryClientBase(base_url=BASE, api_key="k")
    placed = await client.place_entries(["id-a", "id-merged", "id-gone"])
    assert placed == {"id-a": "ns-a", "id-merged": "ns-c"}


@pytest.mark.asyncio
async def test_place_entries_refuses_when_the_registry_cannot_answer(httpx_mock):
    from wip_auth.registry_client import RegistryError

    httpx_mock.add_response(
        url=f"{BASE}/api/registry/entries/lookup/by-id",
        json={"results": [{"index": 0, "status": "not_found"}]},
    )
    httpx_mock.add_response(url=f"{BASE}/api/registry/entries/id-x", status_code=503)
    client = RegistryClientBase(base_url=BASE, api_key="k")
    with pytest.raises(RegistryError):
        await client.place_entries(["id-x"])


@pytest.mark.asyncio
async def test_place_entries_refuses_when_the_bulk_lookup_fails(httpx_mock):
    from wip_auth.registry_client import RegistryError

    httpx_mock.add_response(
        url=f"{BASE}/api/registry/entries/lookup/by-id", status_code=503,
    )
    client = RegistryClientBase(base_url=BASE, api_key="k")
    with pytest.raises(RegistryError):
        await client.place_entries(["id-x"])


def _answer(httpx_mock, *results):
    httpx_mock.add_response(
        url=f"{BASE}/api/registry/entries/lookup/by-id", json={"results": list(results)},
    )


@pytest.mark.asyncio
async def test_an_item_the_registry_failed_on_is_an_outage_when_asked_to_raise(httpx_mock):
    """A 200 whose results hold an `error` item answered the request but not
    that id: a caller that must not pass an unplaced target as "nowhere"
    gets an error naming the id, not a map without it."""
    from wip_auth.registry_client import RegistryError

    _answer(
        httpx_mock,
        {"index": 0, "status": "found", "entry_id": "id-a", "namespace": "ns-a"},
        {"index": 1, "status": "error", "error_code": "internal_error", "error": "boom"},
    )
    client = RegistryClientBase(base_url=BASE, api_key="k")
    with pytest.raises(RegistryError) as failed:
        await client.lookup_entry_namespaces(["id-a", "id-b"], raise_on_error=True)
    assert "id-b" in str(failed.value) and "internal_error" in str(failed.value)


@pytest.mark.asyncio
async def test_ambiguous_and_not_found_items_are_answers_not_outages(httpx_mock):
    _answer(
        httpx_mock,
        {"index": 0, "status": "ambiguous", "error_code": "ambiguous"},
        {"index": 1, "status": "not_found"},
    )
    client = RegistryClientBase(base_url=BASE, api_key="k")
    assert await client.lookup_entry_namespaces(["id-a", "id-b"], raise_on_error=True) == {}


@pytest.mark.asyncio
async def test_without_raise_an_error_item_is_left_out_as_before(httpx_mock):
    _answer(httpx_mock, {"index": 0, "status": "error", "error_code": "internal_error"})
    client = RegistryClientBase(base_url=BASE, api_key="k")
    assert await client.lookup_entry_namespaces(["id-a"]) == {}
