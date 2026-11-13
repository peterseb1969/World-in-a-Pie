"""The table view is bounded and counts documents, not version rows.

- A JSON page refuses to flatten into more than MAX_TABLE_ROWS rows (it
  could reach page_size x max_cross_product, 10 million at the limits).
- The CSV export streams from a cursor instead of holding every document,
  every row and the whole file in memory at once.
- A document with several active version rows (a merge restore with
  older_versions: keep) is shown once, at its latest version, and counted
  once in total_documents.
"""

import csv
import io

import pytest
from httpx import AsyncClient

from document_store.api import table_view
from document_store.models.document import Document

API = "/api/document-store"


async def _person(client: AsyncClient, headers: dict, nid: str, first: str) -> dict:
    resp = await client.post(f"{API}/documents", headers=headers, json=[{
        "namespace": "wip", "template_id": "PERSON",
        "data": {"national_id": nid, "first_name": first, "last_name": "Bounds"},
    }])
    result = resp.json()["results"][0]
    assert result["status"] in ("created", "updated"), result
    return result


@pytest.mark.asyncio
async def test_a_page_over_the_row_cap_is_refused(client, auth_headers, monkeypatch):
    for i in range(3):
        await _person(client, auth_headers, f"11450010{i}", f"P{i}")
    monkeypatch.setattr(table_view, "MAX_TABLE_ROWS", 2)
    resp = await client.get(f"{API}/table/PERSON", headers=auth_headers)
    assert resp.status_code == 422, resp.text
    assert "page_size" in resp.text and "max_cross_product" in resp.text
    # The CSV export is not capped: it streams.
    assert (await client.get(f"{API}/table/PERSON/csv", headers=auth_headers)).status_code == 200


@pytest.mark.asyncio
async def test_the_csv_export_streams_in_chunks_and_loses_no_row(client, auth_headers, monkeypatch):
    ids = [(await _person(client, auth_headers, f"11450020{i}", f"S{i}"))["document_id"]
           for i in range(5)]
    monkeypatch.setattr(table_view, "CSV_CHUNK_CHARS", 1)  # flush after every document
    resp = await client.get(f"{API}/table/PERSON/csv", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    assert {r["_document_id"] for r in rows} >= set(ids)
    assert len(rows) == len({r["_document_id"] for r in rows})


@pytest.mark.asyncio
async def test_a_document_with_several_active_versions_is_one_row(client, auth_headers):
    first = await _person(client, auth_headers, "114500301", "Old")
    await _person(client, auth_headers, "114500301", "New")  # version 2; v1 retired
    # What a merge with older_versions: keep leaves: v1 active again.
    await Document.get_motor_collection().update_one(
        {"document_id": first["document_id"], "version": 1}, {"$set": {"status": "active"}},
    )

    resp = await client.get(f"{API}/table/PERSON", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    mine = [r for r in resp.json()["rows"] if r["_document_id"] == first["document_id"]]
    assert [(r["_version"], r["first_name"]) for r in mine] == [(2, "New")]
    assert resp.json()["total_documents"] == len({r["_document_id"] for r in resp.json()["rows"]})

    exported = await client.get(f"{API}/table/PERSON/csv", headers=auth_headers)
    lines = [r for r in csv.DictReader(io.StringIO(exported.text))
             if r["_document_id"] == first["document_id"]]
    assert [r["first_name"] for r in lines] == ["New"]


@pytest.mark.asyncio
async def test_a_csv_that_fails_before_its_first_chunk_is_an_error_not_an_empty_200(
    client, auth_headers, monkeypatch,
):
    """The export starts streaming only after its first chunk exists, so a
    failure up to it raises from the route (an error status) instead of
    answering 200 with an empty or partial body."""
    from fastapi.responses import StreamingResponse

    from wip_auth import UserIdentity

    await _person(client, auth_headers, "114500401", "Fail")

    def broken(*args, **kwargs):
        raise RuntimeError("flatten failed")

    monkeypatch.setattr(table_view, "_flatten_document", broken)
    admin = UserIdentity(user_id="a", username="a", groups=["wip-admins"], auth_method="api_key")
    with pytest.raises(RuntimeError, match="flatten failed"):
        response = await table_view.export_table_csv(
            "PERSON", status=None, namespace="wip", max_cross_product=1000,
            include_metadata=True, identity=admin,
        )
        assert not isinstance(response, StreamingResponse)
