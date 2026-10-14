"""Tests for name-ambiguity visibility and the authoring-time collision guard.

Covers: validate reporting every bearer of a shared name (matches +
candidates), the single-hit response keeping its shape, collision warnings
on create/update (details.name_collisions), the unique_names terminology
flag upgrading warnings to per-item name_collision errors, intra-batch
collision detection, and case-sensitivity semantics of the guard.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient


@pytest_asyncio.fixture
async def ambiguity_terminology(client: AsyncClient, auth_headers: dict):
    """Case-insensitive terminology with two terms sharing a name."""
    resp = await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json=[{"value": "AMBIG_MOLECULES", "label": "Ambiguity", "namespace": "wip"}],
    )
    tid = resp.json()["results"][0]["id"]
    create = await client.post(
        f"/api/def-store/terminologies/{tid}/terms",
        headers=auth_headers,
        json=[
            {"value": "RO4876646", "names": [{"value": "bevacizumab", "type": "INN"}]},
            {"value": "RO4876646-000", "names": [{"value": "bevacizumab"}]},
            {"value": "plain", "names": [{"value": "unshared"}]},
        ],
    )
    body = create.json()
    assert body["failed"] == 0, body
    return {"terminology_id": tid,
            "ids": {r["value"]: r["id"] for r in body["results"]}}


# =============================================================================
# Ambiguity visible at validation
# =============================================================================

@pytest.mark.asyncio
async def test_validate_reports_all_bearers(client: AsyncClient, auth_headers: dict, ambiguity_terminology):
    resp = await client.post(
        "/api/def-store/validate",
        headers=auth_headers,
        json={"terminology_id": ambiguity_terminology["terminology_id"],
              "value": "bevacizumab"},
    )
    data = resp.json()
    assert data["valid"] is True
    assert data["matches"] == 2
    bearer_values = {c["term"]["value"] for c in data["candidates"]}
    assert bearer_values == {"RO4876646", "RO4876646-000"}
    # Each candidate carries the name that matched, with its type
    types = {c["term"]["value"]: c["matched_name"]["type"] for c in data["candidates"]}
    assert types["RO4876646"] == "INN"
    # matched_term is the first bearer — still deterministic, now visible
    assert data["matched_term"]["value"] in bearer_values


@pytest.mark.asyncio
async def test_validate_single_hit_keeps_shape(client: AsyncClient, auth_headers: dict, ambiguity_terminology):
    resp = await client.post(
        "/api/def-store/validate",
        headers=auth_headers,
        json={"terminology_id": ambiguity_terminology["terminology_id"],
              "value": "unshared"},
    )
    data = resp.json()
    assert data["valid"] is True
    assert data["matches"] == 1
    assert data["candidates"] == []

    # A value match is unambiguous by construction
    resp = await client.post(
        "/api/def-store/validate",
        headers=auth_headers,
        json={"terminology_id": ambiguity_terminology["terminology_id"],
              "value": "RO4876646"},
    )
    data = resp.json()
    assert data["matched_via"] == "value"
    assert data["matches"] == 1
    assert data["candidates"] == []


# =============================================================================
# Authoring-time guard: warnings by default
# =============================================================================

@pytest.mark.asyncio
async def test_create_collision_warns_in_details(client: AsyncClient, auth_headers: dict, ambiguity_terminology):
    tid = ambiguity_terminology["terminology_id"]
    resp = await client.post(
        f"/api/def-store/terminologies/{tid}/terms",
        headers=auth_headers,
        json=[{"value": "third", "names": [{"value": "Bevacizumab"}]}],
    )
    item = resp.json()["results"][0]
    assert item["status"] == "created"
    cols = item["details"]["name_collisions"]
    # Case-insensitive terminology: 'Bevacizumab' collides with both
    # existing bearers of 'bevacizumab'
    assert {c["conflicts_with_value"] for c in cols} == {"RO4876646", "RO4876646-000"}
    assert all(c["name"] == "Bevacizumab" for c in cols)


@pytest.mark.asyncio
async def test_create_name_colliding_with_value_warns(client: AsyncClient, auth_headers: dict, ambiguity_terminology):
    tid = ambiguity_terminology["terminology_id"]
    resp = await client.post(
        f"/api/def-store/terminologies/{tid}/terms",
        headers=auth_headers,
        json=[{"value": "fourth", "names": [{"value": "plain"}]}],
    )
    item = resp.json()["results"][0]
    assert item["status"] == "created"
    cols = item["details"]["name_collisions"]
    assert cols[0]["conflicts_with_value"] == "plain"
    assert cols[0]["via"] == "value"


@pytest.mark.asyncio
async def test_bulk_intra_batch_collision_warns(client: AsyncClient, auth_headers: dict, ambiguity_terminology):
    tid = ambiguity_terminology["terminology_id"]
    resp = await client.post(
        f"/api/def-store/terminologies/{tid}/terms",
        headers=auth_headers,
        json=[
            {"value": "b1", "names": [{"value": "shared-in-batch"}]},
            {"value": "b2", "names": [{"value": "shared-in-batch"}]},
        ],
    )
    body = resp.json()
    assert body["failed"] == 0
    # The LATER item carries the batch-collision warning
    second = body["results"][1]
    assert second["status"] == "created"
    cols = second["details"]["name_collisions"]
    assert cols[0]["via"] == "batch"
    assert cols[0]["conflicts_with_value"] == "b1"


@pytest.mark.asyncio
async def test_update_collision_warns(client: AsyncClient, auth_headers: dict, ambiguity_terminology):
    term_id = ambiguity_terminology["ids"]["plain"]
    resp = await client.put(
        "/api/def-store/terms",
        headers=auth_headers,
        json=[{"term_id": term_id, "names": [{"value": "unshared"}, {"value": "bevacizumab"}]}],
    )
    item = resp.json()["results"][0]
    assert item["status"] == "updated"
    cols = item["details"]["name_collisions"]
    assert {c["conflicts_with_value"] for c in cols} == {"RO4876646", "RO4876646-000"}


# =============================================================================
# unique_names: the guard becomes a per-item error
# =============================================================================

@pytest_asyncio.fixture
async def strict_terminology(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json=[{"value": "STRICT_NAMES", "label": "Strict", "namespace": "wip",
               "unique_names": True}],
    )
    tid = resp.json()["results"][0]["id"]
    seed = await client.post(
        f"/api/def-store/terminologies/{tid}/terms",
        headers=auth_headers,
        json=[{"value": "first", "names": [{"value": "taken"}]}],
    )
    assert seed.json()["failed"] == 0
    return tid


@pytest.mark.asyncio
async def test_unique_names_rejects_single_create(client: AsyncClient, auth_headers: dict, strict_terminology):
    resp = await client.post(
        f"/api/def-store/terminologies/{strict_terminology}/terms",
        headers=auth_headers,
        json=[{"value": "second", "names": [{"value": "taken"}]}],
    )
    item = resp.json()["results"][0]
    assert item["status"] == "error"
    assert item["error_code"] == "name_collision"
    assert item["details"]["name_collisions"][0]["conflicts_with_value"] == "first"


@pytest.mark.asyncio
async def test_unique_names_rejects_bulk_item(client: AsyncClient, auth_headers: dict, strict_terminology):
    resp = await client.post(
        f"/api/def-store/terminologies/{strict_terminology}/terms",
        headers=auth_headers,
        json=[
            {"value": "clean", "names": [{"value": "fresh"}]},
            {"value": "second", "names": [{"value": "Taken"}]},
        ],
    )
    body = resp.json()
    assert body["results"][0]["status"] == "created"
    bad = body["results"][1]
    assert bad["status"] == "error"
    assert bad["error_code"] == "name_collision"


@pytest.mark.asyncio
async def test_unique_names_rejects_update(client: AsyncClient, auth_headers: dict, strict_terminology):
    create = await client.post(
        f"/api/def-store/terminologies/{strict_terminology}/terms",
        headers=auth_headers,
        json=[{"value": "third"}],
    )
    term_id = create.json()["results"][0]["id"]
    resp = await client.put(
        "/api/def-store/terms",
        headers=auth_headers,
        json=[{"term_id": term_id, "names": [{"value": "taken"}]}],
    )
    item = resp.json()["results"][0]
    assert item["status"] == "error"
    assert item["error_code"] == "name_collision"


# =============================================================================
# Case-sensitivity semantics of the guard
# =============================================================================

@pytest.mark.asyncio
async def test_case_sensitive_terminology_allows_case_variants(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json=[{"value": "CS_NAMES", "label": "CS", "namespace": "wip",
               "case_sensitive": True, "unique_names": True}],
    )
    tid = resp.json()["results"][0]["id"]
    first = await client.post(
        f"/api/def-store/terminologies/{tid}/terms",
        headers=auth_headers,
        json=[{"value": "pH", "names": [{"value": "acidity"}]}],
    )
    assert first.json()["failed"] == 0
    # In a case-sensitive vocabulary, 'ACIDITY' is a different name —
    # no collision even under unique_names
    second = await client.post(
        f"/api/def-store/terminologies/{tid}/terms",
        headers=auth_headers,
        json=[{"value": "PH2", "names": [{"value": "ACIDITY"}]}],
    )
    assert second.json()["results"][0]["status"] == "created"
    # The exact string IS a collision
    third = await client.post(
        f"/api/def-store/terminologies/{tid}/terms",
        headers=auth_headers,
        json=[{"value": "PH3", "names": [{"value": "acidity"}]}],
    )
    assert third.json()["results"][0]["error_code"] == "name_collision"
