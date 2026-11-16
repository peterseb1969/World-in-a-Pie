"""The candidate lookup reads the caller's namespaces once, and checks a lock again.

POST /entries/lookup/candidates read the caller's readable namespaces once
per item, unbounded by the batch size; it is read once per request now. And
a namespace locked after that list was read still had its entries returned
and counted, where the sibling lookups check each match's namespace at
decision time; the lookup now asks again for the namespaces its matches come
from before it counts them.
"""

import uuid

import pytest

import registry.api.grants as grants

from .test_case_1226_lookup_candidates_locked import CANDIDATES, _lock, _namespace, _register


@pytest.mark.asyncio
async def test_the_readable_namespaces_are_read_once_per_request(client, auth_headers, monkeypatch):
    real = grants._unlocked_namespaces
    calls = []

    async def counted():
        calls.append(1)
        return await real()

    monkeypatch.setattr(grants, "_unlocked_namespaces", counted)
    items = [{"value": f"v-{uuid.uuid4().hex[:8]}"} for _ in range(25)]
    r = await client.post(CANDIDATES, json=items, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_namespace_locked_after_the_list_was_read_is_not_counted(
    client, auth_headers, monkeypatch,
):
    open_ns, locked_ns = await _namespace(client, auth_headers), await _namespace(client, auth_headers)
    value = f"v1226r-{uuid.uuid4().hex[:8]}"
    open_id = await _register(client, auth_headers, open_ns, value)
    locked_id = await _register(client, auth_headers, locked_ns, value)
    # The list was read before the lock: it still names the locked namespace.
    stale = await grants._unlocked_namespaces()

    async def read_before_the_lock():
        return stale

    monkeypatch.setattr(grants, "_unlocked_namespaces", read_before_the_lock)
    await _lock(locked_ns)

    r = await client.post(CANDIDATES, json=[{"value": value}, {"value": locked_id}], headers=auth_headers)
    assert r.status_code == 200, r.text
    by_value, by_id = sorted(r.json()["results"], key=lambda x: x["index"])
    assert by_value["total"] == 1
    assert [c["entry_id"] for c in by_value["candidates"]] == [open_id]
    assert by_id["status"] == "not_found"


@pytest.mark.asyncio
async def test_an_administrators_namespaces_are_the_live_ones(client, auth_headers):
    """Active and archived count; locked (deletion in progress) and the legacy
    ``status: "deleted"`` record do not, as in every other enumeration."""
    from registry.models.namespace import Namespace

    active, archived, locked, deleted = [await _namespace(client, auth_headers) for _ in range(4)]
    coll = Namespace.get_motor_collection()
    await coll.update_one({"prefix": archived}, {"$set": {"status": "archived"}})
    await _lock(locked)
    await coll.update_one({"prefix": deleted}, {"$set": {"status": "deleted"}})
    listed = set(await grants._unlocked_namespaces())
    assert {active, archived} <= listed
    assert not {locked, deleted} & listed


@pytest.mark.asyncio
async def test_an_item_naming_its_namespace_is_not_asked_again(
    client, auth_headers, monkeypatch,
):
    """CASE-1334 (review of 6126de3b, R3): an item that names its namespace
    has had read on it decided already, and allows() repeats that answer for
    the rest of the request, so the late re-check of the matches' namespaces
    could drop nothing and cost a query per item. It runs only for items
    scoped to the readable list."""
    import registry.api.entries as entries

    ns = await _namespace(client, auth_headers)
    value = f"v1334-{uuid.uuid4().hex[:8]}"
    entry_id = await _register(client, auth_headers, ns, value)
    calls: list[dict] = []
    real = entries._drop_unreadable

    async def counted(by_value, access):
        calls.append(by_value)
        await real(by_value, access)

    monkeypatch.setattr(entries, "_drop_unreadable", counted)

    named = [{"value": value, "namespace": ns} for _ in range(5)]
    r = await client.post(CANDIDATES, json=[*named, {"value": value}], headers=auth_headers)

    assert r.status_code == 200, r.text
    assert len(calls) == 1  # only the item without a namespace
    results = sorted(r.json()["results"], key=lambda x: x["index"])
    assert all(x["status"] == "found" and x["total"] == 1 for x in results)
    assert all(x["candidates"][0]["entry_id"] == entry_id for x in results)
