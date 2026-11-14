"""Terminology update and delete resolve the entity they change past the cache.

Provenance: CASE-1283 (review of 77d70c7b). Writes must not be decided by
the 30 s read cache: within its TTL after a value's entity was deleted and
created again, or merged, the cached id selects a dead or retired entity.
PUT and DELETE /terminologies resolved their targets through the cache;
every other bulk write door already asked the Registry.
"""

import pytest
from httpx import AsyncClient

import def_store.api.terminologies as terminologies_api

API = "/api/def-store/terminologies"


@pytest.fixture
def resolutions(monkeypatch):
    seen: list[bool] = []
    real = terminologies_api.resolve_bulk_ids

    async def spy(*args, bypass_cache=False, **kwargs):
        seen.append(bypass_cache)
        return await real(*args, bypass_cache=bypass_cache, **kwargs)

    monkeypatch.setattr(terminologies_api, "resolve_bulk_ids", spy)
    return seen


@pytest.mark.asyncio
@pytest.mark.parametrize("method,body", [
    ("PUT", [{"terminology_id": "NO_SUCH_1283", "label": "x"}]),
    ("DELETE", [{"id": "NO_SUCH_1283"}]),
])
async def test_terminology_write_doors_bypass_the_cache(
    client: AsyncClient, auth_headers: dict, resolutions, method, body,
):
    resp = await client.request(method, API, json=body, headers=auth_headers,
                                params={"namespace": "wip"})
    assert resp.status_code == 200, resp.text
    assert resolutions == [True]
