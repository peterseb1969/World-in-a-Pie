"""A runtime API key's expiry: one already past is refused at create and
update, and an update can remove an expiry by sending it as null.

Provenance: CASE-1302. Create stored an expires_at already in the past and
returned the plaintext of a key that never authenticated; an update with a
past date expired a working key at once; and an expiry, once set, could not
be removed (update ignored null). Only expires_at clears on null:
namespaces null would mean unrestricted.
"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from registry.models.api_key import StoredAPIKey
from registry.models.grant import expiry_passed

BASE = "/api/registry/api-keys"
PAST = (datetime.now(UTC) - timedelta(days=1)).isoformat()
NAIVE_PAST = (datetime.now(UTC) - timedelta(minutes=5)).replace(tzinfo=None).isoformat()
FUTURE = (datetime.now(UTC) + timedelta(days=30)).isoformat()


async def _create(client: AsyncClient, headers: dict, name: str, **extra) -> dict:
    await StoredAPIKey.find(StoredAPIKey.name == name).delete()
    response = await client.post(BASE, json={"name": name, **extra}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def _stored(name: str) -> StoredAPIKey:
    doc = await StoredAPIKey.find_one(StoredAPIKey.name == name)
    assert doc is not None
    return doc


@pytest.mark.asyncio
@pytest.mark.parametrize("expires_at", [PAST, NAIVE_PAST], ids=["aware", "naive"])
async def test_a_key_created_already_expired_is_refused(client: AsyncClient, auth_headers: dict, expires_at: str):
    name = "c1302-create-past"
    await StoredAPIKey.find(StoredAPIKey.name == name).delete()
    response = await client.post(BASE, json={"name": name, "expires_at": expires_at}, headers=auth_headers)
    assert response.status_code == 422, response.text
    assert "already passed" in response.json()["detail"]
    assert await StoredAPIKey.find_one(StoredAPIKey.name == name) is None


@pytest.mark.asyncio
async def test_a_future_expiry_is_still_accepted(client: AsyncClient, auth_headers: dict):
    created = await _create(client, auth_headers, "c1302-create-future", expires_at=FUTURE)
    assert created["expires_at"] is not None


@pytest.mark.asyncio
async def test_renewing_with_a_past_date_leaves_the_key_as_it_was(client: AsyncClient, auth_headers: dict):
    name = "c1302-renew-past"
    await _create(client, auth_headers, name, expires_at=FUTURE, description="before")
    response = await client.patch(
        f"{BASE}/{name}", json={"expires_at": PAST, "description": "after"}, headers=auth_headers,
    )
    assert response.status_code == 422, response.text
    doc = await _stored(name)
    assert doc.description == "before" and not expiry_passed(doc.expires_at)


@pytest.mark.asyncio
async def test_an_expiry_sent_as_null_is_removed(client: AsyncClient, auth_headers: dict):
    name = "c1302-clear"
    await _create(client, auth_headers, name, expires_at=FUTURE)
    response = await client.patch(f"{BASE}/{name}", json={"expires_at": None}, headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()["expires_at"] is None
    assert (await _stored(name)).expires_at is None


@pytest.mark.asyncio
async def test_an_expiry_left_out_is_unchanged(client: AsyncClient, auth_headers: dict):
    name = "c1302-unchanged"
    await _create(client, auth_headers, name, expires_at=FUTURE)
    response = await client.patch(f"{BASE}/{name}", json={"description": "x"}, headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()["expires_at"] is not None
    assert (await _stored(name)).expires_at is not None


@pytest.mark.asyncio
async def test_namespaces_null_does_not_clear_the_scope(client: AsyncClient, auth_headers: dict):
    # The null-clears rule is expires_at's alone: namespaces null would make
    # a scoped key unrestricted.
    name = "c1302-scope"
    await _create(client, auth_headers, name, namespaces=["wip"])
    response = await client.patch(f"{BASE}/{name}", json={"namespaces": None}, headers=auth_headers)
    assert response.status_code == 200, response.text
    assert (await _stored(name)).namespaces == ["wip"]
