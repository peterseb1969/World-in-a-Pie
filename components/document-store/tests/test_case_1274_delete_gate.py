"""A bulk write's gate covers every namespace a document id is held in.

A document_id is unique only within its namespace. DELETE and PATCH
/documents asked write permission for one namespace per id (the last row the
pre-pass read), and DELETE then read the document by id alone and treated a
namespace it never asked about as allowed: with the same id in a namespace
the caller cannot write, the write could land there. The gate now asks for
every namespace holding the id, refuses an item when any of them refuses, and
reads a namespace it never asked about as not found. Its pre-pass reads one
row per (document_id, namespace), not every version row.
"""

import pytest
from httpx import AsyncClient

from document_store.models.document import Document, DocumentStatus

from .test_case_1140 import READER, URL, _create


async def _same_id_in_closed(doc_id: str) -> None:
    await Document(
        namespace="xns-closed", document_id=doc_id, template_id="T1274",
        template_version=1, template_value="T1274", identity_hash=f"h-{doc_id}",
        version=1, data={"x": 1}, status=DocumentStatus.ACTIVE,
    ).insert()


async def _active(doc_id: str, namespace: str) -> bool:
    return await Document.find_one({
        "document_id": doc_id, "namespace": namespace, "status": DocumentStatus.ACTIVE.value,
    }) is not None


@pytest.mark.asyncio
async def test_delete_is_refused_when_another_namespace_holding_the_id_refuses(
    client: AsyncClient, auth_headers,
):
    [doc_id] = await _create(client, auth_headers, "127420001")
    await _same_id_in_closed(doc_id)
    resp = await client.request("DELETE", URL, headers=READER, json=[{"id": doc_id}])
    assert resp.status_code == 200, resp.text
    [result] = resp.json()["results"]
    assert result["status"] == "error", result
    assert result["error_code"] == "forbidden", result
    assert await _active(doc_id, "xns-closed")
    assert await _active(doc_id, "wip")


@pytest.mark.asyncio
async def test_patch_is_refused_when_another_namespace_holding_the_id_refuses(
    client: AsyncClient, auth_headers,
):
    [doc_id] = await _create(client, auth_headers, "127420002")
    await _same_id_in_closed(doc_id)
    resp = await client.patch(URL, headers=READER, json=[
        {"document_id": doc_id, "patch": {"first_name": "Jane"}},
    ])
    assert resp.status_code == 200, resp.text
    [result] = resp.json()["results"]
    assert result["error_code"] == "forbidden", result
    row = await Document.find_one({"document_id": doc_id, "namespace": "xns-closed"})
    assert (row.version, row.data) == (1, {"x": 1})
