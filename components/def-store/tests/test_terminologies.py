"""Tests for terminology API endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_terminology(client: AsyncClient, auth_headers: dict):
    """Test creating a new terminology."""
    response = await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json={
            "value": "DOC_STATUS",
            "label": "Document Status",
            "description": "Status codes for documents",
            "case_sensitive": False,
            "allow_multiple": False,
            "extensible": True
        }
    )

    assert response.status_code == 201
    data = response.json()
    assert data["value"] == "DOC_STATUS"
    assert data["label"] == "Document Status"
    assert data["terminology_id"].startswith("TERM-")
    assert data["status"] == "active"
    assert data["term_count"] == 0


@pytest.mark.asyncio
async def test_create_terminology_duplicate_code(client: AsyncClient, auth_headers: dict):
    """Test that duplicate codes are rejected."""
    # Create first terminology
    await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json={
            "value": "PRIORITY",
            "label": "Priority Levels"
        }
    )

    # Try to create duplicate
    response = await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json={
            "value": "PRIORITY",
            "label": "Different Name"
        }
    )

    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_terminology_by_id(client: AsyncClient, auth_headers: dict):
    """Test getting a terminology by ID."""
    # Create terminology
    create_response = await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json={
            "value": "COUNTRIES",
            "label": "Country Codes"
        }
    )
    terminology_id = create_response.json()["terminology_id"]

    # Get by ID
    response = await client.get(
        f"/api/def-store/terminologies/{terminology_id}",
        headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json()["terminology_id"] == terminology_id
    assert response.json()["value"] == "COUNTRIES"


@pytest.mark.asyncio
async def test_get_terminology_by_value(client: AsyncClient, auth_headers: dict):
    """Test getting a terminology by value."""
    # Create terminology
    await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json={
            "value": "LANGUAGES",
            "label": "Language Codes"
        }
    )

    # Get by value
    response = await client.get(
        "/api/def-store/terminologies/by-value/LANGUAGES",
        headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json()["value"] == "LANGUAGES"


@pytest.mark.asyncio
async def test_list_terminologies(client: AsyncClient, auth_headers: dict):
    """Test listing terminologies."""
    # Create some terminologies
    for i in range(3):
        await client.post(
            "/api/def-store/terminologies",
            headers=auth_headers,
            json={
                "value": f"TEST_{i}",
                "label": f"Test Terminology {i}"
            }
        )

    # List all
    response = await client.get(
        "/api/def-store/terminologies",
        headers=auth_headers
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 3
    assert len(data["items"]) >= 3


@pytest.mark.asyncio
async def test_update_terminology(client: AsyncClient, auth_headers: dict):
    """Test updating a terminology."""
    # Create terminology
    create_response = await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json={
            "value": "OLD_VALUE",
            "label": "Old Name"
        }
    )
    terminology_id = create_response.json()["terminology_id"]

    # Update
    response = await client.put(
        f"/api/def-store/terminologies/{terminology_id}",
        headers=auth_headers,
        json={
            "label": "New Name",
            "description": "Added description"
        }
    )

    assert response.status_code == 200
    assert response.json()["label"] == "New Name"
    assert response.json()["description"] == "Added description"


@pytest.mark.asyncio
async def test_delete_terminology(client: AsyncClient, auth_headers: dict):
    """Test soft-deleting a terminology."""
    # Create terminology
    create_response = await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json={
            "value": "TO_DELETE",
            "label": "Will Be Deleted"
        }
    )
    terminology_id = create_response.json()["terminology_id"]

    # Delete
    response = await client.delete(
        f"/api/def-store/terminologies/{terminology_id}",
        headers=auth_headers
    )

    assert response.status_code == 200

    # Verify it's inactive
    get_response = await client.get(
        f"/api/def-store/terminologies/{terminology_id}",
        headers=auth_headers
    )
    assert get_response.json()["status"] == "inactive"


@pytest.mark.asyncio
async def test_authentication_required(client: AsyncClient):
    """Test that authentication is required."""
    response = await client.get("/api/def-store/terminologies")
    assert response.status_code == 401

    response = await client.get(
        "/api/def-store/terminologies",
        headers={"X-API-Key": "wrong_key"}
    )
    assert response.status_code == 403
