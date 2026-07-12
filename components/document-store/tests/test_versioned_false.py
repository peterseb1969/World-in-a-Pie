"""Tests for the Phase-3 `versioned: false` template lifecycle.

When a template's `versioned` flag is False (set at template create
time, immutable after), document updates overwrite the existing
document in place instead of creating a new version. Same
document_id, same version number, fresh data + updated_at.

Exercised here against an entity template (LATEST_ONLY_NOTE) so the
test isolates the lifecycle change from the relationship-template
plumbing — same branch, simpler payload.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

API = "/api/document-store"


async def _create_doc(
    client: AsyncClient, auth_headers: dict, template_value: str, data: dict, **extra,
) -> dict:
    resp = await client.post(
        f"{API}/documents",
        headers=auth_headers,
        json=[{
            "namespace": "wip",
            "template_id": template_value,
            "data": data,
            **extra,
        }],
    )
    assert resp.status_code == 200, f"Create failed: {resp.text}"
    return resp.json()["results"][0]


# =============================================================================
# POST upsert path
# =============================================================================


@pytest.mark.asyncio
async def test_post_overwrites_in_place_for_versioned_false(
    client: AsyncClient, auth_headers: dict,
):
    """A second POST against a versioned=False template updates the
    existing document in place — same document_id, same version=1."""
    first = await _create_doc(client, auth_headers, "LATEST_ONLY_NOTE", {
        "note_id": "N-001",
        "body": "first body",
    })
    assert first["status"] == "created", first
    assert first["version"] == 1
    doc_id = first["document_id"]

    second = await _create_doc(client, auth_headers, "LATEST_ONLY_NOTE", {
        "note_id": "N-001",  # same identity
        "body": "updated body",
    })
    # is_new=False because identity matched; version still 1 (not bumped).
    # The overwrite changed data, so it must report "updated" — not the
    # no-op bucket. (The bulk envelope does not carry previous_version;
    # the status label is the observable contract here.)
    assert second["status"] == "updated", second
    assert second["document_id"] == doc_id, second
    assert second["version"] == 1, second
    assert second["is_new"] is False

    # Read back: the latest body should win, only one version exists.
    versions_resp = await client.get(
        f"{API}/documents/{doc_id}/versions",
        headers=auth_headers,
    )
    assert versions_resp.status_code == 200
    versions_body = versions_resp.json()
    versions = versions_body.get("versions") or versions_body.get("items") or []
    assert len(versions) == 1, f"Expected exactly 1 version, got {versions}"

    doc_resp = await client.get(
        f"{API}/documents/{doc_id}",
        headers=auth_headers,
    )
    assert doc_resp.status_code == 200
    body = doc_resp.json()
    assert body["data"]["body"] == "updated body"
    assert body["version"] == 1


@pytest.mark.asyncio
async def test_post_no_change_returns_unchanged_for_versioned_false(
    client: AsyncClient, auth_headers: dict,
):
    """Submitting identical data twice doesn't update — no-op preserved."""
    first = await _create_doc(client, auth_headers, "LATEST_ONLY_NOTE", {
        "note_id": "N-002", "body": "same",
    })
    second = await _create_doc(client, auth_headers, "LATEST_ONLY_NOTE", {
        "note_id": "N-002", "body": "same",
    })
    assert second["document_id"] == first["document_id"]
    assert second["version"] == 1
    assert second["is_new"] is False
    # A true no-op reports "unchanged" — same vocabulary as the bulk and
    # PATCH paths. "skipped" is reserved for items that were never
    # attempted (batch aborted after an earlier failure).
    assert second["status"] == "unchanged", second


@pytest.mark.asyncio
async def test_bulk_post_overwrites_in_place_for_versioned_false(
    client: AsyncClient, auth_headers: dict,
):
    """A 2+-item POST routes through bulk_create, which must honour
    versioned=False exactly like the single-item path: overwrite in
    place, version stays 1, exactly one version row — never a
    deactivate-and-insert-v2."""
    first = await _create_doc(client, auth_headers, "LATEST_ONLY_NOTE", {
        "note_id": "N-BULK", "body": "first body",
    })
    assert first["status"] == "created", first
    doc_id = first["document_id"]

    # Two items so the request takes the bulk path, not the single-item
    # short-circuit. Item 0 re-addresses the existing identity with
    # changed data; item 1 is an unrelated fresh identity.
    resp = await client.post(
        f"{API}/documents",
        headers=auth_headers,
        json=[
            {
                "namespace": "wip",
                "template_id": "LATEST_ONLY_NOTE",
                "data": {"note_id": "N-BULK", "body": "second body"},
            },
            {
                "namespace": "wip",
                "template_id": "LATEST_ONLY_NOTE",
                "data": {"note_id": "N-BULK-OTHER", "body": "unrelated"},
            },
        ],
    )
    assert resp.status_code == 200, resp.text
    results = {r["index"]: r for r in resp.json()["results"]}
    overwrite = results[0]
    assert overwrite["status"] == "updated", overwrite
    assert overwrite["document_id"] == doc_id, overwrite
    assert overwrite["version"] == 1, overwrite
    assert results[1]["status"] == "created", results[1]

    versions_resp = await client.get(
        f"{API}/documents/{doc_id}/versions",
        headers=auth_headers,
    )
    versions_body = versions_resp.json()
    versions = versions_body.get("versions") or versions_body.get("items") or []
    assert len(versions) == 1, f"Expected exactly 1 version, got {versions}"

    doc_resp = await client.get(
        f"{API}/documents/{doc_id}",
        headers=auth_headers,
    )
    body = doc_resp.json()
    assert body["data"]["body"] == "second body"
    assert body["version"] == 1


# =============================================================================
# PATCH path
# =============================================================================


@pytest.mark.asyncio
async def test_patch_overwrites_in_place_for_versioned_false(
    client: AsyncClient, auth_headers: dict,
):
    """PATCH against a versioned=False document updates in place too."""
    first = await _create_doc(client, auth_headers, "LATEST_ONLY_NOTE", {
        "note_id": "N-PATCH", "body": "before",
    })
    doc_id = first["document_id"]

    patch_resp = await client.patch(
        f"{API}/documents",
        headers=auth_headers,
        json=[{
            "document_id": doc_id,
            "patch": {"body": "after"},
        }],
    )
    assert patch_resp.status_code == 200, patch_resp.text
    patch_data = patch_resp.json()
    item = patch_data["results"][0]
    assert item["status"] == "updated", item
    assert item["version"] == 1, item

    versions_resp = await client.get(
        f"{API}/documents/{doc_id}/versions",
        headers=auth_headers,
    )
    versions_body = versions_resp.json()
    versions = versions_body.get("versions") or versions_body.get("items") or []
    assert len(versions) == 1, versions

    doc_resp = await client.get(
        f"{API}/documents/{doc_id}",
        headers=auth_headers,
    )
    assert doc_resp.json()["data"]["body"] == "after"


# =============================================================================
# Versioned=True still works (regression guard)
# =============================================================================


@pytest.mark.asyncio
async def test_versioned_true_still_creates_new_versions(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict,
):
    """PERSON template (versioned default True) must still bump version
    on update — this is the regression guard for the new branch."""
    first = await _create_doc(client, auth_headers, "PERSON", sample_person_data)
    assert first["version"] == 1
    doc_id = first["document_id"]

    updated = sample_person_data.copy()
    updated["first_name"] = "Renamed"
    second = await _create_doc(client, auth_headers, "PERSON", updated)
    assert second["document_id"] == doc_id
    assert second["version"] == 2, second
    assert second["is_new"] is False
