"""Tests for terminology snapshot reconcile (POST /terminologies/{id}/reconcile).

Covers: the dry-run plan (and that it writes nothing), apply semantics
through the existing doors, idempotent re-runs, on_missing policies with
successors, the exists_inactive / duplicate_in_snapshot /
unknown_successor / invalid_names error rows, names as authoritative
substance, and the non-clearing rule for description/sort_order/parent.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient


@pytest_asyncio.fixture
async def reconcile_fixture(client: AsyncClient, auth_headers: dict):
    """A terminology with three active terms: KEEP, CHANGE, RETIRE."""
    resp = await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json=[{"value": "RTIS_FEED", "label": "RTiS Feed", "namespace": "wip"}],
    )
    tid = resp.json()["results"][0]["id"]
    create = await client.post(
        f"/api/def-store/terminologies/{tid}/terms",
        headers=auth_headers,
        json=[
            {"value": "KEEP", "label": "Keep", "description": "stays put"},
            {"value": "CHANGE", "label": "Old Label",
             "names": [{"value": "old-name"}]},
            {"value": "RETIRE", "label": "Retire"},
        ],
    )
    assert create.json()["failed"] == 0
    return {"terminology_id": tid,
            "ids": {r["value"]: r["id"] for r in create.json()["results"]}}


SNAPSHOT = [
    # KEEP: identical (description omitted -> non-clearing, stays)
    {"value": "KEEP", "label": "Keep"},
    # CHANGE: new label + new typed name
    {"value": "CHANGE", "label": "New Label",
     "names": [{"value": "new-name", "type": "SYNONYM"}]},
    # NEW: not in the terminology yet
    {"value": "NEW", "label": "New Term"},
]


async def _reconcile(client, auth_headers, tid, **overrides):
    body = {"terms": SNAPSHOT, **overrides}
    resp = await client.post(
        f"/api/def-store/terminologies/{tid}/reconcile",
        headers=auth_headers, json=body,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# =============================================================================
# Dry run
# =============================================================================

@pytest.mark.asyncio
async def test_dry_run_plans_and_writes_nothing(client: AsyncClient, auth_headers: dict, reconcile_fixture):
    tid = reconcile_fixture["terminology_id"]
    data = await _reconcile(
        client, auth_headers, tid,
        on_missing="deprecate", successors={"RETIRE": "NEW"},
    )
    assert data["dry_run"] is True
    assert data["counts"] == {
        "created": 1, "updated": 1, "unchanged": 1,
        "deprecated": 1, "missing_ignored": 0, "errors": 0,
    }
    rows = {r["value"]: r for r in data["results"]}
    # Unchanged terms are counted, not listed
    assert "KEEP" not in rows
    assert rows["NEW"]["status"] == "created"
    assert set(rows["CHANGE"]["details"]["changed"]) == {"label", "names"}
    assert rows["RETIRE"]["status"] == "deprecated"
    assert rows["RETIRE"]["details"]["successor"] == "NEW"

    # Nothing was written
    listing = await client.get(
        f"/api/def-store/terminologies/{tid}/terms", headers=auth_headers
    )
    values = {t["value"]: t for t in listing.json()["items"]}
    assert set(values) == {"KEEP", "CHANGE", "RETIRE"}
    assert values["CHANGE"]["label"] == "Old Label"
    assert values["RETIRE"]["status"] == "active"


# =============================================================================
# Apply
# =============================================================================

@pytest.mark.asyncio
async def test_apply_and_idempotent_rerun(client: AsyncClient, auth_headers: dict, reconcile_fixture):
    tid = reconcile_fixture["terminology_id"]
    data = await _reconcile(
        client, auth_headers, tid,
        dry_run=False, on_missing="deprecate", successors={"RETIRE": "NEW"},
    )
    assert data["dry_run"] is False
    assert data["counts"]["created"] == 1
    assert data["counts"]["updated"] == 1
    assert data["counts"]["deprecated"] == 1
    assert data["counts"]["errors"] == 0

    listing = await client.get(
        f"/api/def-store/terminologies/{tid}/terms", headers=auth_headers
    )
    values = {t["value"]: t for t in listing.json()["items"]}
    assert values["NEW"]["label"] == "New Term"
    assert values["CHANGE"]["label"] == "New Label"
    assert values["CHANGE"]["names"][0]["type"] == "SYNONYM"
    # Description was omitted from the snapshot — non-clearing keeps it
    assert values["KEEP"]["description"] == "stays put"
    retired = values["RETIRE"]
    assert retired["status"] == "deprecated"
    assert retired["replaced_by_term_id"] == values["NEW"]["term_id"]

    # Re-running the same snapshot is a no-op: RETIRE is no longer active,
    # so nothing is missing and nothing changes
    rerun = await _reconcile(
        client, auth_headers, tid,
        dry_run=False, on_missing="deprecate", successors={"RETIRE": "NEW"},
    )
    assert rerun["counts"] == {
        "created": 0, "updated": 0, "unchanged": 3,
        "deprecated": 0, "missing_ignored": 0, "errors": 0,
    }
    assert rerun["results"] == []


@pytest.mark.asyncio
async def test_on_missing_ignore_counts_only(client: AsyncClient, auth_headers: dict, reconcile_fixture):
    tid = reconcile_fixture["terminology_id"]
    data = await _reconcile(client, auth_headers, tid, dry_run=False)
    assert data["counts"]["missing_ignored"] == 1
    assert data["counts"]["deprecated"] == 0
    listing = await client.get(
        f"/api/def-store/terminologies/{tid}/terms", headers=auth_headers
    )
    values = {t["value"]: t for t in listing.json()["items"]}
    assert values["RETIRE"]["status"] == "active"


# =============================================================================
# Error rows
# =============================================================================

@pytest.mark.asyncio
async def test_exists_inactive_is_a_per_item_error(client: AsyncClient, auth_headers: dict, reconcile_fixture):
    tid = reconcile_fixture["terminology_id"]
    retire_id = reconcile_fixture["ids"]["RETIRE"]
    dep = await client.post(
        "/api/def-store/terms/deprecate",
        headers=auth_headers,
        json=[{"term_id": retire_id, "reason": "test"}],
    )
    assert dep.json()["failed"] == 0

    snapshot = [*SNAPSHOT, {"value": "RETIRE", "label": "Back again"}]
    data = await _reconcile(client, auth_headers, tid, terms=snapshot)
    rows = {r["value"]: r for r in data["results"]}
    assert rows["RETIRE"]["status"] == "error"
    assert rows["RETIRE"]["error_code"] == "exists_inactive"


@pytest.mark.asyncio
async def test_duplicate_and_bad_successor_and_invalid_names(client: AsyncClient, auth_headers: dict, reconcile_fixture):
    tid = reconcile_fixture["terminology_id"]
    snapshot = [
        {"value": "KEEP", "label": "Keep"},
        {"value": "KEEP", "label": "Keep again"},
        {"value": "BAD", "names": [{"value": "x", "type": "NOT_A_TYPE"}]},
    ]
    data = await _reconcile(
        client, auth_headers, tid, terms=snapshot,
        on_missing="deprecate", successors={"CHANGE": "NOWHERE"},
    )
    by_code = {r["error_code"]: r for r in data["results"] if r["status"] == "error"}
    assert "duplicate_in_snapshot" in by_code
    assert "invalid_names" in by_code
    assert "unknown_successor" in by_code
    assert by_code["unknown_successor"]["value"] == "CHANGE"
    assert data["counts"]["errors"] == 3
