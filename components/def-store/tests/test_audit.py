"""Tests for audit log API endpoints."""

import pytest
import pytest_asyncio
from httpx import AsyncClient


@pytest_asyncio.fixture
async def terminology_with_terms(client: AsyncClient, auth_headers: dict):
    """Create a terminology with several terms and operations for audit testing.

    Returns a dict with terminology_id and a list of term_ids.
    """
    # Create terminology
    create_resp = await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json=[{
            "value": "AUDIT_TEST",
            "label": "Audit Test Terminology",
            "namespace": "wip",
            "description": "Terminology for audit log testing"
        }]
    )
    data = create_resp.json()
    terminology_id = data["results"][0]["id"]

    # Create first term
    t1_resp = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "alpha",
            "label": "Alpha",
            "description": "First term"
        }]
    )
    term1_id = t1_resp.json()["results"][0]["id"]

    # Create second term
    t2_resp = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "beta",
            "label": "Beta",
            "description": "Second term"
        }]
    )
    term2_id = t2_resp.json()["results"][0]["id"]

    # Update first term (generates an "updated" audit entry)
    await client.put(
        "/api/def-store/terms",
        headers=auth_headers,
        json=[{
            "term_id": term1_id,
            "label": "Alpha Updated",
            "description": "First term updated"
        }]
    )

    # Delete second term (generates a soft-delete; note: delete_term
    # does NOT currently create an audit log entry via _create_audit_log,
    # but the term status changes)
    await client.request(
        "DELETE",
        "/api/def-store/terms",
        headers=auth_headers,
        json=[{"id": term2_id}]
    )

    return {
        "terminology_id": terminology_id,
        "term_ids": [term1_id, term2_id],
    }


# =============================================================================
# GET /api/def-store/audit/terms/{term_id}
# =============================================================================

@pytest.mark.asyncio
async def test_get_term_audit_log_after_create_and_update(
    client: AsyncClient, auth_headers: dict, terminology_with_terms: dict
):
    """Test retrieving audit log for a term that was created then updated."""
    term_id = terminology_with_terms["term_ids"][0]

    response = await client.get(
        f"/api/def-store/audit/terms/{term_id}",
        headers=auth_headers
    )

    assert response.status_code == 200
    data = response.json()

    # Should have at least 2 entries: created + updated
    assert data["total"] >= 2
    assert len(data["items"]) >= 2

    # Most recent entry first (sorted by changed_at desc)
    actions = [item["action"] for item in data["items"]]
    assert "created" in actions
    assert "updated" in actions

    # The most recent action should be "updated"
    assert data["items"][0]["action"] == "updated"
    assert data["items"][0]["term_id"] == term_id

    # Verify updated entry has changed_fields tracking
    updated_entry = data["items"][0]
    assert "label" in updated_entry["changed_fields"]
    assert updated_entry["previous_values"].get("label") == "Alpha"
    assert updated_entry["new_values"].get("label") == "Alpha Updated"


