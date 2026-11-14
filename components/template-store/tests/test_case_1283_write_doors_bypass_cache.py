"""Template update and delete resolve the template they change past the cache.

Provenance: CASE-1283 (review of 77d70c7b). Writes must not be decided by
the 30 s read cache: within its TTL after a value's template was deleted and
created again, or merged, the cached id selects a dead or retired template.
PUT and DELETE /templates resolved their targets through the cache; every
other bulk write door already asked the Registry.
"""

import pytest
from httpx import AsyncClient

import template_store.api.templates as templates_api

API = "/api/template-store/templates"


@pytest.fixture
def resolutions(monkeypatch):
    seen: list[bool] = []
    real = templates_api.resolve_bulk_ids

    async def spy(*args, bypass_cache=False, **kwargs):
        seen.append(bypass_cache)
        return await real(*args, bypass_cache=bypass_cache, **kwargs)

    monkeypatch.setattr(templates_api, "resolve_bulk_ids", spy)
    return seen


@pytest.mark.asyncio
@pytest.mark.parametrize("method,body", [
    ("PUT", [{"template_id": "NO_SUCH_1283", "label": "x"}]),
    ("DELETE", [{"id": "NO_SUCH_1283"}]),
])
async def test_template_write_doors_bypass_the_cache(
    client: AsyncClient, auth_headers: dict, resolutions, method, body,
):
    resp = await client.request(method, API, json=body, headers=auth_headers,
                                params={"namespace": "wip"})
    assert resp.status_code == 200, resp.text
    assert resolutions == [True]
