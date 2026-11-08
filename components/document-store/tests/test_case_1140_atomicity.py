"""A failed successor write leaves the entity with an active version.

A new version is written by retiring the active row and then inserting its
successor. When the insert failed, nothing put the retired row back: the
entity had no active version, which reads as a retired document, and on the
bulk path an insert failure other than a per-row write error answered 500
with no results at all. Now every failed successor reports its own error and
the entity's highest stored version is active again, with the retired row's
own updated_at and updated_by.
"""

from unittest.mock import patch

import pytest
from httpx import AsyncClient
from pymongo.errors import AutoReconnect, BulkWriteError

from document_store.models.document import Document, DocumentStatus

URL = "/api/document-store/documents"


def _person(national_id: str, first_name: str = "John") -> dict:
    return {
        "namespace": "wip", "template_id": "PERSON",
        "data": {"national_id": national_id, "first_name": first_name,
                 "last_name": "Doe", "age": 34},
    }


async def _create(client, headers, *national_ids) -> list[str]:
    resp = await client.post(URL, headers=headers, json=[_person(n) for n in national_ids])
    assert resp.status_code == 200, resp.text
    results = sorted(resp.json()["results"], key=lambda r: r["index"])
    assert all(r["status"] == "created" for r in results), results
    return [r["document_id"] for r in results]


async def _rows(doc_id: str) -> list[Document]:
    return sorted(await Document.find({"document_id": doc_id}).to_list(), key=lambda d: d.version)


async def _assert_intact(doc_id: str, before: Document) -> None:
    """One active row: the version that was live before the failed write,
    carrying its own stamps."""
    rows = await _rows(doc_id)
    active = [r for r in rows if r.status == DocumentStatus.ACTIVE]
    assert len(active) == 1, [(r.version, r.status) for r in rows]
    assert active[0].version == before.version
    assert active[0].updated_by == before.updated_by
    assert abs((active[0].updated_at - before.updated_at).total_seconds()) < 0.01


# --- bulk path ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_insert_failure_reports_items_and_keeps_versions(
    client: AsyncClient, auth_headers,
):
    ids = await _create(client, auth_headers, "114100001", "114100002")
    before = [(await _rows(d))[-1] for d in ids]

    async def fail(*args, **kwargs):
        raise AutoReconnect("connection lost")

    with patch.object(Document, "insert_many", fail):
        resp = await client.post(URL, headers=auth_headers, json=[
            _person("114100001", "Jane"), _person("114100002", "Jane"),
        ])
    assert resp.status_code == 200, resp.text
    results = resp.json()["results"]
    assert [r["status"] for r in results] == ["error", "error"], results
    for doc_id, prior in zip(ids, before, strict=True):
        await _assert_intact(doc_id, prior)


@pytest.mark.asyncio
async def test_bulk_row_refusal_reactivates_only_that_entity(client: AsyncClient, auth_headers):
    ids = await _create(client, auth_headers, "114100011", "114100012")
    before = [(await _rows(d))[-1] for d in ids]
    real = Document.insert_many

    async def refuse_second(documents, *args, **kwargs):
        await real(documents[:1], *args, **kwargs)
        raise BulkWriteError({"writeErrors": [
            {"index": 1, "code": 121, "errmsg": "Document failed validation"},
        ]})

    with patch.object(Document, "insert_many", refuse_second):
        resp = await client.post(URL, headers=auth_headers, json=[
            _person("114100011", "Jane"), _person("114100012", "Jane"),
        ])
    assert resp.status_code == 200, resp.text
    results = sorted(resp.json()["results"], key=lambda r: r["index"])
    assert [r["status"] for r in results] == ["updated", "error"], results
    first = await _rows(ids[0])
    assert [(r.version, r.status) for r in first] == [
        (1, DocumentStatus.INACTIVE), (2, DocumentStatus.ACTIVE),
    ]
    await _assert_intact(ids[1], before[1])


# --- per-item paths -------------------------------------------------------------


async def _fail_insert(*args, **kwargs):
    raise AutoReconnect("connection lost")


@pytest.mark.asyncio
async def test_single_update_insert_failure_keeps_the_version(client: AsyncClient, auth_headers):
    [doc_id] = await _create(client, auth_headers, "114100021")
    before = (await _rows(doc_id))[-1]
    with (
        patch(
            "document_store.services.document_service.DocumentService._insert_with_retry",
            _fail_insert,
        ),
        pytest.raises(AutoReconnect),  # the single-item path answers 500 on it
    ):
        await client.post(URL, headers=auth_headers, json=[_person("114100021", "Jane")])
    await _assert_intact(doc_id, before)


@pytest.mark.asyncio
async def test_sequential_bulk_insert_failure_keeps_the_version(client: AsyncClient, auth_headers):
    # continue_on_error=false writes item by item (_apply_validated_item).
    ids = await _create(client, auth_headers, "114100031", "114100032")
    before = (await _rows(ids[0]))[-1]
    with patch(
        "document_store.services.document_service.DocumentService._insert_with_retry",
        _fail_insert,
    ):
        resp = await client.post(f"{URL}?continue_on_error=false", headers=auth_headers, json=[
            _person("114100031", "Jane"), _person("114100032", "Jane"),
        ])
    assert resp.status_code == 200, resp.text
    await _assert_intact(ids[0], before)


@pytest.mark.asyncio
async def test_patch_insert_failure_keeps_the_version(client: AsyncClient, auth_headers):
    [doc_id] = await _create(client, auth_headers, "114100041")
    before = (await _rows(doc_id))[-1]
    with patch(
        "document_store.services.document_service.DocumentService._insert_with_retry",
        _fail_insert,
    ):
        resp = await client.patch(URL, headers=auth_headers, json=[
            {"document_id": doc_id, "patch": {"first_name": "Jane"}},
        ])
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][0]["status"] == "error"
    await _assert_intact(doc_id, before)
