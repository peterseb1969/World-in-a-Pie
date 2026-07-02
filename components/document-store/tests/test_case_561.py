"""CASE-561: `DeleteItem.force` was accepted-but-ignored on document deletion.

`force` is file-delete semantics (override the referenced-file guard);
document deletion has no reference gate to override, so the field was a
validated no-op — a guardrail that looked live but did nothing. Now the
route rejects it per-item with error_code=force_unsupported instead of
silently ignoring it.
"""

import pytest
from httpx import AsyncClient


async def _create_one(client: AsyncClient, auth_headers: dict, data: dict):
    payload = {"namespace": "wip", "template_id": "PERSON", "data": data}
    response = await client.post(
        "/api/document-store/documents", headers=auth_headers, json=[payload]
    )
    assert response.status_code == 200, f"Create failed: {response.text}"
    bulk = response.json()
    assert bulk["succeeded"] >= 1
    return bulk["results"][0]


@pytest.mark.asyncio
async def test_force_on_document_delete_rejected(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """force=true on DELETE /documents errors per-item; the doc survives."""
    data = sample_person_data.copy()
    data["national_id"] = "561000001"
    doc_id = (await _create_one(client, auth_headers, data))["document_id"]

    response = await client.request(
        "DELETE",
        "/api/document-store/documents",
        headers=auth_headers,
        json=[{"id": doc_id, "force": True}],
    )
    assert response.status_code == 200  # bulk-first: errors are per-item
    item = response.json()["results"][0]
    assert item["status"] == "error"
    assert item["error_code"] == "force_unsupported"

    # The rejected item must not have been deleted.
    get_resp = await client.get(
        f"/api/document-store/documents/{doc_id}", headers=auth_headers
    )
    assert get_resp.json()["status"] == "active"


@pytest.mark.asyncio
async def test_force_rejection_is_per_item(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """A mixed batch: the force item errors, the plain item deletes."""
    ids = []
    for i in range(2):
        data = sample_person_data.copy()
        data["national_id"] = f"56100001{i}"
        ids.append((await _create_one(client, auth_headers, data))["document_id"])

    response = await client.request(
        "DELETE",
        "/api/document-store/documents",
        headers=auth_headers,
        json=[{"id": ids[0], "force": True}, {"id": ids[1]}],
    )
    bulk = response.json()
    assert bulk["failed"] == 1
    assert bulk["succeeded"] == 1
    by_index = {r["index"]: r for r in bulk["results"]}
    assert by_index[0]["error_code"] == "force_unsupported"
    assert by_index[1]["status"] == "deleted"

    # explicit force=False behaves like omitting it
    response = await client.request(
        "DELETE",
        "/api/document-store/documents",
        headers=auth_headers,
        json=[{"id": ids[0], "force": False}],
    )
    assert response.json()["results"][0]["status"] == "deleted"
