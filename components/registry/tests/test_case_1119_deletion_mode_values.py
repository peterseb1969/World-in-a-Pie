"""deletion_mode takes exactly "retain" or "full" on every namespace write (CASE-1119).

The request models typed deletion_mode as a free string, so "FULL", "Full"
or " full" slipped past the guards that compare the exact value — the "wip"
refusal and the confirm_enable_deletion step for retain → full. An upsert of
an existing namespace then stored the value, which the stored model refuses
on read, so every later read of that namespace, and every listing, failed.
These tests pin the 422 on create, upsert-create and upsert-update, and that
the stored namespace keeps its mode and stays readable.
"""

import pytest
from httpx import AsyncClient

from registry.models.namespace import Namespace

NON_CANONICAL = [
    pytest.param("FULL", id="upper"),
    pytest.param("Full", id="title"),
    pytest.param(" full", id="leading-space"),
    pytest.param("full ", id="trailing-space"),
    pytest.param("RETAIN", id="upper-retain"),
    pytest.param("delete", id="unknown"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", NON_CANONICAL)
async def test_create_refuses_a_non_canonical_mode(client: AsyncClient, auth_headers: dict, mode) -> None:
    resp = await client.post(
        "/api/registry/namespaces",
        json={"prefix": "dm-create", "deletion_mode": mode},
        headers=auth_headers,
    )
    assert resp.status_code == 422, resp.text
    assert await Namespace.find_one({"prefix": "dm-create"}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", NON_CANONICAL)
async def test_upsert_create_refuses_a_non_canonical_mode(client: AsyncClient, auth_headers: dict, mode) -> None:
    resp = await client.put(
        "/api/registry/namespaces/dm-upcreate",
        json={"deletion_mode": mode},
        headers=auth_headers,
    )
    assert resp.status_code == 422, resp.text
    assert await Namespace.find_one({"prefix": "dm-upcreate"}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", NON_CANONICAL)
async def test_upsert_update_refuses_and_keeps_the_namespace_readable(
    client: AsyncClient, auth_headers: dict, mode,
) -> None:
    created = await client.put(
        "/api/registry/namespaces/dm-update", json={"description": "x"}, headers=auth_headers,
    )
    assert created.status_code == 200, created.text

    resp = await client.put(
        "/api/registry/namespaces/dm-update",
        json={"deletion_mode": mode, "confirm_enable_deletion": True},
        headers=auth_headers,
    )

    assert resp.status_code == 422, resp.text
    read = await client.get("/api/registry/namespaces/dm-update", headers=auth_headers)
    assert read.status_code == 200, read.text
    assert read.json()["deletion_mode"] == "retain"
    listing = await client.get("/api/registry/namespaces", headers=auth_headers)
    assert listing.status_code == 200, listing.text


@pytest.mark.asyncio
async def test_the_wip_namespace_refuses_a_spelling_of_full(client: AsyncClient, auth_headers: dict) -> None:
    await client.put("/api/registry/namespaces/wip", json={"description": "wip"}, headers=auth_headers)

    resp = await client.put(
        "/api/registry/namespaces/wip", json={"deletion_mode": "FULL"}, headers=auth_headers,
    )

    assert resp.status_code == 422, resp.text
    read = await client.get("/api/registry/namespaces/wip", headers=auth_headers)
    assert read.status_code == 200 and read.json()["deletion_mode"] == "retain"


@pytest.mark.asyncio
async def test_the_canonical_flip_still_needs_its_confirmation(client: AsyncClient, auth_headers: dict) -> None:
    await client.put("/api/registry/namespaces/dm-flip", json={"description": "x"}, headers=auth_headers)

    unconfirmed = await client.put(
        "/api/registry/namespaces/dm-flip", json={"deletion_mode": "full"}, headers=auth_headers,
    )
    confirmed = await client.put(
        "/api/registry/namespaces/dm-flip",
        json={"deletion_mode": "full", "confirm_enable_deletion": True},
        headers=auth_headers,
    )

    assert unconfirmed.status_code == 400, unconfirmed.text
    assert confirmed.status_code == 200 and confirmed.json()["deletion_mode"] == "full"
