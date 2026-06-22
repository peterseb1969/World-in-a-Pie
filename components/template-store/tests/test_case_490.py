"""CASE-490 — reactivate-version primitive: the symmetric inverse of deactivate.

Restores a soft-deleted (inactive) template version to active so documents
pinned to it can be updated again. `version` is required, the op is idempotent
on an already-active version, and a draft version is rejected (drafts are
activated, not reactivated).

The inactive starting state is set directly on the model rather than through
the deactivate route — deactivate runs a document-dependency check that needs
document-store, which isn't in this harness; here we isolate the reactivate
behavior.
"""

import pytest
from httpx import AsyncClient

from template_store.models.template import Template

NS = "wip"


async def _create(client: AsyncClient, auth_headers: dict, value: str) -> str:
    """Create an active v1 template, return its template_id."""
    resp = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json=[{
            "namespace": NS, "value": value, "label": value,
            "identity_fields": ["code"],
            "fields": [{"name": "code", "label": "Code", "type": "string", "mandatory": True}],
        }],
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["succeeded"] == 1, data
    return data["results"][0]["id"]


async def _set_status(template_id: str, version: int, status: str) -> None:
    t = await Template.find_one({"template_id": template_id, "version": version})
    assert t is not None
    t.status = status
    await t.save()


async def _status(client: AsyncClient, auth_headers: dict, template_id: str, version: int) -> str:
    resp = await client.get(
        f"/api/template-store/templates/{template_id}?namespace={NS}&version={version}",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["status"]


@pytest.mark.asyncio
async def test_reactivate_restores_inactive_version(client: AsyncClient, auth_headers: dict):
    tid = await _create(client, auth_headers, "CASE490_BASIC")
    await _set_status(tid, 1, "inactive")
    assert await _status(client, auth_headers, tid, 1) == "inactive"

    resp = await client.post(
        f"/api/template-store/templates/{tid}/reactivate?namespace={NS}&version=1",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "active"
    assert body["version"] == 1
    # the change is persisted, not just echoed
    assert await _status(client, auth_headers, tid, 1) == "active"


@pytest.mark.asyncio
async def test_reactivate_is_idempotent_on_active(client: AsyncClient, auth_headers: dict):
    tid = await _create(client, auth_headers, "CASE490_IDEM")  # v1 already active
    resp = await client.post(
        f"/api/template-store/templates/{tid}/reactivate?namespace={NS}&version=1",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"


@pytest.mark.asyncio
async def test_reactivate_unknown_version_400(client: AsyncClient, auth_headers: dict):
    tid = await _create(client, auth_headers, "CASE490_NOVER")
    resp = await client.post(
        f"/api/template-store/templates/{tid}/reactivate?namespace={NS}&version=99",
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text
    assert "not found" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_reactivate_rejects_draft(client: AsyncClient, auth_headers: dict):
    tid = await _create(client, auth_headers, "CASE490_DRAFT")
    await _set_status(tid, 1, "draft")
    resp = await client.post(
        f"/api/template-store/templates/{tid}/reactivate?namespace={NS}&version=1",
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text
    assert "draft" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_reactivate_requires_version(client: AsyncClient, auth_headers: dict):
    tid = await _create(client, auth_headers, "CASE490_REQ")
    resp = await client.post(
        f"/api/template-store/templates/{tid}/reactivate?namespace={NS}",
        headers=auth_headers,
    )
    assert resp.status_code == 422  # FastAPI: missing required query param


# --- namespace change-stamp (Piece 3: cache-coherence signal) ---------------

async def _stamp(client: AsyncClient, auth_headers: dict) -> str:
    resp = await client.get(
        f"/api/template-store/templates/stamp?namespace={NS}",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["namespace"] == NS
    return body["stamp"]


@pytest.mark.asyncio
async def test_stamp_changes_on_create_and_status_flip(client: AsyncClient, auth_headers: dict):
    before = await _stamp(client, auth_headers)

    # A create must move the stamp (count goes up).
    tid = await _create(client, auth_headers, "CASE490_STAMP")
    after_create = await _stamp(client, auth_headers)
    assert after_create != before

    # A real status flip moves max(updated_at). Set inactive directly (a raw
    # save does NOT bump updated_at), then REACTIVATE via the service path
    # (which does) — the stamp must move on that reactivate.
    await _set_status(tid, 1, "inactive")
    resp = await client.post(
        f"/api/template-store/templates/{tid}/reactivate?namespace={NS}&version=1",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    after_reactivate = await _stamp(client, auth_headers)
    assert after_reactivate != after_create


@pytest.mark.asyncio
async def test_stamp_requires_namespace(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/template-store/templates/stamp", headers=auth_headers)
    assert resp.status_code == 422  # namespace is a required query param
