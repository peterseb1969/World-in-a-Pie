"""A single-item POST /documents reports a lost write race as the item's
error (concurrency_conflict), as the bulk path does, instead of a 500.

The single path calls create_document, whose contract is (response, error);
a concurrency failure raised from the in-place overwrite or from an
exhausted version-insert retry escaped that contract and the route had no
handler for it.
"""

import pytest
from httpx import AsyncClient
from pymongo.errors import DuplicateKeyError

from document_store.models.document import Document
from document_store.services.document_service import DocumentService

API = "/api/document-store"


async def _post(client: AsyncClient, headers: dict, item: dict):
    return await client.post(f"{API}/documents", headers=headers, json=[item])


@pytest.mark.asyncio
async def test_a_lost_in_place_race_is_the_items_error(client, auth_headers, monkeypatch):
    note = {"namespace": "wip", "template_id": "LATEST_ONLY_NOTE",
            "data": {"note_id": "N-1279-S1", "body": "v0"}}
    assert (await _post(client, auth_headers, note)).status_code == 200

    async def always_lose(self, *args, **kwargs):
        return False

    monkeypatch.setattr(DocumentService, "_write_in_place", always_lose)
    resp = await _post(client, auth_headers, {**note, "data": {"note_id": "N-1279-S1", "body": "v1"}})
    assert resp.status_code == 200, resp.text
    item = resp.json()["results"][0]
    assert item["status"] == "error" and item["error_code"] == "concurrency_conflict", item


@pytest.mark.asyncio
async def test_an_exhausted_version_retry_is_the_items_error(client, auth_headers, monkeypatch):
    person = {"namespace": "wip", "template_id": "PERSON",
              "data": {"national_id": "127900001", "first_name": "A", "last_name": "B"}}
    created = (await _post(client, auth_headers, person)).json()["results"][0]
    assert created["status"] == "created"

    original = Document.insert

    async def taken(self, *args, **kwargs):
        if self.version > 1:
            raise DuplicateKeyError("E11000 duplicate key")
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(Document, "insert", taken)
    resp = await _post(client, auth_headers, {**person, "data": {**person["data"], "first_name": "C"}})
    assert resp.status_code == 200, resp.text
    item = resp.json()["results"][0]
    assert item["status"] == "error" and item["error_code"] == "concurrency_conflict", item

    monkeypatch.setattr(Document, "insert", original)
    doc = (await client.get(f"{API}/documents/{created['document_id']}", headers=auth_headers)).json()
    assert doc["status"] == "active" and doc["data"]["first_name"] == "A", doc
