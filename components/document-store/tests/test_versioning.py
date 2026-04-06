"""Tests for document versioning and upsert logic."""

import pytest
from httpx import AsyncClient


# Helper to create a single document and extract the result from BulkResponse
async def create_one(client: AsyncClient, auth_headers: dict, template_id: str, data: dict, **extra):
    """Create a single document via the bulk-first API and return the result item."""
    payload = {"namespace": "wip", "template_id": template_id, "data": data, **extra}
    response = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json=[payload]
    )
    assert response.status_code == 200, f"Create failed: {response.text}"
    bulk = response.json()
    assert bulk["total"] == 1
    assert bulk["succeeded"] == 1
    return bulk["results"][0]


@pytest.mark.asyncio
async def test_upsert_creates_new_version(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test that upserting an existing document creates a new version with the same stable document_id."""
    # Create initial document
    initial = await create_one(client, auth_headers, "PERSON", sample_person_data)
    assert initial["version"] == 1
    assert initial["is_new"] is True

    # Update with same identity (national_id)
    updated_data = sample_person_data.copy()
    updated_data["first_name"] = "Jane"

    updated = await create_one(client, auth_headers, "PERSON", updated_data)
    assert updated["version"] == 2
    assert updated["is_new"] is False
    assert updated["identity_hash"] == initial["identity_hash"]
    # Stable document ID: same document_id across versions
    assert updated["document_id"] == initial["document_id"]


@pytest.mark.asyncio
async def test_upsert_deactivates_old_version(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test that old version is deactivated on upsert."""
    # Create initial document
    initial = await create_one(client, auth_headers, "PERSON", sample_person_data)
    document_id = initial["document_id"]

    # Update with same identity
    updated_data = sample_person_data.copy()
    updated_data["first_name"] = "Jane"

    updated = await create_one(client, auth_headers, "PERSON", updated_data)

    # Stable ID: same document_id across versions
    assert updated["document_id"] == document_id

    # GET by document_id returns latest version (v2, active) by default
    latest_response = await client.get(
        f"/api/document-store/documents/{document_id}",
        headers=auth_headers
    )
    assert latest_response.status_code == 200
    assert latest_response.json()["version"] == 2
    assert latest_response.json()["status"] == "active"

    # Check old version (v1) is inactive via version-specific endpoint
    old_response = await client.get(
        f"/api/document-store/documents/{document_id}/versions/1",
        headers=auth_headers
    )

    assert old_response.status_code == 200
    assert old_response.json()["status"] == "inactive"


@pytest.mark.asyncio
async def test_get_document_versions(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test getting all versions of a document."""
    # Create initial document
    initial = await create_one(client, auth_headers, "PERSON", sample_person_data)
    document_id = initial["document_id"]

    # Create version 2
    updated_data = sample_person_data.copy()
    updated_data["first_name"] = "Jane"
    await create_one(client, auth_headers, "PERSON", updated_data)

    # Create version 3
    updated_data["first_name"] = "Jack"
    await create_one(client, auth_headers, "PERSON", updated_data)

    # Get all versions
    response = await client.get(
        f"/api/document-store/documents/{document_id}/versions",
        headers=auth_headers
    )

    assert response.status_code == 200
    data = response.json()
    assert data["current_version"] == 3
    assert len(data["versions"]) == 3

    # Versions should be sorted by version number descending
    versions = [v["version"] for v in data["versions"]]
    assert versions == [3, 2, 1]

    # All versions share the same stable document_id
    version_ids = [v["document_id"] for v in data["versions"]]
    assert all(vid == document_id for vid in version_ids)


@pytest.mark.asyncio
async def test_get_specific_version(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test getting a specific version of a document."""
    # Create initial document
    initial = await create_one(client, auth_headers, "PERSON", sample_person_data)
    document_id = initial["document_id"]

    # Create version 2 with different name
    updated_data = sample_person_data.copy()
    updated_data["first_name"] = "UpdatedName"
    await create_one(client, auth_headers, "PERSON", updated_data)

    # Get version 1
    response = await client.get(
        f"/api/document-store/documents/{document_id}/versions/1",
        headers=auth_headers
    )

    assert response.status_code == 200
    data = response.json()
    assert data["version"] == 1
    assert data["data"]["first_name"] == sample_person_data["first_name"]


@pytest.mark.asyncio
async def test_different_identity_creates_new_document(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test that different identity creates a new document, not a new version."""
    # Create document 1
    result1 = await create_one(client, auth_headers, "PERSON", sample_person_data)

    # Create document 2 with different national_id
    data2 = sample_person_data.copy()
    data2["national_id"] = "987654321"

    result2 = await create_one(client, auth_headers, "PERSON", data2)

    assert result1["identity_hash"] != result2["identity_hash"]
    assert result1["version"] == 1
    assert result2["version"] == 1
    assert result2["is_new"] is True
    # Different identity = different stable document_id
    assert result1["document_id"] != result2["document_id"]


@pytest.mark.asyncio
async def test_multiple_identity_fields(client: AsyncClient, auth_headers: dict, sample_employee_data: dict):
    """Test versioning with multiple identity fields."""
    # Create initial employee
    initial = await create_one(client, auth_headers, "EMPLOYEE", sample_employee_data)
    assert initial["version"] == 1

    # Same employee_id + company_id should create new version
    updated_data = sample_employee_data.copy()
    updated_data["name"] = "Updated Name"

    updated = await create_one(client, auth_headers, "EMPLOYEE", updated_data)
    assert updated["version"] == 2
    assert updated["identity_hash"] == initial["identity_hash"]
    # Stable document ID across versions
    assert updated["document_id"] == initial["document_id"]


@pytest.mark.asyncio
async def test_different_company_is_different_identity(client: AsyncClient, auth_headers: dict, sample_employee_data: dict):
    """Test that different company_id creates new identity."""
    # Create employee for company 1
    await create_one(client, auth_headers, "EMPLOYEE", sample_employee_data)

    # Create same employee for company 2
    data2 = sample_employee_data.copy()
    data2["company_id"] = "COMP002"

    result2 = await create_one(client, auth_headers, "EMPLOYEE", data2)

    # Should be a new document, not a new version
    assert result2["version"] == 1
    assert result2["is_new"] is True


@pytest.mark.asyncio
async def test_version_not_found(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test getting a non-existent version."""
    # Create document
    initial = await create_one(client, auth_headers, "PERSON", sample_person_data)
    document_id = initial["document_id"]

    # Try to get version 99
    response = await client.get(
        f"/api/document-store/documents/{document_id}/versions/99",
        headers=auth_headers
    )

    assert response.status_code == 404
