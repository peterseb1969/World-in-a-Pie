"""The per-item write path of a bulk create, pinned before its refactor.

Provenance: CASE-1161. A bulk item goes through the per-item path
(DocumentService._apply_validated_item) when it carries inline synonyms,
pins its version, revives a deleted document, or belongs to an unversioned
template. The suite never reached two of that path's branches: a re-post that
changes nothing (reported unchanged, no new version) and an update that does
not address the custom metadata (the existing metadata carries forward). An
inline synonym puts each item here; a second document in every request keeps
it on the bulk route.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from document_store.models.document import Document

DOCS = "/api/document-store/documents"


def _person(national_id: str, synonym: str, first: str = "A", metadata: dict | None = None) -> dict:
    item: dict = {
        "template_id": "PERSON", "namespace": "wip",
        "data": {"national_id": national_id, "first_name": first, "last_name": "B"},
        "synonyms": [{"case_1161_ref": synonym}],
    }
    if metadata is not None:
        item["metadata"] = metadata
    return item


def _nid() -> str:
    return f"{uuid.uuid4().int % 10**9:09d}"


async def _post(client: AsyncClient, headers: dict, items: list[dict]) -> list[dict]:
    """Post the items with one unrelated document after them — a request of
    one item takes the single-document path, not the bulk one — and return
    the items' own results."""
    pad = _person(_nid(), uuid.uuid4().hex)
    resp = await client.post(DOCS, headers=headers, json=[*items, pad])
    assert resp.status_code == 200, resp.text
    results = sorted(resp.json()["results"], key=lambda r: r["index"])
    assert results[-1]["status"] == "created"
    return results[:-1]


async def _rows(document_id: str) -> list[Document]:
    return sorted(
        await Document.find({"document_id": document_id}).to_list(), key=lambda d: d.version,
    )


@pytest.mark.asyncio
async def test_a_repost_that_changes_nothing_is_unchanged(client: AsyncClient, auth_headers: dict):
    nid, syn = _nid(), uuid.uuid4().hex
    [first] = await _post(client, auth_headers, [_person(nid, syn)])
    assert (first["status"], first["version"]) == ("created", 1)

    [again] = await _post(client, auth_headers, [_person(nid, syn)])
    assert again["status"] == "unchanged"
    assert again["document_id"] == first["document_id"]
    assert again["version"] == 1
    assert again["is_new"] is False
    assert again["identity_hash"] == first["identity_hash"]
    rows = await _rows(first["document_id"])
    assert [(r.version, r.status.value) for r in rows] == [(1, "active")]


@pytest.mark.asyncio
async def test_an_update_without_metadata_carries_the_custom_metadata_forward(
    client: AsyncClient, auth_headers: dict,
):
    nid, syn = _nid(), uuid.uuid4().hex
    [first] = await _post(client, auth_headers, [_person(nid, syn, metadata={"source": "erp"})])

    [update] = await _post(client, auth_headers, [_person(nid, syn, first="A2")])
    assert (update["status"], update["version"]) == ("updated", 2)
    rows = await _rows(first["document_id"])
    assert [(r.version, r.status.value) for r in rows] == [(1, "inactive"), (2, "active")]
    assert rows[1].metadata.custom == {"source": "erp"}
    assert rows[1].data["first_name"] == "A2"


@pytest.mark.asyncio
async def test_an_update_with_metadata_replaces_it(client: AsyncClient, auth_headers: dict):
    nid, syn = _nid(), uuid.uuid4().hex
    [first] = await _post(client, auth_headers, [_person(nid, syn, metadata={"source": "erp"})])

    [update] = await _post(client, auth_headers, [_person(nid, syn, first="A2", metadata={})])
    assert (update["status"], update["version"]) == ("updated", 2)
    rows = await _rows(first["document_id"])
    assert rows[1].metadata.custom == {}
