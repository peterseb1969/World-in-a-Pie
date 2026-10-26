"""latest_only + status filters on each document's LATEST version.

The status filter used to run before the group-by-document, so
`latest_only=true&status=inactive` picked each document's highest *inactive*
version: superseded versions of live documents came back next to the deleted
ones, and total/pages counted them. Now an inactive filter applies after the
group. Active filters before it, which gives the same result (a document has
at most one active version) and keeps that call cheap. latest_only cannot be
combined with cursor; that combination used to ignore the cursor silently.
"""

import pytest
from httpx import AsyncClient

DOCS = "/api/document-store/documents"


def _person(national_id: str, first_name: str = "John") -> dict:
    return {
        "national_id": national_id, "first_name": first_name, "last_name": "Doe",
        "birth_date": "1990-01-15", "gender": "M", "age": 34,
    }


async def _post(client, auth_headers, *data: dict) -> list[dict]:
    resp = await client.post(DOCS, headers=auth_headers, json=[
        {"namespace": "wip", "template_id": "PERSON", "data": d} for d in data
    ])
    assert resp.status_code == 200, resp.text
    return resp.json()["results"]


async def _delete(client, auth_headers, document_id: str) -> None:
    resp = await client.request("DELETE", DOCS, headers=auth_headers, json=[{"id": document_id}])
    assert resp.json()["results"][0]["status"] == "deleted"


async def _list(client, auth_headers, **params) -> dict:
    resp = await client.get(DOCS, headers=auth_headers, params={
        "namespace": "wip", "template_value": "PERSON", **params,
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture
async def mixed(client: AsyncClient, auth_headers: dict) -> dict:
    """A live document with a superseded v1, and two soft-deleted documents."""
    live, gone1, gone2 = await _post(
        client, auth_headers,
        _person("953000001"), _person("953000002"), _person("953000003"),
    )
    await _post(client, auth_headers, _person("953000001", "Jane"))  # live → v2
    await _delete(client, auth_headers, gone1["document_id"])
    await _delete(client, auth_headers, gone2["document_id"])
    return {"live": live["document_id"], "deleted": {gone1["document_id"], gone2["document_id"]}}


@pytest.mark.asyncio
async def test_inactive_lists_only_deleted_documents(client, auth_headers, mixed):
    body = await _list(client, auth_headers, latest_only="true", status="inactive")
    assert {d["document_id"] for d in body["items"]} == mixed["deleted"]
    assert body["total"] == 2
    assert all(d["is_latest_version"] for d in body["items"])


@pytest.mark.asyncio
async def test_active_lists_the_live_document_at_its_latest_version(client, auth_headers, mixed):
    body = await _list(client, auth_headers, latest_only="true", status="active")
    assert [(d["document_id"], d["version"]) for d in body["items"]] == [(mixed["live"], 2)]


@pytest.mark.asyncio
async def test_without_latest_only_every_inactive_row_is_listed(client, auth_headers, mixed):
    # Unchanged: version rows, so the live document's superseded v1 is included.
    body = await _list(client, auth_headers, status="inactive")
    ids = [d["document_id"] for d in body["items"]]
    assert mixed["live"] in ids
    assert set(ids) >= mixed["deleted"]


@pytest.mark.asyncio
async def test_pagination_counts_the_filtered_result(client, auth_headers, mixed):
    first = await _list(client, auth_headers, latest_only="true", status="inactive", page_size=1)
    second = await _list(
        client, auth_headers, latest_only="true", status="inactive", page_size=1, page=2,
    )
    assert first["total"] == second["total"] == 2
    assert first["pages"] == 2
    assert {first["items"][0]["document_id"], second["items"][0]["document_id"]} == mixed["deleted"]


@pytest.mark.asyncio
async def test_latest_only_with_a_cursor_is_refused(client: AsyncClient, auth_headers: dict):
    resp = await client.get(DOCS, headers=auth_headers, params={
        "namespace": "wip", "latest_only": "true", "cursor": "0" * 24,
    })
    assert resp.status_code == 422
    assert "cursor" in resp.json()["detail"]
