"""A batch writes each document into the namespace its item names (CASE-1136).

POST /documents reads the namespace from each item. One-item requests take
the single path; longer ones the bulk path, which used to write every
document of the batch into the first item's namespace. The same payload
then behaved differently by batch size, and the response could not show it.
"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from registry.main import app as registry_app

DOCS = "/api/document-store/documents"
NS_A = "mixed-a"
NS_B = "mixed-b"


async def _namespace(headers: dict, prefix: str) -> None:
    async with AsyncClient(transport=ASGITransport(app=registry_app), base_url="http://registry") as reg:
        resp = await reg.put(f"/api/registry/namespaces/{prefix}", headers=headers,
                             json={"description": "mixed-namespace batch tests"})
    assert resp.status_code == 200, resp.text


async def _post(client: AsyncClient, headers: dict, items: list[dict], **params) -> list[dict]:
    resp = await client.post(DOCS, headers=headers, json=items, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()["results"]


def _chapter(namespace: str, title: str | None = None) -> dict:
    return {"template_id": "CHAPTER_Q", "namespace": namespace,
            "data": {"title": title or f"C-{uuid.uuid4().hex[:8]}"}}


async def _namespace_of(client: AsyncClient, headers: dict, document_id: str) -> str:
    resp = await client.get(f"{DOCS}/{document_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["namespace"]


@pytest.fixture
async def namespaces(auth_headers: dict) -> None:
    await _namespace(auth_headers, NS_A)
    await _namespace(auth_headers, NS_B)


@pytest.mark.asyncio
async def test_each_item_is_written_into_its_own_namespace(client, auth_headers, namespaces):
    results = await _post(client, auth_headers, [_chapter(NS_A), _chapter(NS_B), _chapter(NS_A)])

    assert [r["index"] for r in results] == [0, 1, 2]
    assert [r["status"] for r in results] == ["created"] * 3
    placed = [await _namespace_of(client, auth_headers, r["document_id"]) for r in results]
    assert placed == [NS_A, NS_B, NS_A]


@pytest.mark.asyncio
async def test_a_later_write_of_the_same_identity_finds_the_batch_written_document(
    client, auth_headers, namespaces,
):
    """The second item's identity is registered in its own namespace, so a
    later one-item write of the same identity there is an update of that
    document, not a second one."""
    title = f"C-{uuid.uuid4().hex[:8]}"
    first = await _post(client, auth_headers, [_chapter(NS_A), _chapter(NS_B, title)])
    again = await _post(client, auth_headers, [_chapter(NS_B, title)])

    assert again[0]["document_id"] == first[1]["document_id"]
    assert again[0]["status"] in ("updated", "unchanged")


@pytest.mark.asyncio
async def test_stopping_at_the_first_error_skips_every_later_item(client, auth_headers, namespaces):
    """continue_on_error=false stops the request at its first failing item,
    in submission order, whichever namespace the later items name."""
    broken = {"template_id": "CHAPTER_Q", "namespace": NS_A, "data": {}}
    results = await _post(
        client, auth_headers,
        [_chapter(NS_B), broken, _chapter(NS_B), _chapter(NS_A)],
        continue_on_error="false",
    )

    assert [r["status"] for r in results] == ["created", "error", "skipped", "skipped"]


@pytest.mark.asyncio
async def test_an_empty_batch_is_an_empty_response(client, auth_headers):
    resp = await client.post(DOCS, headers=auth_headers, json=[])

    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 0 and resp.json()["results"] == []
