"""Regression coverage for CASE-465 — idempotent bootstrap for def-store kinds.

Terminologies (and the single-item term path) gain the same `on_conflict`
semantics templates already have: a bootstrap that died mid-run is recovered
by running it again.

Contract under test:

  - Default mode unchanged in shape, but duplicate errors now carry
    error_code='already_exists' (callers branch on the code, never the
    message string — wip://conventions).
  - on_conflict=validate: identical re-create → status='unchanged' with the
    existing ID; any config difference → status='error',
    error_code='incompatible_config', details.changed naming the fields.
  - 'unchanged' counts toward `succeeded` (a re-run reports success).
  - Invalid on_conflict value → 400.
  - Bulk term path (2+ items) keeps its skip-duplicates behavior.
"""

import pytest
from httpx import AsyncClient

TERMINOLOGY = {
    "value": "C465_STATUS",
    "label": "Case 465 Status",
    "namespace": "wip",
    "description": "Idempotent bootstrap test vocabulary",
    "extensible": True,
}


async def _create_terminology(client, auth_headers, **params):
    return await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        params=params,
        json=[dict(TERMINOLOGY)],
    )


@pytest.mark.asyncio
async def test_duplicate_terminology_default_mode_has_error_code(
    client: AsyncClient, auth_headers: dict
):
    first = await _create_terminology(client, auth_headers)
    assert first.json()["results"][0]["status"] == "created"

    dup = await _create_terminology(client, auth_headers)
    item = dup.json()["results"][0]
    assert item["status"] == "error"
    assert item["error_code"] == "already_exists"
    assert "already exists" in item["error"]


@pytest.mark.asyncio
async def test_identical_terminology_revalidate_is_unchanged(
    client: AsyncClient, auth_headers: dict
):
    first = await _create_terminology(client, auth_headers)
    created_id = first.json()["results"][0]["id"]

    rerun = await _create_terminology(client, auth_headers, on_conflict="validate")
    data = rerun.json()
    item = data["results"][0]
    assert item["status"] == "unchanged"
    assert item["id"] == created_id
    assert data["succeeded"] == 1
    assert data["failed"] == 0


@pytest.mark.asyncio
async def test_divergent_terminology_revalidate_is_incompatible_config(
    client: AsyncClient, auth_headers: dict
):
    await _create_terminology(client, auth_headers)

    changed = dict(TERMINOLOGY, label="Renamed Label", extensible=False)
    rerun = await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        params={"on_conflict": "validate"},
        json=[changed],
    )
    item = rerun.json()["results"][0]
    assert item["status"] == "error"
    assert item["error_code"] == "incompatible_config"
    assert sorted(item["details"]["changed"]) == ["extensible", "label"]


@pytest.mark.asyncio
async def test_invalid_on_conflict_value_is_400(
    client: AsyncClient, auth_headers: dict
):
    resp = await _create_terminology(client, auth_headers, on_conflict="upsert")
    assert resp.status_code == 400
    assert "on_conflict" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_mixed_batch_validate_creates_and_skips(
    client: AsyncClient, auth_headers: dict
):
    """A re-run with one new vocabulary added creates only the new one."""
    await _create_terminology(client, auth_headers)

    batch = [
        dict(TERMINOLOGY),
        {"value": "C465_KIND", "label": "Case 465 Kind", "namespace": "wip"},
    ]
    rerun = await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        params={"on_conflict": "validate"},
        json=batch,
    )
    data = rerun.json()
    statuses = [r["status"] for r in data["results"]]
    assert statuses == ["unchanged", "created"]
    assert data["succeeded"] == 2
    assert data["failed"] == 0


# ── Terms (single-item path) ────────────────────────────────────────


async def _setup_terminology_with_term(client, auth_headers):
    resp = await _create_terminology(client, auth_headers)
    terminology_id = resp.json()["results"][0]["id"]
    term = {"value": "OPEN", "label": "Open", "aliases": ["open"]}
    created = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[term],
    )
    assert created.json()["results"][0]["status"] == "created"
    return terminology_id, term, created.json()["results"][0]["id"]


@pytest.mark.asyncio
async def test_duplicate_term_default_mode_has_error_code(
    client: AsyncClient, auth_headers: dict
):
    terminology_id, term, _ = await _setup_terminology_with_term(client, auth_headers)

    dup = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[term],
    )
    item = dup.json()["results"][0]
    assert item["status"] == "error"
    assert item["error_code"] == "already_exists"


@pytest.mark.asyncio
async def test_identical_term_revalidate_is_unchanged(
    client: AsyncClient, auth_headers: dict
):
    terminology_id, term, term_id = await _setup_terminology_with_term(
        client, auth_headers
    )

    rerun = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        params={"on_conflict": "validate"},
        json=[term],
    )
    data = rerun.json()
    item = data["results"][0]
    assert item["status"] == "unchanged"
    assert item["id"] == term_id
    assert data["succeeded"] == 1


@pytest.mark.asyncio
async def test_divergent_term_revalidate_is_incompatible_config(
    client: AsyncClient, auth_headers: dict
):
    terminology_id, term, _ = await _setup_terminology_with_term(client, auth_headers)

    changed = dict(term, label="Reopened", aliases=["open", "reopened"])
    rerun = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        params={"on_conflict": "validate"},
        json=[changed],
    )
    item = rerun.json()["results"][0]
    assert item["status"] == "error"
    assert item["error_code"] == "incompatible_config"
    assert sorted(item["details"]["changed"]) == ["aliases", "label"]


@pytest.mark.asyncio
async def test_bulk_term_path_still_skips_duplicates(
    client: AsyncClient, auth_headers: dict
):
    """The 2+ item path keeps its CASE-336-pinned skip behavior regardless
    of on_conflict."""
    terminology_id, term, _ = await _setup_terminology_with_term(client, auth_headers)

    batch = [dict(term), {"value": "CLOSED", "label": "Closed"}]
    resp = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        params={"on_conflict": "validate"},
        json=batch,
    )
    data = resp.json()
    statuses = [r["status"] for r in data["results"]]
    assert statuses == ["skipped", "created"]
    assert data["failed"] == 0
