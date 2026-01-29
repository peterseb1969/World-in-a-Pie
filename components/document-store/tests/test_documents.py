"""Tests for document CRUD operations."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_document(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test creating a new document."""
    response = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json={
            "template_id": "TPL-000001",
            "data": sample_person_data,
            "created_by": "test_user"
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["document_id"] is not None
    assert data["template_id"] == "TPL-000001"
    assert data["identity_hash"] is not None
    assert data["version"] == 1
    assert data["is_new"] is True
    assert data["previous_version"] is None


@pytest.mark.asyncio
async def test_get_document(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test retrieving a document by ID."""
    # Create document first
    create_response = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json={
            "template_id": "TPL-000001",
            "data": sample_person_data
        }
    )
    assert create_response.status_code == 200
    document_id = create_response.json()["document_id"]

    # Get the document
    response = await client.get(
        f"/api/document-store/documents/{document_id}",
        headers=auth_headers
    )

    assert response.status_code == 200
    data = response.json()
    assert data["document_id"] == document_id
    assert data["data"]["national_id"] == sample_person_data["national_id"]
    assert data["data"]["first_name"] == sample_person_data["first_name"]
    assert data["status"] == "active"


@pytest.mark.asyncio
async def test_get_document_not_found(client: AsyncClient, auth_headers: dict):
    """Test getting a non-existent document."""
    response = await client.get(
        "/api/document-store/documents/nonexistent-id",
        headers=auth_headers
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_documents(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test listing documents."""
    # Create a few documents with different national_ids
    for i in range(3):
        data = sample_person_data.copy()
        data["national_id"] = f"12345678{i}"
        await client.post(
            "/api/document-store/documents",
            headers=auth_headers,
            json={
                "template_id": "TPL-000001",
                "data": data
            }
        )

    # List documents
    response = await client.get(
        "/api/document-store/documents",
        headers=auth_headers
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert len(data["items"]) == 3


@pytest.mark.asyncio
async def test_list_documents_with_filter(client: AsyncClient, auth_headers: dict, sample_person_data: dict, sample_employee_data: dict):
    """Test listing documents with template filter."""
    # Create person document
    person_data = sample_person_data.copy()
    await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json={
            "template_id": "TPL-000001",
            "data": person_data
        }
    )

    # Create employee document
    await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json={
            "template_id": "TPL-000002",
            "data": sample_employee_data
        }
    )

    # List only person documents
    response = await client.get(
        "/api/document-store/documents?template_id=TPL-000001",
        headers=auth_headers
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["template_id"] == "TPL-000001"


@pytest.mark.asyncio
async def test_delete_document(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test soft-deleting a document."""
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

    # Delete document
    response = await client.delete(
        f"/api/document-store/documents/{document_id}?deleted_by=test_user",
        headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json()["status"] == "deleted"

    # Verify document is inactive
    get_response = await client.get(
        f"/api/document-store/documents/{document_id}",
        headers=auth_headers
    )
    assert get_response.status_code == 200
    assert get_response.json()["status"] == "inactive"


@pytest.mark.asyncio
async def test_archive_document(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test archiving a document."""
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

    # Archive document
    response = await client.post(
        f"/api/document-store/documents/{document_id}/archive",
        headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json()["status"] == "archived"


@pytest.mark.asyncio
async def test_get_document_by_identity(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test getting a document by identity hash."""
    # Create document
    create_response = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json={
            "template_id": "TPL-000001",
            "data": sample_person_data
        }
    )
    identity_hash = create_response.json()["identity_hash"]

    # Get by identity
    response = await client.get(
        f"/api/document-store/documents/by-identity/{identity_hash}",
        headers=auth_headers
    )

    assert response.status_code == 200
    data = response.json()
    assert data["identity_hash"] == identity_hash


@pytest.mark.asyncio
async def test_query_documents(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test querying documents with complex filters."""
    # Create documents with different ages
    for i, age in enumerate([25, 30, 35]):
        data = sample_person_data.copy()
        data["national_id"] = f"10000000{i}"  # 9 digits to match pattern
        data["age"] = age
        await client.post(
            "/api/document-store/documents",
            headers=auth_headers,
            json={
                "template_id": "TPL-000001",
                "data": data
            }
        )

    # Query for age >= 30
    response = await client.post(
        "/api/document-store/documents/query",
        headers=auth_headers,
        json={
            "filters": [
                {"field": "data.age", "operator": "gte", "value": 30}
            ],
            "template_id": "TPL-000001"
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2


@pytest.mark.asyncio
async def test_bulk_create_documents(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test bulk creating documents."""
    items = []
    for i in range(3):
        data = sample_person_data.copy()
        data["national_id"] = f"98765432{i}"
        items.append({
            "template_id": "TPL-000001",
            "data": data
        })

    response = await client.post(
        "/api/document-store/documents/bulk",
        headers=auth_headers,
        json={"items": items}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert data["created"] == 3
    assert data["failed"] == 0


@pytest.mark.asyncio
async def test_auth_required(client: AsyncClient, sample_person_data: dict):
    """Test that authentication is required."""
    response = await client.post(
        "/api/document-store/documents",
        json={
            "template_id": "TPL-000001",
            "data": sample_person_data
        }
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_invalid_api_key(client: AsyncClient, sample_person_data: dict):
    """Test that invalid API key is rejected."""
    response = await client.post(
        "/api/document-store/documents",
        headers={"X-API-Key": "invalid_key"},
        json={
            "template_id": "TPL-000001",
            "data": sample_person_data
        }
    )

    assert response.status_code == 403
