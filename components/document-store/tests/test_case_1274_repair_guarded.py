"""The repair after a failed write is itself guarded, and matches what landed.

A successor write retires the active row, then inserts the new one; when the
insert fails, a repair gives the document its active version back. The
repair and the check before it ran unguarded: if the store was still
unreachable, the bulk landed-rows read raised out of the request (500, no
results) and the repair never ran, and on the per-item paths a failing repair
replaced the write's own error. Rows a duplicate-key retry retired kept the
failed write's stamps. Which rows of a failed chunk landed was answered by
(document_id, version), so a row another write stored at that slot counted
as this write's.
"""

from unittest.mock import patch

import pytest
from bson import ObjectId
from httpx import AsyncClient
from pymongo.errors import AutoReconnect

from document_store.models.document import Document, DocumentStatus
from document_store.services.document_service import DocumentService

from .test_case_1140_atomicity import URL, _assert_intact, _create, _person, _rows


class _FindFails:
    """The documents collection with ``find`` raising, as an unreachable store
    does, once ``armed`` is set."""

    def __init__(self, real):
        self._real = real
        self.armed = False

    def __getattr__(self, name):
        return getattr(self._real, name)

    def find(self, *args, **kwargs):
        if self.armed:
            raise AutoReconnect("still unreachable")
        return self._real.find(*args, **kwargs)


@pytest.mark.asyncio
async def test_an_unreadable_store_after_a_failed_chunk_reports_every_row(
    client: AsyncClient, auth_headers, monkeypatch,
):
    ids = await _create(client, auth_headers, "127400001", "127400002")
    before = [(await _rows(d))[-1] for d in ids]
    wrapped = _FindFails(Document.get_motor_collection())
    monkeypatch.setattr(Document, "get_motor_collection", classmethod(lambda cls: wrapped))

    async def fail(*args, **kwargs):
        wrapped.armed = True
        raise AutoReconnect("connection lost")

    with patch.object(Document, "insert_many", fail):
        resp = await client.post(URL, headers=auth_headers, json=[
            _person("127400001", "Jane"), _person("127400002", "Jane"),
        ])
    monkeypatch.undo()
    assert resp.status_code == 200, resp.text
    results = resp.json()["results"]
    assert [r["status"] for r in results] == ["error", "error"], results
    assert all("unknown" in r["error"] for r in results), results
    for doc_id, prior in zip(ids, before, strict=True):
        await _assert_intact(doc_id, prior)


@pytest.mark.asyncio
async def test_a_bulk_write_that_raises_still_repairs(client: AsyncClient, auth_headers):
    ids = await _create(client, auth_headers, "127400011", "127400012")
    before = [(await _rows(d))[-1] for d in ids]

    async def boom(*args, **kwargs):
        raise RuntimeError("unexpected")

    with (
        patch.object(DocumentService, "_insert_planned", boom),
        pytest.raises(RuntimeError),
    ):
        await client.post(URL, headers=auth_headers, json=[
            _person("127400011", "Jane"), _person("127400012", "Jane"),
        ])
    for doc_id, prior in zip(ids, before, strict=True):
        await _assert_intact(doc_id, prior)


@pytest.mark.asyncio
async def test_a_failing_repair_does_not_replace_the_write_error(client: AsyncClient, auth_headers):
    [doc_id] = await _create(client, auth_headers, "127400021")

    async def fail_insert(*args, **kwargs):
        raise AutoReconnect("connection lost")

    async def fail_repair(*args, **kwargs):
        raise RuntimeError("repair failed too")

    with (
        patch.object(DocumentService, "_insert_with_retry", fail_insert),
        patch.object(DocumentService, "_reactivate_if_orphaned", fail_repair),
        pytest.raises(AutoReconnect),
    ):
        await client.post(URL, headers=auth_headers, json=[_person("127400021", "Jane")])


@pytest.mark.asyncio
async def test_a_row_another_write_stored_in_the_slot_is_not_this_writes(
    client: AsyncClient, auth_headers,
):
    doc_id, other_id = await _create(client, auth_headers, "127400031", "127400032")
    [v1] = await _rows(doc_id)
    coll = Document.get_motor_collection()
    real = Document.insert_many

    async def foreign_then_fail(documents, *args, **kwargs):
        # Another write stores the first document's version 2 under its own
        # _id; the second document's row is stored; then the connection drops.
        row = documents[0].model_dump(by_alias=True, mode="python")
        row["_id"] = ObjectId()
        row["updated_by"] = "someone-else"
        await coll.insert_one(row)
        await real(documents[1:], *args, **kwargs)
        raise AutoReconnect("connection lost")

    with patch.object(Document, "insert_many", foreign_then_fail):
        resp = await client.post(URL, headers=auth_headers, json=[
            _person("127400031", "Jane"), _person("127400032", "Jane"),
        ])
    assert resp.status_code == 200, resp.text
    results = sorted(resp.json()["results"], key=lambda r: r["index"])
    assert [r["status"] for r in results] == ["error", "updated"], results
    assert [r.version for r in await _rows(other_id) if r.status == DocumentStatus.ACTIVE] == [2]
    rows = await _rows(doc_id)
    assert [r.version for r in rows if r.status == DocumentStatus.ACTIVE] == [2]
    assert rows[-1].updated_by == "someone-else"
    assert rows[0].id == v1.id


@pytest.mark.asyncio
async def test_a_row_a_retry_retired_gets_its_own_stamps_back(client: AsyncClient, auth_headers):
    [doc_id] = await _create(client, auth_headers, "127400041")
    coll = Document.get_motor_collection()
    real_insert = Document.insert
    calls = 0

    async def insert(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            # A concurrent writer takes version 2 first: this insert hits a
            # duplicate key and the retry retires that writer's row.
            row = self.model_dump(by_alias=True, mode="python")
            row["_id"] = ObjectId()
            row["status"] = DocumentStatus.ACTIVE.value
            row["updated_by"] = "concurrent-writer"
            await coll.insert_one(row)
            return await real_insert(self, *args, **kwargs)
        raise AutoReconnect("connection lost")

    with (
        patch.object(Document, "insert", insert),
        pytest.raises(AutoReconnect),
    ):
        await client.post(URL, headers=auth_headers, json=[_person("127400041", "Jane")])
    rows = await _rows(doc_id)
    active = [r for r in rows if r.status == DocumentStatus.ACTIVE]
    assert [r.version for r in active] == [2], [(r.version, r.status) for r in rows]
    assert active[0].updated_by == "concurrent-writer"
