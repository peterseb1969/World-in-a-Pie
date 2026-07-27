"""CASE-802: `created_at` is the entity's creation time, not the version's.

Every version is its own row. The version-writing path used to stamp
`created_at=now` on each successor, so a document appeared to be created again
on every edit: `created_at` drifted forward and became indistinguishable from
`updated_at` (they were both `now`), and any consumer sorting or displaying it
was silently wrong.

`versioned: false` templates never had the bug — they mutate the original row in
place and only touch `updated_at` — so these tests also pin that path, since the
fix is what makes versioned templates agree with it.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

API = "/api/document-store"


async def _post(client: AsyncClient, auth_headers: dict, items: list[dict]) -> list[dict]:
    resp = await client.post(f"{API}/documents", headers=auth_headers, json=items)
    assert resp.status_code == 200, resp.text
    return resp.json()["results"]


async def _get(client: AsyncClient, auth_headers: dict, doc_id: str) -> dict:
    resp = await client.get(f"{API}/documents/{doc_id}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _person(national_id: str, **extra) -> dict:
    return {
        "namespace": "wip",
        "template_id": "PERSON",
        "data": {"national_id": national_id, "first_name": "Ada", "last_name": "L", **extra},
    }


@pytest.mark.asyncio
async def test_created_at_survives_an_upsert_new_version(
    client: AsyncClient, auth_headers: dict,
):
    """Re-posting the same identity creates v2 — created_at must not move."""
    created = (await _post(client, auth_headers, [_person("800000801")]))[0]
    assert created["status"] == "created"
    doc_id = created["document_id"]

    v1 = await _get(client, auth_headers, doc_id)
    assert v1["version"] == 1
    original_created = v1["created_at"]

    updated = (await _post(client, auth_headers, [_person("800000801", first_name="Grace")]))[0]
    assert updated["status"] == "updated"

    v2 = await _get(client, auth_headers, doc_id)
    assert v2["version"] == 2
    assert v2["created_at"] == original_created, (
        "created_at moved on a new version — it must be the entity's creation time"
    )
    assert v2["updated_at"] >= original_created


@pytest.mark.asyncio
async def test_created_at_survives_patch(client: AsyncClient, auth_headers: dict):
    """PATCH writes a successor version; created_at must be v1's."""
    created = (await _post(client, auth_headers, [_person("800000802")]))[0]
    doc_id = created["document_id"]
    original_created = (await _get(client, auth_headers, doc_id))["created_at"]

    resp = await client.patch(
        f"{API}/documents",
        headers=auth_headers,
        json=[{"document_id": doc_id, "patch": {"first_name": "Patched"}}],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][0]["status"] == "updated", resp.text

    after = await _get(client, auth_headers, doc_id)
    assert after["version"] == 2
    assert after["data"]["first_name"] == "Patched"
    assert after["created_at"] == original_created


@pytest.mark.asyncio
async def test_created_at_and_updated_at_diverge_after_an_edit(
    client: AsyncClient, auth_headers: dict,
):
    """The two stamps used to be equal on every row by construction. After an
    edit they must differ, which is what makes `created_at` informative."""
    created = (await _post(client, auth_headers, [_person("800000803")]))[0]
    doc_id = created["document_id"]

    await _post(client, auth_headers, [_person("800000803", last_name="Changed")])
    after = await _get(client, auth_headers, doc_id)

    assert after["version"] == 2
    assert after["updated_at"] > after["created_at"], (
        "updated_at should have advanced past the entity's creation time"
    )


@pytest.mark.asyncio
async def test_version_history_keeps_per_version_write_times(
    client: AsyncClient, auth_headers: dict,
):
    """Carrying created_at forward must not flatten the audit trail: the
    versions endpoint still distinguishes the versions by updated_at."""
    created = (await _post(client, auth_headers, [_person("800000804")]))[0]
    doc_id = created["document_id"]
    await _post(client, auth_headers, [_person("800000804", first_name="Second")])

    resp = await client.get(f"{API}/documents/{doc_id}/versions", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    versions = resp.json()["versions"]
    assert len(versions) == 2

    by_version = {v["version"]: v for v in versions}
    # Same entity creation time on both rows...
    assert by_version[1]["created_at"] == by_version[2]["created_at"]
    # ...but the per-version write times remain distinguishable.
    assert by_version[2]["updated_at"] >= by_version[1]["updated_at"]


@pytest.mark.asyncio
async def test_versioned_false_created_at_still_stable(
    client: AsyncClient, auth_headers: dict,
):
    """The overwrite-in-place path was always correct; pin it so the fix keeps
    both template kinds agreeing."""
    note = {
        "namespace": "wip",
        "template_id": "LATEST_ONLY_NOTE",
        "data": {"note_id": "N-802", "body": "first"},
    }
    created = (await _post(client, auth_headers, [note]))[0]
    doc_id = created["document_id"]
    original_created = (await _get(client, auth_headers, doc_id))["created_at"]

    note["data"]["body"] = "second"
    await _post(client, auth_headers, [note])

    after = await _get(client, auth_headers, doc_id)
    assert after["version"] == 1  # overwrite in place
    assert after["data"]["body"] == "second"
    assert after["created_at"] == original_created
