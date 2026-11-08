"""A file batch reports a namespace refusal on its item, at HTTP 200.

``PATCH /files`` and ``DELETE /files`` checked write permission inside the
item loop with a call that raises. One item in a namespace the caller cannot
write turned the whole request into a 403/404 after earlier items had already
been updated or deleted, and none of them was reported. A refusal is now a
per-item ``status: "error"`` (``forbidden`` for a readable namespace,
``not_found`` worded as a missing file for an invisible one), and an
authorization outage refuses the request before any item is written.
"""

from unittest.mock import patch

import pytest
from httpx import AsyncClient

from document_store.models.file import File, FileStatus
from tests.test_files import _insert_file, file_client  # noqa: F401 — fixture

URL = "/api/document-store/files"
READER = {"X-API-Key": "test_xns_reader_key"}  # write on wip, read on xns-closed
WRITER = {"X-API-Key": "test_xns_writer_key"}  # wip only: xns-closed is invisible


async def _batch():
    first = await _insert_file(namespace="wip", description="before")
    closed = await _insert_file(namespace="xns-closed", description="before")
    last = await _insert_file(namespace="wip", description="before")
    return first, closed, last


async def _current(file_id: str) -> File:
    doc = await File.find_one({"file_id": file_id})
    assert doc is not None
    return doc


@pytest.mark.asyncio
async def test_patch_mixed_batch_reports_forbidden_item(file_client: AsyncClient):  # noqa: F811
    first, closed, last = await _batch()
    resp = await file_client.patch(URL, headers=READER, json=[
        {"file_id": f.file_id, "description": "after"} for f in (first, closed, last)
    ])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [r["status"] for r in body["results"]] == ["updated", "error", "updated"]
    assert body["results"][1]["error_code"] == "forbidden"
    assert body["results"][1]["id"] == closed.file_id
    assert (body["succeeded"], body["failed"]) == (2, 1)
    assert (await _current(first.file_id)).metadata.description == "after"
    assert (await _current(closed.file_id)).metadata.description == "before"
    assert (await _current(last.file_id)).metadata.description == "after"


@pytest.mark.asyncio
async def test_delete_mixed_batch_reports_forbidden_item(file_client: AsyncClient):  # noqa: F811
    first, closed, last = await _batch()
    resp = await file_client.request("DELETE", URL, headers=READER, json=[
        {"id": f.file_id} for f in (first, closed, last)
    ])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [r["status"] for r in body["results"]] == ["deleted", "error", "deleted"]
    assert body["results"][1]["error_code"] == "forbidden"
    assert (body["succeeded"], body["failed"]) == (2, 1)
    assert (await _current(first.file_id)).status == FileStatus.INACTIVE
    assert (await _current(closed.file_id)).status != FileStatus.INACTIVE
    assert (await _current(last.file_id)).status == FileStatus.INACTIVE


@pytest.mark.asyncio
async def test_invisible_namespace_answers_like_a_missing_file(file_client: AsyncClient):  # noqa: F811
    first, closed, _ = await _batch()
    missing = "019a0000-0000-7000-8000-000000000000"
    resp = await file_client.request("DELETE", URL, headers=WRITER, json=[
        {"id": first.file_id}, {"id": closed.file_id}, {"id": missing},
    ])
    assert resp.status_code == 200, resp.text
    results = resp.json()["results"]
    assert results[0]["status"] == "deleted"
    hidden, absent = results[1], results[2]
    assert (hidden["error_code"], hidden["error"]) == (absent["error_code"], absent["error"])
    assert hidden["error_code"] == "not_found"
    assert (await _current(closed.file_id)).status != FileStatus.INACTIVE


@pytest.mark.asyncio
async def test_authorization_outage_refuses_before_any_write(file_client: AsyncClient):  # noqa: F811
    first, closed, _ = await _batch()

    async def answer(identity, namespace):
        return "unavailable" if namespace == "xns-closed" else "write"

    with patch("wip_auth.permissions.resolve_permission", side_effect=answer):
        resp = await file_client.patch(URL, headers=READER, json=[
            {"file_id": first.file_id, "description": "after"},
            {"file_id": closed.file_id, "description": "after"},
        ])
    assert resp.status_code == 503, resp.text
    assert (await _current(first.file_id)).metadata.description == "before"
    assert (await _current(closed.file_id)).metadata.description == "before"
