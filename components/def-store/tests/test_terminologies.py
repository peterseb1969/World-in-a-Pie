"""Tests for terminology API endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_terminology(client: AsyncClient, auth_headers: dict):
    """Test creating a new terminology."""
    response = await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json=[{
            "value": "DOC_STATUS",
            "label": "Document Status",
            "namespace": "wip",
            "description": "Status codes for documents",
            "case_sensitive": False,
            "allow_multiple": False,
            "extensible": True
        }]
    )

    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == 1
    assert data["failed"] == 0
    assert data["results"][0]["status"] == "created"
    terminology_id = data["results"][0]["id"]
    assert terminology_id  # Real Registry assigns the ID format

    # Verify the created entity via GET
    get_response = await client.get(
        f"/api/def-store/terminologies/{terminology_id}",
        headers=auth_headers
    )
    assert get_response.status_code == 200
    detail = get_response.json()
    assert detail["value"] == "DOC_STATUS"
    assert detail["label"] == "Document Status"
    assert detail["status"] == "active"
    assert detail["term_count"] == 0


@pytest.mark.asyncio
async def test_create_terminology_duplicate_code(client: AsyncClient, auth_headers: dict):
    """Test that duplicate codes are rejected."""
    # Create first terminology
    await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json=[{
            "value": "PRIORITY",
            "label": "Priority Levels",
            "namespace": "wip"
        }]
    )

    # Try to create duplicate
    response = await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json=[{
            "value": "PRIORITY",
            "label": "Different Name",
            "namespace": "wip"
        }]
    )

    assert response.status_code == 200
    data = response.json()
    assert data["results"][0]["status"] == "error"
    assert "already exists" in data["results"][0]["error"]


@pytest.mark.asyncio
async def test_get_terminology_by_id(client: AsyncClient, auth_headers: dict):
    """Test getting a terminology by ID."""
    # Create terminology
    create_response = await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json=[{
            "value": "COUNTRIES",
            "label": "Country Codes",
            "namespace": "wip"
        }]
    )
    terminology_id = create_response.json()["results"][0]["id"]

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
        json=[{
            "value": "LANGUAGES",
            "label": "Language Codes",
            "namespace": "wip"
        }]
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
            json=[{
                "value": f"TEST_{i}",
                "label": f"Test Terminology {i}",
                "namespace": "wip"
            }]
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
        json=[{
            "value": "OLD_VALUE",
            "label": "Old Name",
            "namespace": "wip"
        }]
    )
    terminology_id = create_response.json()["results"][0]["id"]

    # Update
    response = await client.put(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json=[{
            "terminology_id": terminology_id,
            "label": "New Name",
            "description": "Added description"
        }]
    )

    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == 1
    assert data["results"][0]["status"] == "updated"

    # Verify the update via GET
    get_response = await client.get(
        f"/api/def-store/terminologies/{terminology_id}",
        headers=auth_headers
    )
    assert get_response.json()["label"] == "New Name"
    assert get_response.json()["description"] == "Added description"


@pytest.mark.asyncio
async def test_delete_terminology(client: AsyncClient, auth_headers: dict):
    """Test soft-deleting a terminology."""
    # Create terminology
    create_response = await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json=[{
            "value": "TO_DELETE",
            "label": "Will Be Deleted",
            "namespace": "wip"
        }]
    )
    terminology_id = create_response.json()["results"][0]["id"]

    # Delete
    response = await client.request(
        "DELETE",
        "/api/def-store/terminologies",
        headers=auth_headers,
        json=[{"id": terminology_id}]
    )

    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == 1
    assert data["results"][0]["status"] == "deleted"

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
    assert response.status_code == 401
