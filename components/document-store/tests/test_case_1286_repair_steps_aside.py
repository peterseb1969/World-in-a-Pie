"""The repair after a failed successor write never leaves two active rows.

When a successor insert fails, the repair reactivates the document's highest
stored version if the document has no active row. The check and the set were
two steps, the set filtered on the row's _id only: a concurrent write to the
same document between them left two active versions, and nothing in the
store enforces one (an `older_versions: keep` restore legitimately holds
several). The set is now conditional on the row not being active, and when
another active row appeared meanwhile the repair undoes its activation:
the document keeps the one active row the concurrent write made.
"""

import pytest
from httpx import AsyncClient

from document_store.models.document import Document, DocumentStatus
from document_store.services.document_service import DocumentService

from .test_case_1140_atomicity import URL, _create, _person, _rows


class _ConcurrentWriteAfterRead:
    """The documents collection, with another write landing right after the
    repair reads the latest row (between its check and its set)."""

    def __init__(self, real, on_latest_read):
        self._real = real
        self._hook = on_latest_read

    def __getattr__(self, name):
        return getattr(self._real, name)

    async def find_one(self, *args, **kwargs):
        doc = await self._real.find_one(*args, **kwargs)
        if "sort" in kwargs and self._hook is not None:
            hook, self._hook = self._hook, None
            await hook()
        return doc


@pytest.mark.asyncio
async def test_a_concurrent_write_during_the_repair_leaves_one_active_row(
    client: AsyncClient, auth_headers, monkeypatch,
):
    [doc_id] = await _create(client, auth_headers, "128600001")
    resp = await client.post(URL, headers=auth_headers, json=[_person("128600001", "Jane")])
    assert resp.status_code == 200, resp.text
    v1, v2 = await _rows(doc_id)
    coll = Document.get_motor_collection()
    # A failed successor write left the document with no active row.
    await coll.update_one({"_id": v2.id}, {"$set": {"status": DocumentStatus.INACTIVE.value}})

    async def concurrent_write():
        await coll.update_one({"_id": v1.id}, {"$set": {"status": DocumentStatus.ACTIVE.value}})

    wrapped = _ConcurrentWriteAfterRead(coll, concurrent_write)
    monkeypatch.setattr(Document, "get_motor_collection", classmethod(lambda cls: wrapped))

    repaired = await DocumentService()._reactivate_if_orphaned(v2.namespace, doc_id)

    monkeypatch.undo()
    assert repaired is False
    rows = await _rows(doc_id)
    active = [r.version for r in rows if r.status == DocumentStatus.ACTIVE]
    assert active == [v1.version]
    restored = next(r for r in rows if r.version == v2.version)
    assert restored.status == DocumentStatus.INACTIVE
    assert restored.updated_by == v2.updated_by


@pytest.mark.asyncio
async def test_with_no_concurrent_write_the_repair_still_reactivates(client: AsyncClient, auth_headers):
    [doc_id] = await _create(client, auth_headers, "128600002")
    [row] = await _rows(doc_id)
    await Document.get_motor_collection().update_one(
        {"_id": row.id}, {"$set": {"status": DocumentStatus.INACTIVE.value}},
    )
    assert await DocumentService()._reactivate_if_orphaned(row.namespace, doc_id) is True
    [after] = await _rows(doc_id)
    assert after.status == DocumentStatus.ACTIVE
