"""Tests for document versioning and upsert logic."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_upsert_creates_new_version(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test that upserting an existing document creates a new version."""
    # Create initial document
    create_response = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json={
            "template_id": "TPL-000001",
            "data": sample_person_data
        }
    )

    assert create_response.status_code == 200
    initial = create_response.json()
    assert initial["version"] == 1
    assert initial["is_new"] is True

    # Update with same identity (national_id)
    updated_data = sample_person_data.copy()
    updated_data["first_name"] = "Jane"

    update_response = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json={
            "template_id": "TPL-000001",
            "data": updated_data
        }
    )

    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["version"] == 2
    assert updated["is_new"] is False
    assert updated["previous_version"] == 1
    assert updated["identity_hash"] == initial["identity_hash"]


@pytest.mark.asyncio
async def test_upsert_deactivates_old_version(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test that old version is deactivated on upsert."""
    # Create initial document
    create_response = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json={
            "template_id": "TPL-000001",
            "data": sample_person_data
        }
    )
    initial_id = create_response.json()["document_id"]

    # Update with same identity
    updated_data = sample_person_data.copy()
    updated_data["first_name"] = "Jane"

    await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json={
            "template_id": "TPL-000001",
            "data": updated_data
        }
    )

    # Check old version is inactive
    old_response = await client.get(
        f"/api/document-store/documents/{initial_id}",
        headers=auth_headers
    )

    assert old_response.status_code == 200
    assert old_response.json()["status"] == "inactive"


@pytest.mark.asyncio
async def test_get_document_versions(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test getting all versions of a document."""
    # Create initial document
    create_response = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json={
            "template_id": "TPL-000001",
            "data": sample_person_data
        }
    )
    document_id = create_response.json()["document_id"]

    # Create version 2
    updated_data = sample_person_data.copy()
    updated_data["first_name"] = "Jane"
    await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json={
            "template_id": "TPL-000001",
            "data": updated_data
        }
    )

    # Create version 3
    updated_data["first_name"] = "Jack"
    await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json={
            "template_id": "TPL-000001",
            "data": updated_data
        }
    )

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


@pytest.mark.asyncio
async def test_get_specific_version(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test getting a specific version of a document."""
    # Create initial document
    create_response = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json={
            "template_id": "TPL-000001",
            "data": sample_person_data
        }
    )
    document_id = create_response.json()["document_id"]

    # Create version 2 with different name
    updated_data = sample_person_data.copy()
    updated_data["first_name"] = "UpdatedName"
    await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json={
            "template_id": "TPL-000001",
            "data": updated_data
        }
    )

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
    response1 = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json={
            "template_id": "TPL-000001",
            "data": sample_person_data
        }
    )

    # Create document 2 with different national_id
    data2 = sample_person_data.copy()
    data2["national_id"] = "987654321"

    response2 = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json={
            "template_id": "TPL-000001",
            "data": data2
        }
    )

    assert response1.json()["identity_hash"] != response2.json()["identity_hash"]
    assert response1.json()["version"] == 1
    assert response2.json()["version"] == 1
    assert response2.json()["is_new"] is True


@pytest.mark.asyncio
async def test_multiple_identity_fields(client: AsyncClient, auth_headers: dict, sample_employee_data: dict):
    """Test versioning with multiple identity fields."""
    # Create initial employee
    create_response = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json={
            "template_id": "TPL-000002",
            "data": sample_employee_data
        }
    )

    assert create_response.status_code == 200
    initial = create_response.json()
    assert initial["version"] == 1

    # Same employee_id + company_id should create new version
    updated_data = sample_employee_data.copy()
    updated_data["name"] = "Updated Name"

    update_response = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json={
            "template_id": "TPL-000002",
            "data": updated_data
        }
    )

    assert update_response.status_code == 200
    assert update_response.json()["version"] == 2
    assert update_response.json()["identity_hash"] == initial["identity_hash"]


@pytest.mark.asyncio
async def test_different_company_is_different_identity(client: AsyncClient, auth_headers: dict, sample_employee_data: dict):
    """Test that different company_id creates new identity."""
    # Create employee for company 1
    await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json={
            "template_id": "TPL-000002",
            "data": sample_employee_data
        }
    )

    # Create same employee for company 2
    data2 = sample_employee_data.copy()
    data2["company_id"] = "COMP002"

    response2 = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json={
            "template_id": "TPL-000002",
            "data": data2
        }
    )

    # Should be a new document, not a new version
    assert response2.json()["version"] == 1
    assert response2.json()["is_new"] is True


@pytest.mark.asyncio
async def test_version_not_found(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test getting a non-existent version."""
    # Create document
    create_response = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json={
            "template_id": "TPL-000001",
            "data": sample_person_data
        }
    )
    document_id = create_response.json()["document_id"]

    # Try to get version 99
    response = await client.get(
        f"/api/document-store/documents/{document_id}/versions/99",
        headers=auth_headers
    )

    assert response.status_code == 404
