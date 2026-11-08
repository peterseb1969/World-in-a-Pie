"""A namespace's created_by / updated_by name the authenticated caller.

Every namespace write route took a free-text "who" from the caller (a body
field or a query parameter) and stored it as the actor, so the audit fields
named whoever the caller chose. They now record the authenticated identity;
the free text is kept beside it as ``actor_note``.
"""

import uuid

import pytest

from registry.models.namespace import Namespace

BASE = "/api/registry/namespaces"
SPOOF = "someone-else@example.com"


def _p() -> str:
    return f"c1185-{uuid.uuid4().hex[:8]}"


async def _stored(prefix: str) -> Namespace:
    ns = await Namespace.find_one({"prefix": prefix})
    assert ns is not None
    return ns


def _authentic(value: str | None) -> bool:
    return bool(value) and value != SPOOF


@pytest.mark.asyncio
async def test_create_records_the_caller(client, auth_headers):
    prefix = _p()
    r = await client.post(BASE, headers=auth_headers, json={"prefix": prefix, "created_by": SPOOF})
    assert r.status_code in (200, 201), r.text
    ns = await _stored(prefix)
    assert _authentic(ns.created_by), ns.created_by
    assert ns.actor_note == SPOOF
    assert r.json()["created_by"] == ns.created_by and r.json()["actor_note"] == SPOOF


@pytest.mark.asyncio
async def test_upsert_create_and_update_record_the_caller(client, auth_headers):
    prefix = _p()
    r = await client.put(f"{BASE}/{prefix}", headers=auth_headers, json={"updated_by": SPOOF})
    assert r.status_code == 200, r.text
    ns = await _stored(prefix)
    assert _authentic(ns.created_by) and ns.actor_note == SPOOF

    r = await client.put(f"{BASE}/{prefix}", headers=auth_headers,
                         json={"description": "changed", "updated_by": "a note"})
    assert r.status_code == 200, r.text
    ns = await _stored(prefix)
    assert _authentic(ns.updated_by) and ns.updated_by != "a note"
    assert ns.actor_note == "a note"


@pytest.mark.asyncio
@pytest.mark.parametrize("verb,param", [("archive", "archived_by"), ("restore", "restored_by")])
async def test_archive_and_restore_record_the_caller(client, auth_headers, verb, param):
    prefix = _p()
    assert (await client.put(f"{BASE}/{prefix}", headers=auth_headers, json={})).status_code == 200
    if verb == "restore":
        assert (await client.post(f"{BASE}/{prefix}/archive", headers=auth_headers)).status_code == 200
    r = await client.post(f"{BASE}/{prefix}/{verb}", headers=auth_headers, params={param: SPOOF})
    assert r.status_code == 200, r.text
    ns = await _stored(prefix)
    assert _authentic(ns.updated_by), ns.updated_by
    assert ns.actor_note == SPOOF


@pytest.mark.asyncio
async def test_a_deletion_mode_change_records_the_caller(client, auth_headers):
    prefix = _p()
    assert (await client.put(f"{BASE}/{prefix}", headers=auth_headers, json={})).status_code == 200
    r = await client.patch(f"{BASE}/{prefix}", headers=auth_headers, params={
        "deletion_mode": "full", "confirm_enable_deletion": "true", "updated_by": SPOOF,
    })
    assert r.status_code == 200, r.text
    ns = await _stored(prefix)
    assert _authentic(ns.updated_by), ns.updated_by
    assert ns.actor_note == SPOOF


@pytest.mark.asyncio
async def test_a_write_without_a_note_clears_the_previous_one(client, auth_headers):
    """The note belongs to the write that recorded the actor beside it."""
    prefix = _p()
    await client.put(f"{BASE}/{prefix}", headers=auth_headers, json={"updated_by": "first"})
    await client.put(f"{BASE}/{prefix}", headers=auth_headers, json={"description": "x"})
    assert (await _stored(prefix)).actor_note is None
