"""Keyless activation's failure handling, pinned before activate_entries'
refactor.

Provenance: CASE-1161, CASE-1268. The suite did not reach two of these
branches of the keyless fast path: the classify read failing, and the update
modifying fewer entries than were reserved (a concurrent change), which a
re-read sorts out. The guarded update failing was already pinned by
test_case_1132_registry_lows; the test here adds the post-state. Each is
answered per item; a keyed item in the same request is answered by its own
path regardless. A re-read that fails is retried once; if it fails again the
write's items are errors that say the write landed, never a failed write. A
repeated entry_id is answered as the keyed path answers it: the first
activates, the rest find it active.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from registry.models.entry import RegistryEntry
from tests.test_case_1161_activate_keyed import _activate, _break_collection, _reserved, _status
from tests.test_reservation_lifecycle import _provision

CLASSIFY = {"entry_id": 1, "status": 1, "_id": 0}


async def _keyless(client, headers, n: int) -> list[dict]:
    ids = await _provision(client, headers, count=n, defer_claim=False)
    return [{"entry_id": eid} for eid in ids]


def _summary(body: dict) -> list[tuple[str, str | None]]:
    return [(r["status"], r.get("error_code")) for r in body["results"]]


@pytest.mark.asyncio
async def test_a_failed_classify_read_fails_the_keyless_items_only(client: AsyncClient, auth_headers, monkeypatch):
    keyed = await _reserved(client, auth_headers, 1, "kl-read")
    keyless = await _keyless(client, auth_headers, 2)
    real = RegistryEntry.get_motor_collection()

    keyless_ids = sorted(i["entry_id"] for i in keyless)

    def find(*a, **kw):
        # Only the keyless classify read: the keyed path reads the same
        # collection with its own queries (some by keyword).
        query = a[0] if a else kw.get("filter", {})
        projection = a[1] if len(a) > 1 else kw.get("projection")
        ids = (query.get("entry_id") or {}).get("$in") if isinstance(query, dict) else None
        if projection == CLASSIFY and isinstance(ids, list) and sorted(ids) == keyless_ids:
            raise RuntimeError("classify failed")
        return real.find(*a, **kw)

    _break_collection(monkeypatch, find=find)
    body = await _activate(client, auth_headers, [*keyed, *keyless])
    assert _summary(body)[0] == ("activated", None), body["results"][0]
    assert [r["status"] for r in body["results"][1:]] == ["error", "error"]
    assert "classify failed" in body["results"][1]["error"]
    assert (body["total"], body["activated"], body["errors"]) == (3, 1, 2)


@pytest.mark.asyncio
async def test_a_failed_update_fails_the_items_it_was_for(client: AsyncClient, auth_headers, monkeypatch):
    keyless = await _keyless(client, auth_headers, 2)
    async def update_many(*a, **kw):
        raise RuntimeError("update failed")

    _break_collection(monkeypatch, update_many=update_many)
    body = await _activate(client, auth_headers, [*keyless, {"entry_id": "missing-1161"}])
    assert [r["status"] for r in body["results"]] == ["error", "error", "not_found"]
    assert "update failed" in body["results"][0]["error"]
    assert (body["activated"], body["errors"]) == (0, 3)
    assert [await _status(i["entry_id"]) for i in keyless] == ["reserved", "reserved"]


@pytest.mark.asyncio
async def test_an_entry_the_update_missed_and_still_reserved_is_a_conflict(client: AsyncClient, auth_headers, monkeypatch):
    first, second = await _keyless(client, auth_headers, 2)
    real = RegistryEntry.get_motor_collection()

    async def update_many(flt: dict[str, Any], update, *a, **kw):
        # Another request "holds" the second entry: the guarded write leaves it.
        ids = [i for i in flt["entry_id"]["$in"] if i != second["entry_id"]]
        return await real.update_many({**flt, "entry_id": {"$in": ids}}, update, *a, **kw)

    _break_collection(monkeypatch, update_many=update_many)
    body = await _activate(client, auth_headers, [first, second, first])
    assert _summary(body) == [("activated", None), ("error", "concurrency_conflict"), ("already_active", None)]
    assert body["results"][1]["error"] == "Entry status changed concurrently; not activated"
    assert (body["activated"], body["errors"]) == (1, 1)
    assert [await _status(i["entry_id"]) for i in (first, second)] == ["active", "reserved"]


@pytest.mark.asyncio
async def test_a_repeated_entry_id_is_activated_once(client: AsyncClient, auth_headers):
    (item,) = await _keyless(client, auth_headers, 1)
    body = await _activate(client, auth_headers, [item, item])
    assert _summary(body) == [("activated", None), ("already_active", None)]
    assert (body["total"], body["activated"], body["errors"]) == (2, 1, 0)


def _short_update(real, held: str):
    """An update_many that leaves ``held`` alone, as if another request
    changed it between the classify read and the write."""
    async def update_many(flt: dict[str, Any], update, *a, **kw):
        ids = [i for i in flt["entry_id"]["$in"] if i != held]
        return await real.update_many({**flt, "entry_id": {"$in": ids}}, update, *a, **kw)
    return update_many


def _failing_rereads(real, times: int):
    """A find that fails the shortfall re-read ``times`` times."""
    failures = [0]

    def find(*a, **kw):
        query = a[0] if a else kw.get("filter", {})
        if isinstance(query, dict) and query.get("status") == "active" and failures[0] < times:
            failures[0] += 1
            raise RuntimeError("re-read failed")
        return real.find(*a, **kw)
    return find, failures


@pytest.mark.asyncio
async def test_a_failed_re_read_is_retried_once(client: AsyncClient, auth_headers, monkeypatch):
    first, second = await _keyless(client, auth_headers, 2)
    real = RegistryEntry.get_motor_collection()
    find, failures = _failing_rereads(real, 1)
    _break_collection(monkeypatch, update_many=_short_update(real, second["entry_id"]), find=find)
    body = await _activate(client, auth_headers, [first, second])
    assert failures == [1]
    assert _summary(body) == [("activated", None), ("error", "concurrency_conflict")]
    assert (body["activated"], body["errors"]) == (1, 1)


@pytest.mark.asyncio
async def test_a_re_read_that_fails_twice_says_the_write_landed(client: AsyncClient, auth_headers, monkeypatch):
    first, second = await _keyless(client, auth_headers, 2)
    real = RegistryEntry.get_motor_collection()
    find, failures = _failing_rereads(real, 2)
    _break_collection(monkeypatch, update_many=_short_update(real, second["entry_id"]), find=find)
    body = await _activate(client, auth_headers, [first, second])
    assert failures == [2]
    assert _summary(body) == [("error", "internal_error"), ("error", "internal_error")]
    error = body["results"][0]["error"]
    assert "wrote 1 of 2 entries" in error and "re-read failed" in error, error
    assert (body["activated"], body["errors"]) == (0, 2)
    # The write landed for the first; activating again tells the caller so.
    assert [await _status(i["entry_id"]) for i in (first, second)] == ["active", "reserved"]
    again = await _activate(client, auth_headers, [first])
    assert _summary(again) == [("already_active", None)]


@pytest.mark.asyncio
async def test_an_entry_another_request_activated_counts_as_activated(client: AsyncClient, auth_headers, monkeypatch):
    first, second = await _keyless(client, auth_headers, 2)
    real = RegistryEntry.get_motor_collection()

    async def update_many(flt: dict[str, Any], update, *a, **kw):
        # Another request flips the second entry first; this write then
        # matches only the first.
        await real.update_one({"entry_id": second["entry_id"]}, {"$set": {"status": "active"}})
        return await real.update_many(flt, update, *a, **kw)

    _break_collection(monkeypatch, update_many=update_many)
    body = await _activate(client, auth_headers, [first, second])
    assert _summary(body) == [("activated", None), ("activated", None)]
    assert (body["activated"], body["errors"]) == (2, 0)
