"""deactivate_entries / reactivate_entries — retirement and revival reach the
Registry, batched, with the per-item results handed back to the caller.

A non-200 raises: a store must know its retirement did not reach the
Registry, because then the retired entity's synonyms are still claimed.
"""

import json

import pytest

from wip_auth import registry_client
from wip_auth.registry_client import RegistryClientBase, RegistryError

BASE = "http://registry.test:8001"


def _client() -> RegistryClientBase:
    return RegistryClientBase(base_url=BASE, api_key="k")


@pytest.mark.asyncio
async def test_deactivate_returns_the_per_item_results(httpx_mock):
    item = {"index": 0, "status": "deactivated", "registry_id": "e-1", "released": [{"a": 1}]}
    httpx_mock.add_response(
        url=f"{BASE}/api/registry/entries/deactivate", method="POST", json={"results": [item]},
    )
    assert await _client().deactivate_entries(["e-1"], updated_by="me") == [item]
    (sent,) = json.loads(httpx_mock.get_requests()[0].content)
    assert sent == {"entry_id": "e-1", "updated_by": "me"}


@pytest.mark.asyncio
async def test_dry_run_is_a_query_parameter(httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE}/api/registry/entries/deactivate?dry_run=true", method="POST",
        json={"results": []},
    )
    await _client().deactivate_entries(["e-1"], dry_run=True)


@pytest.mark.asyncio
async def test_reactivate_posts_to_its_own_route(httpx_mock):
    item = {"index": 0, "status": "reactivated", "warnings": ["held by e-2"]}
    httpx_mock.add_response(
        url=f"{BASE}/api/registry/entries/reactivate", method="POST", json={"results": [item]},
    )
    assert await _client().reactivate_entries(["e-1"]) == [item]


@pytest.mark.asyncio
async def test_large_requests_are_batched(httpx_mock, monkeypatch):
    monkeypatch.setattr(registry_client, "_LIFECYCLE_BATCH", 2)
    for _ in range(3):
        httpx_mock.add_response(
            url=f"{BASE}/api/registry/entries/deactivate", method="POST",
            json={"results": [{"status": "unchanged"}]},
        )
    results = await _client().deactivate_entries(["a", "b", "c", "d", "e"])
    assert len(results) == 3
    sizes = [len(json.loads(r.content)) for r in httpx_mock.get_requests()]
    assert sizes == [2, 2, 1]


@pytest.mark.asyncio
async def test_non_200_raises(httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE}/api/registry/entries/deactivate", method="POST", status_code=503,
    )
    with pytest.raises(RegistryError, match="deactivate"):
        await _client().deactivate_entries(["e-1"])
