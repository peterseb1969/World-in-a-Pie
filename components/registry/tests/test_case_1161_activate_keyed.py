"""Keyed activation's failure handling, pinned before _activate_keyed's refactor.

Provenance: CASE-1161. The suite never reached these branches of a keyed
activation (a deferred-claim reservation finalised with its key): the entry
read failing, the claim failing, the guarded bulk write failing per row or
with an unknown outcome, the re-read failing, an entry whose status changed
between the read and the write, and the claim confirm failing. Each is
answered per item (bulk-first), the entries are never wrongly reported
activated, and the response counts match the statuses.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from pymongo.errors import BulkWriteError

from registry.api import entries as entries_api
from registry.models.entry import RegistryEntry
from tests.test_reservation_lifecycle import NAMESPACE, _provision

URL = "/api/registry/entries/activate"


async def _activate(client, headers, items) -> dict:
    resp = await client.post(URL, json=items, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _reserved(client, headers, n: int, tag: str) -> list[dict]:
    ids = await _provision(client, headers, count=n, defer_claim=True)
    return [{"entry_id": eid, "composite_key": {"ns": NAMESPACE, "value": f"K-{tag}-{k}"}}
            for k, eid in enumerate(ids)]


async def _status(entry_id: str) -> str:
    entry = await RegistryEntry.find_one(RegistryEntry.entry_id == entry_id)
    assert entry is not None
    return entry.status


def _summary(body: dict) -> list[tuple[str, str | None]]:
    return [(r["status"], r.get("error_code")) for r in body["results"]]


class _Collection:
    """The real entries collection with chosen calls broken."""

    def __init__(self, real: Any, **broken: Any):
        self._real = real
        self._broken = broken

    def __getattr__(self, name: str) -> Any:
        if name in self._broken:
            return self._broken[name]
        return getattr(self._real, name)


def _break_collection(monkeypatch, **broken):
    real = RegistryEntry.get_motor_collection()
    monkeypatch.setattr(RegistryEntry, "get_motor_collection", classmethod(lambda cls: _Collection(real, **broken)))


@pytest.mark.asyncio
async def test_a_failed_entry_read_fails_every_keyed_item(client: AsyncClient, auth_headers, monkeypatch):
    items = await _reserved(client, auth_headers, 2, "read")

    def broken_find(*a, **kw):
        raise RuntimeError("read failed")

    monkeypatch.setattr(RegistryEntry, "find", broken_find)
    body = await _activate(client, auth_headers, items)
    assert [r["status"] for r in body["results"]] == ["error", "error"]
    assert (body["activated"], body["errors"]) == (0, 2)


@pytest.mark.asyncio
async def test_a_failed_claim_fails_the_items_and_they_stay_reserved(client: AsyncClient, auth_headers, monkeypatch):
    items = await _reserved(client, auth_headers, 2, "claim")

    async def broken_claim(entries, token):
        raise RuntimeError("claims down")

    monkeypatch.setattr(entries_api, "claim_entries_keys_pending", broken_claim)
    body = await _activate(client, auth_headers, items)
    assert [r["status"] for r in body["results"]] == ["error", "error"]
    assert "claims down" in body["results"][0]["error"]
    assert (body["activated"], body["errors"]) == (0, 2)
    assert [await _status(i["entry_id"]) for i in items] == ["reserved", "reserved"]


@pytest.mark.asyncio
async def test_a_per_row_write_error_fails_that_row_and_an_unwritten_row_is_a_conflict(
    client: AsyncClient, auth_headers, monkeypatch,
):
    items = await _reserved(client, auth_headers, 2, "bwe")

    async def bulk_write(ops, ordered=False):
        raise BulkWriteError({"writeErrors": [{"index": 0, "errmsg": "row 0 refused"}]})

    _break_collection(monkeypatch, bulk_write=bulk_write)
    body = await _activate(client, auth_headers, items)
    assert body["results"][0]["status"] == "error"
    assert body["results"][0]["error"] == "row 0 refused"
    # Row 1 had no write error, but nothing was written: the re-read shows
    # it still reserved, so it is not reported activated.
    assert (body["results"][1]["status"], body["results"][1]["error_code"]) == ("error", "concurrency_conflict")
    assert (body["activated"], body["errors"]) == (0, 2)
    assert [await _status(i["entry_id"]) for i in items] == ["reserved", "reserved"]


@pytest.mark.asyncio
async def test_an_unknown_write_outcome_is_decided_by_the_re_read(client: AsyncClient, auth_headers, monkeypatch):
    items = await _reserved(client, auth_headers, 2, "unk")
    real = RegistryEntry.get_motor_collection()

    async def bulk_write(ops, ordered=False):
        # The first op lands, then the connection "drops".
        await real.bulk_write(ops[:1], ordered=ordered)
        raise RuntimeError("connection reset")

    _break_collection(monkeypatch, bulk_write=bulk_write)
    body = await _activate(client, auth_headers, items)
    assert body["results"][0]["status"] == "activated"
    assert body["results"][1]["status"] == "error"
    assert "connection reset" in body["results"][1]["error"]
    assert (body["activated"], body["errors"]) == (1, 1)
    assert [await _status(i["entry_id"]) for i in items] == ["active", "reserved"]


@pytest.mark.asyncio
async def test_a_failed_re_read_reports_every_written_item_as_an_error(
    client: AsyncClient, auth_headers, monkeypatch,
):
    items = await _reserved(client, auth_headers, 2, "reread")
    real = RegistryEntry.get_motor_collection()
    wrote: list[bool] = []

    async def bulk_write(ops, ordered=False):
        wrote.append(True)
        return await real.bulk_write(ops, ordered=ordered)

    def find(*a, **kw):
        # The claim step reads through this collection too; only the
        # re-read after the write fails.
        if wrote:
            raise RuntimeError("re-read failed")
        return real.find(*a, **kw)

    _break_collection(monkeypatch, bulk_write=bulk_write, find=find)
    body = await _activate(client, auth_headers, items)
    assert wrote == [True]
    assert [r["status"] for r in body["results"]] == ["error", "error"]
    assert "re-read failed" in body["results"][0]["error"]
    assert (body["activated"], body["errors"]) == (0, 2)
    # The write itself landed; only its confirmation was lost.
    assert [await _status(i["entry_id"]) for i in items] == ["active", "active"]


@pytest.mark.asyncio
async def test_an_entry_that_is_neither_reserved_nor_active_is_refused(client: AsyncClient, auth_headers):
    [item] = await _reserved(client, auth_headers, 1, "state")
    await RegistryEntry.get_motor_collection().update_one(
        {"entry_id": item["entry_id"]}, {"$set": {"status": "inactive"}},
    )
    body = await _activate(client, auth_headers, [item])
    [result] = body["results"]
    assert (result["status"], result["error_code"]) == ("error", "invalid_state")
    assert result["error"] == "Cannot activate entry with status 'inactive'"
    assert result["details"] == {"rule": "not_reserved"}
    assert (body["activated"], body["errors"]) == (0, 1)


@pytest.mark.asyncio
async def test_an_entry_retired_between_read_and_write_is_a_conflict(client: AsyncClient, auth_headers, monkeypatch):
    items = await _reserved(client, auth_headers, 2, "race")
    real = RegistryEntry.get_motor_collection()
    victim = items[1]["entry_id"]

    async def bulk_write(ops, ordered=False):
        await real.update_one({"entry_id": victim}, {"$set": {"status": "inactive"}})
        return await real.bulk_write(ops, ordered=ordered)

    _break_collection(monkeypatch, bulk_write=bulk_write)
    body = await _activate(client, auth_headers, items)
    assert _summary(body) == [("activated", None), ("error", "concurrency_conflict")]
    assert body["results"][1]["error"] == "Entry status changed concurrently; not activated"
    assert (body["activated"], body["errors"]) == (1, 1)


@pytest.mark.asyncio
async def test_a_failed_claim_confirm_still_reports_the_activation(client: AsyncClient, auth_headers, monkeypatch):
    items = await _reserved(client, auth_headers, 1, "confirm")

    async def broken_confirm(entries):
        raise RuntimeError("confirm failed")

    monkeypatch.setattr(entries_api, "confirm_entries_keys", broken_confirm)
    body = await _activate(client, auth_headers, items)
    assert _summary(body) == [("activated", None)]
    assert (body["activated"], body["errors"]) == (1, 0)
    assert await _status(items[0]["entry_id"]) == "active"


@pytest.mark.asyncio
async def test_repeats_of_an_activated_and_of_a_failed_item(client: AsyncClient, auth_headers):
    good, bad = await _reserved(client, auth_headers, 2, "rep")
    bad = {**bad, "composite_key": {"ns": "elsewhere", "value": "x"}}  # scoped to another namespace
    body = await _activate(client, auth_headers, [good, bad, good, bad])
    statuses = [r["status"] for r in body["results"]]
    assert statuses == ["activated", "error", "already_active", "error"]
    assert body["results"][3]["error_code"] == body["results"][1]["error_code"]
    assert (body["activated"], body["errors"]) == (1, 2)
