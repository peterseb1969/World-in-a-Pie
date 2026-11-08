"""Every key and grant write announces itself, so other services re-sync
their keys and drop their cached grants at once instead of at their next poll
(30 s) and their cache's TTL."""

import uuid

import pytest

from registry.api import api_keys as api_keys_api
from registry.api import grants as grants_api

BASE = "/api/registry"


@pytest.fixture
def announced(monkeypatch):
    calls: list[bool] = []

    async def record(keys: bool = True) -> None:
        calls.append(keys)

    monkeypatch.setattr(api_keys_api, "access_changed", record)
    monkeypatch.setattr(grants_api, "access_changed", record)
    return calls


@pytest.mark.asyncio
async def test_key_writes_announce_a_key_change(client, auth_headers, announced):
    name = f"c1163-{uuid.uuid4().hex[:8]}"
    r = await client.post(f"{BASE}/api-keys", headers=auth_headers, json={
        "name": name, "groups": [], "namespaces": ["vendor1"],
    })
    assert r.status_code == 201, r.text
    assert announced == [True]

    assert (await client.patch(f"{BASE}/api-keys/{name}", headers=auth_headers,
                               json={"description": "x"})).status_code == 200
    assert (await client.put(f"{BASE}/api-keys/{name}/namespaces/vendor2", headers=auth_headers,
                             json={})).status_code == 200
    assert (await client.delete(f"{BASE}/api-keys/{name}/namespaces/vendor2",
                                headers=auth_headers)).status_code == 200
    assert (await client.delete(f"{BASE}/api-keys/{name}", headers=auth_headers)).status_code == 200
    assert announced == [True] * 5


@pytest.mark.asyncio
async def test_grant_writes_announce_a_grant_change(client, auth_headers, announced):
    subject = f"c1163-{uuid.uuid4().hex[:8]}"
    r = await client.post(f"{BASE}/namespaces/vendor1/grants", headers=auth_headers, json=[
        {"subject": subject, "subject_type": "api_key", "permission": "read"},
    ])
    assert r.status_code == 200, r.text
    r = await client.request("DELETE", f"{BASE}/namespaces/vendor1/grants", headers=auth_headers,
                             json=[{"subject": subject, "subject_type": "api_key"}])
    assert r.status_code == 200, r.text
    assert announced == [False, False]

    # A revoke that found nothing changed nothing, and says nothing.
    await client.request("DELETE", f"{BASE}/namespaces/vendor1/grants", headers=auth_headers,
                         json=[{"subject": subject, "subject_type": "api_key"}])
    assert announced == [False, False]
