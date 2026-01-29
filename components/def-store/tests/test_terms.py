"""Tests for term API endpoints."""

import pytest
import pytest_asyncio
from httpx import AsyncClient


@pytest_asyncio.fixture
async def test_terminology(client: AsyncClient, auth_headers: dict):
    """Create a test terminology for term tests."""
    response = await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json={
            "code": "DOC_STATUS",
            "name": "Document Status",
            "case_sensitive": False
        }
    )
    return response.json()


@pytest.mark.asyncio
async def test_create_term(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test creating a new term."""
    terminology_id = test_terminology["terminology_id"]

    response = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json={
            "code": "DRAFT",
            "value": "draft",
            "label": "Draft",
            "description": "Document is in draft state",
            "sort_order": 1
        }
    )

    assert response.status_code == 201
    data = response.json()
    assert data["code"] == "DRAFT"
    assert data["value"] == "draft"
    assert data["term_id"].startswith("T-")
    assert data["terminology_id"] == terminology_id


@pytest.mark.asyncio
async def test_create_term_duplicate_code(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test that duplicate codes within terminology are rejected."""
    terminology_id = test_terminology["terminology_id"]

    # Create first term
    await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json={
            "code": "APPROVED",
            "value": "approved",
            "label": "Approved"
        }
    )

    # Try to create duplicate
    response = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json={
            "code": "APPROVED",
            "value": "different",
            "label": "Different"
        }
    )

    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


@pytest.mark.asyncio
async def test_list_terms(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test listing terms in a terminology."""
    terminology_id = test_terminology["terminology_id"]

    # Create some terms
    terms = [
        {"code": "DRAFT", "value": "draft", "label": "Draft", "sort_order": 1},
        {"code": "REVIEW", "value": "review", "label": "In Review", "sort_order": 2},
        {"code": "APPROVED", "value": "approved", "label": "Approved", "sort_order": 3},
    ]

    for term in terms:
        await client.post(
            f"/api/def-store/terminologies/{terminology_id}/terms",
            headers=auth_headers,
            json=term
        )

    # List terms
    response = await client.get(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 3


@pytest.mark.asyncio
async def test_get_term_by_code(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test getting a term by code."""
    terminology_id = test_terminology["terminology_id"]

    # Create term
    await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json={
            "code": "ARCHIVED",
            "value": "archived",
            "label": "Archived"
        }
    )

    # Get by code
    response = await client.get(
        f"/api/def-store/terminologies/{terminology_id}/terms/by-code/ARCHIVED",
        headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json()["code"] == "ARCHIVED"


@pytest.mark.asyncio
async def test_update_term(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test updating a term."""
    terminology_id = test_terminology["terminology_id"]

    # Create term
    create_response = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json={
            "code": "PENDING",
            "value": "pending",
            "label": "Pending"
        }
    )
    term_id = create_response.json()["term_id"]

    # Update (note: update endpoint is /terms/{term_id}, not under terminology)
    response = await client.put(
        f"/api/def-store/terms/{term_id}",
        headers=auth_headers,
        json={
            "label": "Pending Review",
            "description": "Waiting for review"
        }
    )

    assert response.status_code == 200
    assert response.json()["label"] == "Pending Review"
    assert response.json()["description"] == "Waiting for review"


@pytest.mark.asyncio
async def test_validate_value_valid(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test validating a valid value."""
    terminology_id = test_terminology["terminology_id"]

    # Create term
    await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json={
            "code": "ACTIVE",
            "value": "active",
            "label": "Active"
        }
    )

    # Validate (note: validate endpoint is at /api/def-store/validate)
    response = await client.post(
        "/api/def-store/validate",
        headers=auth_headers,
        json={
            "terminology_id": terminology_id,
            "value": "active"
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is True
    assert data["matched_term"]["value"] == "active"


@pytest.mark.asyncio
async def test_validate_value_invalid(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test validating an invalid value."""
    terminology_id = test_terminology["terminology_id"]

    # Create term
    await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json={
            "code": "ACTIVE",
            "value": "active",
            "label": "Active"
        }
    )

    # Validate invalid value
    response = await client.post(
        "/api/def-store/validate",
        headers=auth_headers,
        json={
            "terminology_id": terminology_id,
            "value": "invalid_status"
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is False
    assert data["matched_term"] is None


@pytest.mark.asyncio
async def test_validate_case_insensitive(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test case-insensitive validation."""
    terminology_id = test_terminology["terminology_id"]

    # Create term
    await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json={
            "code": "COMPLETE",
            "value": "complete",
            "label": "Complete"
        }
    )

    # Validate with different case
    response = await client.post(
        "/api/def-store/validate",
        headers=auth_headers,
        json={
            "terminology_id": terminology_id,
            "value": "COMPLETE"
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is True


@pytest.mark.asyncio
async def test_bulk_create_terms(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test creating multiple terms at once."""
    terminology_id = test_terminology["terminology_id"]

    terms = [
        {"code": "LOW", "value": "low", "label": "Low Priority", "sort_order": 1},
        {"code": "MEDIUM", "value": "medium", "label": "Medium Priority", "sort_order": 2},
        {"code": "HIGH", "value": "high", "label": "High Priority", "sort_order": 3},
    ]

    response = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms/bulk",
        headers=auth_headers,
        json={"terms": terms}
    )

    assert response.status_code == 201
    data = response.json()
    assert data["succeeded"] == 3
    assert data["failed"] == 0


@pytest.mark.asyncio
async def test_delete_term(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test soft-deleting a term."""
    terminology_id = test_terminology["terminology_id"]

    # Create term
    create_response = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json={
            "code": "OBSOLETE",
            "value": "obsolete",
            "label": "Obsolete"
        }
    )
    term_id = create_response.json()["term_id"]

    # Delete (note: delete endpoint is /terms/{term_id})
    response = await client.delete(
        f"/api/def-store/terms/{term_id}",
        headers=auth_headers
    )

    assert response.status_code == 200

    # Verify it's inactive
    get_response = await client.get(
        f"/api/def-store/terms/{term_id}",
        headers=auth_headers
    )
    assert get_response.json()["status"] == "inactive"
