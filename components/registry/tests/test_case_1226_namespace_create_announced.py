"""Creating a namespace announces an access change.

Provenance: CASE-1226. An administrator now reads a listed set of
namespaces (every one but a locked one) instead of "no filter", and services
cache that list for 30 s. Without an announcement a namespace created a
moment ago stayed out of an administrator's unscoped listings until the
cache expired.
"""

import uuid

import pytest

from registry.api import namespaces as namespaces_api

BASE = "/api/registry"


@pytest.fixture
def announced(monkeypatch):
    calls: list[bool] = []

    async def record(keys: bool = True) -> None:
        calls.append(keys)

    monkeypatch.setattr(namespaces_api, "access_changed", record)
    return calls


@pytest.mark.asyncio
async def test_a_created_namespace_is_announced(client, auth_headers, announced):
    prefix = f"c1226-{uuid.uuid4().hex[:8]}"
    r = await client.put(f"{BASE}/namespaces/{prefix}", headers=auth_headers, json={})
    assert r.status_code == 200, r.text
    assert announced == [False]  # grants and lists, not keys


@pytest.mark.asyncio
async def test_an_existing_namespace_upserted_again_is_not_announced(client, auth_headers, announced):
    prefix = f"c1226-{uuid.uuid4().hex[:8]}"
    assert (await client.put(f"{BASE}/namespaces/{prefix}", headers=auth_headers, json={})).status_code == 200
    announced.clear()
    r = await client.put(f"{BASE}/namespaces/{prefix}", headers=auth_headers, json={})
    assert r.status_code == 200, r.text
    assert announced == []