@pytest.mark.asyncio
async def test_get_term_audit_log_empty_for_unknown_term(
    client: AsyncClient, auth_headers: dict
):
    """Test that requesting audit log for a nonexistent term returns empty."""
    response = await client.get(
        "/api/def-store/audit/terms/T-NONEXISTENT",
        headers=auth_headers
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["items"] == []


# =============================================================================
# GET /api/def-store/audit/terminologies/{terminology_id}
# =============================================================================

@pytest.mark.asyncio
async def test_get_terminology_audit_log(
    client: AsyncClient, auth_headers: dict, terminology_with_terms: dict
):
    """Test retrieving audit log for a terminology (includes all term actions)."""
    terminology_id = terminology_with_terms["terminology_id"]

    response = await client.get(
        f"/api/def-store/audit/terminologies/{terminology_id}",
        headers=auth_headers
    )

    assert response.status_code == 200
    data = response.json()

    # Should contain entries for: terminology created, term1 created,
    # term2 created, term1 updated (at minimum)
    assert data["total"] >= 3
    assert len(data["items"]) >= 3

    # All entries should reference this terminology_id
    for item in data["items"]:
        assert item["terminology_id"] == terminology_id


@pytest.mark.asyncio
async def test_get_terminology_audit_log_filter_by_action(
    client: AsyncClient, auth_headers: dict, terminology_with_terms: dict
):
    """Test filtering terminology audit log by action type."""
    terminology_id = terminology_with_terms["terminology_id"]

    # Filter for only "created" actions
    response = await client.get(
        f"/api/def-store/audit/terminologies/{terminology_id}",
        headers=auth_headers,
        params={"action": "created"}
    )

    assert response.status_code == 200
    data = response.json()

    # Should have at least 3 "created" entries (terminology + 2 terms)
    assert data["total"] >= 3
    for item in data["items"]:
        assert item["action"] == "created"

    # Filter for only "updated" actions
    response_updated = await client.get(
        f"/api/def-store/audit/terminologies/{terminology_id}",
        headers=auth_headers,
        params={"action": "updated"}
    )

    assert response_updated.status_code == 200
    data_updated = response_updated.json()
    assert data_updated["total"] >= 1
    for item in data_updated["items"]:
        assert item["action"] == "updated"


# =============================================================================
# GET /api/def-store/audit (recent entries)
# =============================================================================

@pytest.mark.asyncio
async def test_get_recent_audit_entries(
    client: AsyncClient, auth_headers: dict, terminology_with_terms: dict
):
    """Test retrieving recent audit log entries across all terminologies."""
    response = await client.get(
        "/api/def-store/audit",
        headers=auth_headers
    )

    assert response.status_code == 200
    data = response.json()

    # Should have entries from the fixture setup
    assert data["total"] >= 3
    assert len(data["items"]) >= 3

    # Verify response structure
    first_item = data["items"][0]
    assert "term_id" in first_item
    assert "terminology_id" in first_item
    assert "action" in first_item
    assert "changed_at" in first_item


@pytest.mark.asyncio
async def test_get_recent_audit_entries_filter_by_action(
    client: AsyncClient, auth_headers: dict, terminology_with_terms: dict
):
    """Test filtering recent audit entries by action type."""
    response = await client.get(
        "/api/def-store/audit",
        headers=auth_headers,
        params={"action": "updated"}
    )

    assert response.status_code == 200
    data = response.json()

    # All returned entries should have action=updated
    for item in data["items"]:
        assert item["action"] == "updated"


@pytest.mark.asyncio
async def test_get_recent_audit_entries_no_results_for_nonexistent_action(
    client: AsyncClient, auth_headers: dict, terminology_with_terms: dict
):
    """Test that filtering by a non-matching action returns empty results."""
    response = await client.get(
        "/api/def-store/audit",
        headers=auth_headers,
        params={"action": "nonexistent_action"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["items"] == []


# =============================================================================
# PAGINATION
# =============================================================================

@pytest.mark.asyncio
async def test_audit_log_pagination(
    client: AsyncClient, auth_headers: dict
):
    """Test audit log pagination with page and page_size parameters."""
    # Create a terminology with enough terms to test pagination
    create_resp = await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json=[{
            "value": "PAGINATION_TEST",
            "label": "Pagination Test",
            "namespace": "wip"
        }]
    )
    terminology_id = create_resp.json()["results"][0]["id"]

    # Create 5 terms to generate 5 "created" audit entries (+ 1 for terminology)
    for i in range(5):
        await client.post(
            f"/api/def-store/terminologies/{terminology_id}/terms",
            headers=auth_headers,
            json=[{
                "value": f"page_term_{i}",
                "label": f"Page Term {i}"
            }]
        )

    # Get total entries for this terminology
    full_response = await client.get(
        f"/api/def-store/audit/terminologies/{terminology_id}",
        headers=auth_headers,
        params={"page_size": 100}
    )
    total = full_response.json()["total"]
    assert total >= 6  # 1 terminology created + 5 terms created

    # Request page 1 with page_size=2
    page1_response = await client.get(
        f"/api/def-store/audit/terminologies/{terminology_id}",
        headers=auth_headers,
        params={"page": 1, "page_size": 2}
    )
    page1_data = page1_response.json()
    assert page1_data["page"] == 1
    assert page1_data["page_size"] == 2
    assert len(page1_data["items"]) == 2
    assert page1_data["total"] >= 6

    # Request page 2 with page_size=2
    page2_response = await client.get(
        f"/api/def-store/audit/terminologies/{terminology_id}",
        headers=auth_headers,
        params={"page": 2, "page_size": 2}
    )
    page2_data = page2_response.json()
    assert page2_data["page"] == 2
    assert len(page2_data["items"]) == 2

    # Ensure page 1 and page 2 have different entries
    page1_times = [item["changed_at"] for item in page1_data["items"]]
    page2_times = [item["changed_at"] for item in page2_data["items"]]
    # Since sorted by changed_at desc, page 1 entries should be more recent
    # (or at least not identical sets)
    assert page1_times != page2_times or page1_data["items"] != page2_data["items"]


@pytest.mark.asyncio
async def test_audit_log_pagination_beyond_last_page(
    client: AsyncClient, auth_headers: dict
):
    """Test requesting a page beyond available data returns empty items."""
    # Create a single terminology to have at least 1 audit entry
    await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json=[{
            "value": "BEYOND_PAGE",
            "label": "Beyond Page Test",
            "namespace": "wip"
        }]
    )

    response = await client.get(
        "/api/def-store/audit",
        headers=auth_headers,
        params={"page": 999, "page_size": 50}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 999
    assert len(data["items"]) == 0
    # Total should still reflect the actual count
    assert data["total"] >= 1


# =============================================================================
# AUTHENTICATION
# =============================================================================

@pytest.mark.asyncio
async def test_audit_log_requires_authentication(client: AsyncClient):
    """Test that audit log endpoints require authentication."""
    # Term audit log
    response = await client.get("/api/def-store/audit/terms/0190b000-0000-7000-0000-000000000001")
    assert response.status_code == 401

    # Terminology audit log
    response = await client.get("/api/def-store/audit/terminologies/0190a000-0000-7000-0000-000000000001")
    assert response.status_code == 401

    # Recent audit log
    response = await client.get("/api/def-store/audit")
    assert response.status_code == 401


# =============================================================================
# AUDIT ENTRY STRUCTURE
# =============================================================================

@pytest.mark.asyncio
async def test_audit_entry_has_complete_structure(
    client: AsyncClient, auth_headers: dict
):
    """Test that audit log entries contain all expected fields."""
    # Create a terminology and a term
    create_resp = await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json=[{
            "value": "STRUCTURE_TEST",
            "label": "Structure Test",
            "namespace": "wip"
        }]
    )
    terminology_id = create_resp.json()["results"][0]["id"]

    term_resp = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "struct_term",
            "label": "Structure Term"
        }]
    )
    term_id = term_resp.json()["results"][0]["id"]

    # Fetch audit log for the term
    response = await client.get(
        f"/api/def-store/audit/terms/{term_id}",
        headers=auth_headers
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1

    entry = data["items"][0]
    # Verify all expected fields are present
    assert "term_id" in entry
    assert "terminology_id" in entry
    assert "action" in entry
    assert "changed_at" in entry
    assert "changed_by" in entry
    assert "changed_fields" in entry
    assert "previous_values" in entry
    assert "new_values" in entry
    assert "comment" in entry

    # For a "created" entry, new_values should have the term value
    created_entry = [e for e in data["items"] if e["action"] == "created"][0]
    assert created_entry["new_values"].get("value") == "struct_term"
    assert created_entry["term_id"] == term_id
    assert created_entry["terminology_id"] == terminology_id
