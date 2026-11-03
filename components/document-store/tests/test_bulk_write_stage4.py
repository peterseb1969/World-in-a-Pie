"""Bulk create writes its rows in bulk, with the per-item results of before.

Stage 4 used to insert each row on its own, plus a save retiring each
replaced version: about 500 round trips per 500-row batch, half of the live
write path's time. Plain creates and new versions are now planned in memory
and written with one update (retiring replaced versions) and one unordered
insert. Items that need the per-item path keep it, with every other item of
their document. The bulk document routes accept at most MAX_BULK_ITEMS items.
"""

import uuid

import pytest
from beanie import Document as BeanieDocument
from httpx import AsyncClient
from pymongo.errors import BulkWriteError

from document_store.models.api_models import MAX_BULK_ITEMS
from document_store.models.document import Document
from document_store.services import document_service as ds
from document_store.services.document_service import DocumentService

DOCS = "/api/document-store/documents"


def _person(national_id: str, first: str = "A") -> dict:
    return {"template_id": "PERSON", "namespace": "wip",
            "data": {"national_id": national_id, "first_name": first, "last_name": "B"}}


def _nid() -> str:
    return f"{uuid.uuid4().int % 10**9:09d}"


@pytest.fixture
def writes(monkeypatch):
    """Counts the bulk inserts and the per-item inserts stage 4 makes."""
    calls = {"insert_many": 0, "per_item": 0}
    real_many = BeanieDocument.insert_many.__func__
    real_retry = DocumentService._insert_with_retry

    async def insert_many(cls, documents, *a, **kw):
        if cls is Document:
            calls["insert_many"] += 1
        return await real_many(cls, documents, *a, **kw)

    async def retry(self, *a, **kw):
        calls["per_item"] += 1
        return await real_retry(self, *a, **kw)

    monkeypatch.setattr(Document, "insert_many", classmethod(insert_many))
    monkeypatch.setattr(DocumentService, "_insert_with_retry", retry)
    return calls


@pytest.fixture
def events(monkeypatch):
    seen: list[tuple] = []

    async def publish(event_type, payload, changed_by=None):
        seen.append((event_type, payload))

    monkeypatch.setattr(ds, "publish_document_event", publish)
    return seen


async def _post(client, headers, items, **params):
    r = await client.post(DOCS, headers=headers, json=items, params=params)
    assert r.status_code == 200, r.text
    return r.json()


async def _versions(document_id: str) -> list[tuple[int, str]]:
    rows = await Document.find({"document_id": document_id}).sort("version").to_list()
    return [(d.version, d.status.value) for d in rows]


@pytest.mark.asyncio
async def test_a_batch_of_new_documents_is_one_insert(client: AsyncClient, auth_headers, writes):
    body = await _post(client, auth_headers, [_person(_nid()) for _ in range(20)])
    assert [r["status"] for r in body["results"]] == ["created"] * 20
    assert writes == {"insert_many": 1, "per_item": 0}
    assert "4b_insert_documents" in body["timing"]


@pytest.mark.asyncio
async def test_versions_and_repeats_in_one_batch_keep_one_active_row(
    client: AsyncClient, auth_headers, writes, events,
):
    a, b, c = _nid(), _nid(), _nid()
    first = await _post(client, auth_headers, [_person(a), _person(b)])
    ids = {r["index"]: r["document_id"] for r in first["results"]}
    events.clear()

    body = await _post(client, auth_headers, [
        _person(a, "A2"),                       # new version of a stored document
        _person(b),                             # unchanged
        _person(c), _person(c, "C2"), _person(c, "C3"),   # one document, three versions
    ])
    statuses = [(r["status"], r["version"]) for r in body["results"]]
    assert statuses == [("updated", 2), ("unchanged", 1), ("created", 1), ("updated", 2), ("updated", 3)]
    assert writes["per_item"] == 0

    assert await _versions(ids[0]) == [(1, "inactive"), (2, "active")]
    assert await _versions(ids[1]) == [(1, "active")]
    assert await _versions(body["results"][2]["document_id"]) == [
        (1, "inactive"), (2, "inactive"), (3, "active"),
    ]
    # Each written row publishes one event, showing the row as inserted.
    assert len(events) == 4
    assert {p["status"] for _, p in events} == {"active"}


@pytest.mark.asyncio
async def test_inline_synonyms_keep_the_per_item_path_for_their_document(
    client: AsyncClient, auth_headers, writes,
):
    a, b = _nid(), _nid()
    with_synonym = _person(a)
    with_synonym["synonyms"] = [{"bulk_1026_ref": uuid.uuid4().hex}]
    body = await _post(client, auth_headers, [with_synonym, _person(a, "A2"), _person(b)])
    assert [(r["status"], r["version"]) for r in body["results"]] == [
        ("created", 1), ("updated", 2), ("created", 1),
    ]
    assert writes == {"insert_many": 1, "per_item": 2}
    assert await _versions(body["results"][0]["document_id"]) == [(1, "inactive"), (2, "active")]


@pytest.mark.asyncio
async def test_a_duplicate_key_row_is_retried_alone(
    client: AsyncClient, auth_headers, writes, monkeypatch,
):
    real_many = BeanieDocument.insert_many.__func__

    async def one_lost_race(cls, documents, *a, **kw):
        documents = list(documents)
        await real_many(cls, documents[1:], *a, **kw)
        raise BulkWriteError({"writeErrors": [{"index": 0, "code": 11000, "errmsg": "dup"}],
                              "nInserted": len(documents) - 1})

    monkeypatch.setattr(Document, "insert_many", classmethod(one_lost_race))
    body = await _post(client, auth_headers, [_person(_nid()), _person(_nid())])
    assert [r["status"] for r in body["results"]] == ["created", "created"]
    assert writes["per_item"] == 1
    for r in body["results"]:
        assert await _versions(r["document_id"]) == [(1, "active")]


@pytest.mark.asyncio
async def test_stop_on_first_error_keeps_the_per_item_path(client: AsyncClient, auth_headers, writes):
    body = await _post(client, auth_headers, [_person(_nid()), _person(_nid())],
                       continue_on_error="false")
    assert [r["status"] for r in body["results"]] == ["created", "created"]
    assert writes == {"insert_many": 0, "per_item": 2}


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,item", [
    ("POST", DOCS, _person("123456789")),
    ("PATCH", DOCS, {"document_id": "x", "patch": {}}),
    ("DELETE", DOCS, {"id": "x"}),
])
async def test_a_bulk_request_is_capped(client: AsyncClient, auth_headers, method, path, item):
    r = await client.request(method, path, headers=auth_headers, json=[item] * (MAX_BULK_ITEMS + 1))
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_bulk_validation_is_capped(client: AsyncClient, auth_headers):
    r = await client.post("/api/document-store/validation/validate-bulk", headers=auth_headers, json={
        "template_id": "PERSON", "namespace": "wip", "items": [{}] * (MAX_BULK_ITEMS + 1),
    })
    assert r.status_code == 422, r.text
