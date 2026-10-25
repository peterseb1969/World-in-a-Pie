"""Namespace isolation holds for batches as it does for single writes.

POST /documents sends one-item requests to create_document and longer ones
to bulk_create. Only the single path used to check a namespace's isolation
settings, so a strict namespace's allow-list could be bypassed by sending
two items. Both paths now run the same check, per item.
"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from registry.main import app as registry_app

DOCS = "/api/document-store/documents"
STRICT = "iso-strict"
OTHER = "iso-other"


async def _namespace(headers: dict, prefix: str, body: dict) -> None:
    async with AsyncClient(transport=ASGITransport(app=registry_app), base_url="http://registry") as reg:
        resp = await reg.put(f"/api/registry/namespaces/{prefix}", headers=headers, json=body)
    assert resp.status_code == 200, resp.text


async def _post(client: AsyncClient, headers: dict, items: list[dict]) -> list[dict]:
    resp = await client.post(DOCS, headers=headers, json=items)
    assert resp.status_code == 200, resp.text
    return resp.json()["results"]


def _chapter(namespace: str) -> dict:
    return {"template_id": "CHAPTER_Q", "namespace": namespace,
            "data": {"title": f"C-{uuid.uuid4().hex[:8]}"}}


def _holder(ref: str) -> dict:
    return {"template_id": "HOLDER_Q", "namespace": STRICT,
            "data": {"name": f"H-{uuid.uuid4().hex[:8]}", "ref": ref}}


@pytest.fixture
async def setup(client: AsyncClient, auth_headers: dict) -> dict:
    # STRICT may use the shared templates in "wip" and nothing else.
    await _namespace(auth_headers, STRICT, {
        "description": "isolation tests", "isolation_mode": "strict",
        "allowed_external_refs": ["wip"],
    })
    await _namespace(auth_headers, OTHER, {"description": "isolation tests"})
    [foreign] = await _post(client, auth_headers, [_chapter(OTHER)])
    [own] = await _post(client, auth_headers, [_chapter(STRICT)])
    assert foreign["status"] == own["status"] == "created", (foreign, own)
    return {"foreign": foreign["id"], "own": own["id"]}


@pytest.mark.asyncio
async def test_a_single_write_refuses_a_foreign_reference(client, auth_headers, setup):
    [item] = await _post(client, auth_headers, [_holder(setup["foreign"])])
    assert item["status"] == "error"
    assert "Cross-namespace reference violation" in item["error"]
    assert item["error_code"] == "reference_violation"


@pytest.mark.asyncio
async def test_a_batch_refuses_the_same_reference(client, auth_headers, setup):
    results = await _post(client, auth_headers, [_holder(setup["foreign"]), _holder(setup["foreign"])])
    assert [r["status"] for r in results] == ["error", "error"]
    assert all("Cross-namespace reference violation" in r["error"] for r in results)
    assert all(r["error_code"] == "reference_violation" for r in results)


@pytest.mark.asyncio
async def test_a_mixed_batch_refuses_only_the_violating_item(client, auth_headers, setup):
    results = await _post(client, auth_headers, [_holder(setup["foreign"]), _holder(setup["own"])])
    by_index = {r["index"]: r for r in results}
    assert by_index[0]["status"] == "error"
    assert by_index[1]["status"] == "created", by_index[1]
