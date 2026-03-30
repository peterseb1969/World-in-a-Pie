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
        json=[{
            "value": "DOC_STATUS",
            "label": "Document Status",
            "namespace": "wip",
            "case_sensitive": False
        }]
    )
    data = response.json()
    terminology_id = data["results"][0]["id"]

    # Fetch the full terminology so callers get the complete object
    get_response = await client.get(
        f"/api/def-store/terminologies/{terminology_id}",
        headers=auth_headers
    )
    return get_response.json()


@pytest.mark.asyncio
async def test_create_term(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test creating a new term."""
    terminology_id = test_terminology["terminology_id"]

    response = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "draft",
            "label": "Draft",
            "description": "Document is in draft state",
            "sort_order": 1
        }]
    )

    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == 1
    assert data["failed"] == 0
    assert data["results"][0]["status"] == "created"
    term_id = data["results"][0]["id"]
    assert term_id.startswith("T-")

    # Verify the created term via GET
    get_response = await client.get(
        f"/api/def-store/terms/{term_id}",
        headers=auth_headers
    )
    assert get_response.status_code == 200
    detail = get_response.json()
    assert detail["value"] == "draft"
    assert detail["terminology_id"] == terminology_id


@pytest.mark.asyncio
async def test_create_term_duplicate_code(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test that duplicate codes within terminology are rejected."""
    terminology_id = test_terminology["terminology_id"]

    # Create first term
    await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "approved",
            "label": "Approved"
        }]
    )

    # Try to create duplicate
    response = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "approved",
            "label": "Different"
        }]
    )

    assert response.status_code == 200
    data = response.json()
    assert data["results"][0]["status"] == "error"
    assert "already exists" in data["results"][0]["error"]


@pytest.mark.asyncio
async def test_list_terms(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test listing terms in a terminology."""
    terminology_id = test_terminology["terminology_id"]

    # Create some terms
    terms = [
        {"value": "draft", "label": "Draft", "sort_order": 1},
        {"value": "review", "label": "In Review", "sort_order": 2},
        {"value": "approved", "label": "Approved", "sort_order": 3},
    ]

    for term in terms:
        await client.post(
            f"/api/def-store/terminologies/{terminology_id}/terms",
            headers=auth_headers,
            json=[term]
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
async def test_get_term_by_value(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test finding a term by searching for its value."""
    terminology_id = test_terminology["terminology_id"]

    # Create term
    await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "archived",
            "label": "Archived"
        }]
    )

    # Search by value using the list endpoint
    response = await client.get(
        f"/api/def-store/terminologies/{terminology_id}/terms?search=archived",
        headers=auth_headers
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["value"] == "archived"


@pytest.mark.asyncio
async def test_update_term(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test updating a term."""
    terminology_id = test_terminology["terminology_id"]

    # Create term
    create_response = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "pending",
            "label": "Pending"
        }]
    )
    term_id = create_response.json()["results"][0]["id"]

    # Update (bulk endpoint: PUT /terms with array body)
    response = await client.put(
        "/api/def-store/terms",
        headers=auth_headers,
        json=[{
            "term_id": term_id,
            "label": "Pending Review",
            "description": "Waiting for review"
        }]
    )

    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == 1
    assert data["results"][0]["status"] == "updated"

    # Verify the update via GET
    get_response = await client.get(
        f"/api/def-store/terms/{term_id}",
        headers=auth_headers
    )
    assert get_response.json()["label"] == "Pending Review"
    assert get_response.json()["description"] == "Waiting for review"


@pytest.mark.asyncio
async def test_validate_value_valid(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test validating a valid value."""
    terminology_id = test_terminology["terminology_id"]

    # Create term
    await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "active",
            "label": "Active"
        }]
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
        json=[{
            "value": "active",
            "label": "Active"
        }]
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
        json=[{
            "value": "complete",
            "label": "Complete"
        }]
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
        {"value": "low", "label": "Low Priority", "sort_order": 1},
        {"value": "medium", "label": "Medium Priority", "sort_order": 2},
        {"value": "high", "label": "High Priority", "sort_order": 3},
    ]

    response = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=terms
    )

    assert response.status_code == 200
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
        json=[{
            "value": "obsolete",
            "label": "Obsolete"
        }]
    )
    term_id = create_response.json()["results"][0]["id"]

    # Delete (bulk endpoint: DELETE /terms with array body)
    response = await client.request(
        "DELETE",
        "/api/def-store/terms",
        headers=auth_headers,
        content='[{"id": "' + term_id + '"}]'
    )

    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == 1
    assert data["results"][0]["status"] == "deleted"

    # Verify it's inactive
    get_response = await client.get(
        f"/api/def-store/terms/{term_id}",
        headers=auth_headers
    )
    assert get_response.json()["status"] == "inactive"
