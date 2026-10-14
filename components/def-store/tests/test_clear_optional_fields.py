"""Explicit null clears an optional field; omitted still means unchanged.

RFC 7396 alignment for term and terminology updates: an explicitly
submitted null clears description/label/parent_term_id (and resets
sort_order to 0), detected via Pydantic's model_fields_set so an omitted
field keeps its value — full backward compatibility. value and names
reject explicit null loudly. The snapshot reconcile speaks the same
vocabulary: an explicit null in a snapshot item clears, idempotently,
and also normalizes the historical "" workaround.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient


@pytest_asyncio.fixture
async def clear_fixture(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json=[{"value": "CLEARABLE", "label": "Clearable", "namespace": "wip",
               "description": "terminology description"}],
    )
    tid = resp.json()["results"][0]["id"]
    create = await client.post(
        f"/api/def-store/terminologies/{tid}/terms",
        headers=auth_headers,
        json=[
            {"value": "parent"},
            {"value": "child", "label": "Child Label",
             "description": "to be cleared", "sort_order": 7},
        ],
    )
    ids = {r["value"]: r["id"] for r in create.json()["results"]}
    # Give child a parent so parent_term_id clearing is observable
    link = await client.put(
        "/api/def-store/terms",
        headers=auth_headers,
        json=[{"term_id": ids["child"], "parent_term_id": ids["parent"]}],
    )
    assert link.json()["failed"] == 0
    return {"terminology_id": tid, "ids": ids}


async def _get_term(client, auth_headers, term_id):
    resp = await client.get(f"/api/def-store/terms/{term_id}", headers=auth_headers)
    return resp.json()


@pytest.mark.asyncio
async def test_explicit_null_clears_omitted_keeps(client: AsyncClient, auth_headers: dict, clear_fixture):
    child = clear_fixture["ids"]["child"]

    # Omitted fields: nothing changes
    resp = await client.put(
        "/api/def-store/terms",
        headers=auth_headers,
        json=[{"term_id": child, "sort_order": 9}],
    )
    assert resp.json()["failed"] == 0
    term = await _get_term(client, auth_headers, child)
    assert term["description"] == "to be cleared"
    assert term["parent_term_id"] == clear_fixture["ids"]["parent"]

    # Explicit nulls: cleared / reset
    resp = await client.put(
        "/api/def-store/terms",
        headers=auth_headers,
        json=[{"term_id": child, "description": None, "label": None,
               "sort_order": None, "parent_term_id": None}],
    )
    assert resp.json()["failed"] == 0, resp.json()
    term = await _get_term(client, auth_headers, child)
    assert term["description"] is None
    # label cleared in storage; the response serves the documented fallback
    assert term["label"] == "child"
    assert term["sort_order"] == 0
    assert term["parent_term_id"] is None


@pytest.mark.asyncio
async def test_empty_string_workaround_normalizes(client: AsyncClient, auth_headers: dict, clear_fixture):
    child = clear_fixture["ids"]["child"]
    await client.put(
        "/api/def-store/terms",
        headers=auth_headers,
        json=[{"term_id": child, "description": ""}],
    )
    resp = await client.put(
        "/api/def-store/terms",
        headers=auth_headers,
        json=[{"term_id": child, "description": None}],
    )
    assert resp.json()["failed"] == 0
    term = await _get_term(client, auth_headers, child)
    assert term["description"] is None


@pytest.mark.asyncio
async def test_value_and_names_reject_explicit_null(client: AsyncClient, auth_headers: dict, clear_fixture):
    child = clear_fixture["ids"]["child"]
    for payload in ({"value": None}, {"names": None}):
        resp = await client.put(
            "/api/def-store/terms",
            headers=auth_headers,
            json=[{"term_id": child, **payload}],
        )
        item = resp.json()["results"][0]
        assert item["status"] == "error", payload
        assert "cannot be" in item["error"]


@pytest.mark.asyncio
async def test_terminology_description_clears(client: AsyncClient, auth_headers: dict, clear_fixture):
    tid = clear_fixture["terminology_id"]
    resp = await client.put(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json=[{"terminology_id": tid, "description": None}],
    )
    assert resp.json()["failed"] == 0
    got = await client.get(f"/api/def-store/terminologies/{tid}", headers=auth_headers)
    assert got.json()["description"] is None


@pytest.mark.asyncio
async def test_reconcile_explicit_null_clears_idempotently(client: AsyncClient, auth_headers: dict, clear_fixture):
    tid = clear_fixture["terminology_id"]
    snapshot = {
        "terms": [
            {"value": "parent"},
            # explicit null clears; label omitted -> falls back to value
            {"value": "child", "description": None, "parent_term_id": None},
        ],
        "dry_run": False,
    }
    resp = await client.post(
        f"/api/def-store/terminologies/{tid}/reconcile",
        headers=auth_headers, json=snapshot,
    )
    body = resp.json()
    assert body["counts"]["errors"] == 0, body
    assert body["counts"]["updated"] == 1
    rows = {r["value"]: r for r in body["results"]}
    assert set(rows["child"]["details"]["changed"]) >= {"description", "parent_term_id"}

    term = await _get_term(client, auth_headers, clear_fixture["ids"]["child"])
    assert term["description"] is None
    assert term["parent_term_id"] is None

    # Idempotent: the same snapshot re-run changes nothing
    rerun = await client.post(
        f"/api/def-store/terminologies/{tid}/reconcile",
        headers=auth_headers, json=snapshot,
    )
    assert rerun.json()["counts"]["updated"] == 0
    assert rerun.json()["counts"]["unchanged"] == 2


@pytest.mark.asyncio
async def test_reconcile_omitted_still_keeps(client: AsyncClient, auth_headers: dict, clear_fixture):
    tid = clear_fixture["terminology_id"]
    resp = await client.post(
        f"/api/def-store/terminologies/{tid}/reconcile",
        headers=auth_headers,
        json={"terms": [{"value": "parent"}, {"value": "child", "sort_order": 7}],
              "dry_run": False},
    )
    assert resp.json()["counts"]["errors"] == 0
    term = await _get_term(client, auth_headers, clear_fixture["ids"]["child"])
    # description and parent were never mentioned — kept
    assert term["description"] == "to be cleared"
    assert term["parent_term_id"] == clear_fixture["ids"]["parent"]
