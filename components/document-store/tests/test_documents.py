"""Tests for document CRUD operations."""

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
async def test_create_document(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test creating a new document."""
    result = await create_one(client, auth_headers, "PERSON", sample_person_data, created_by="test_user")

    assert result["document_id"] is not None
    assert result["identity_hash"] is not None
    assert result["version"] == 1
    assert result["is_new"] is True
    assert result["status"] == "created"


@pytest.mark.asyncio
async def test_get_document(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test retrieving a document by ID."""
    # Create document first
    result = await create_one(client, auth_headers, "PERSON", sample_person_data)
    document_id = result["document_id"]

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
            json=[{
                "namespace": "wip",
                "template_id": "PERSON",
                "data": data
            }]
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
    await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json=[{
            "namespace": "wip",
            "template_id": "PERSON",
            "data": sample_person_data.copy()
        }]
    )

    # Create employee document
    await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json=[{
            "namespace": "wip",
            "template_id": "EMPLOYEE",
            "data": sample_employee_data
        }]
    )

    # List only person documents
    response = await client.get(
        "/api/document-store/documents?template_id=PERSON",
        headers=auth_headers
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["template_id"]  # truthy canonical ID


@pytest.mark.asyncio
async def test_delete_document(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test soft-deleting a document."""
    # Create document
    result = await create_one(client, auth_headers, "PERSON", sample_person_data)
    document_id = result["document_id"]

    # Delete document via bulk-first DELETE
    response = await client.request(
        "DELETE",
        "/api/document-store/documents",
        headers=auth_headers,
        json=[{"id": document_id, "updated_by": "test_user"}]
    )

    assert response.status_code == 200
    bulk = response.json()
    assert bulk["succeeded"] == 1
    assert bulk["results"][0]["status"] == "deleted"

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
    result = await create_one(client, auth_headers, "PERSON", sample_person_data)
    document_id = result["document_id"]

    # Archive document via bulk-first POST /documents/archive
    response = await client.post(
        "/api/document-store/documents/archive",
        headers=auth_headers,
        json=[{"id": document_id}]
    )

    assert response.status_code == 200
    bulk = response.json()
    assert bulk["succeeded"] == 1

    # Verify document is archived
    get_response = await client.get(
        f"/api/document-store/documents/{document_id}",
        headers=auth_headers
    )
    assert get_response.status_code == 200
    assert get_response.json()["status"] == "archived"


@pytest.mark.asyncio
async def test_get_document_by_identity(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test getting a document by identity hash."""
    # Create document
    result = await create_one(client, auth_headers, "PERSON", sample_person_data)
    identity_hash = result["identity_hash"]

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
            json=[{
                "namespace": "wip",
                "template_id": "PERSON",
                "data": data
            }]
        )

    # Query for age >= 30
    response = await client.post(
        "/api/document-store/documents/query",
        headers=auth_headers,
        json={
            "filters": [
                {"field": "data.age", "operator": "gte", "value": 30}
            ],
            "template_id": "PERSON"
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
            "namespace": "wip",
            "template_id": "PERSON",
            "data": data
        })

    response = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json=items
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert data["succeeded"] == 3
    assert data["failed"] == 0


@pytest.mark.asyncio
async def test_auth_required(client: AsyncClient, sample_person_data: dict):
    """Test that authentication is required."""
    response = await client.post(
        "/api/document-store/documents",
        json=[{
            "namespace": "wip",
            "template_id": "PERSON",
            "data": sample_person_data
        }]
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_document_without_identity_fields(client: AsyncClient, auth_headers: dict):
    """Test creating a document when template has no identity_fields.

    Regression test: identity_hash was a required str field, causing a crash
    when the template had no identity_fields (identity_hash was None from Registry).
    """
    result = await create_one(
        client, auth_headers, "TPL-NO-IDENTITY",
        {"title": "Meeting Notes", "notes": "Discussed Q3 roadmap"}
    )
    assert result["status"] == "created"
    assert result["version"] == 1
    assert result["is_new"] is True
    # identity_hash should be empty string, not None
    assert result["identity_hash"] == ""

    # Creating another document with same data should create a NEW document (no upsert)
    result2 = await create_one(
        client, auth_headers, "TPL-NO-IDENTITY",
        {"title": "Meeting Notes", "notes": "Discussed Q3 roadmap"}
    )
    assert result2["status"] == "created"
    assert result2["is_new"] is True
    assert result2["document_id"] != result["document_id"]


@pytest.mark.asyncio
async def test_get_document_without_identity_fields(client: AsyncClient, auth_headers: dict):
    """Test that a document without identity_fields can be retrieved."""
    result = await create_one(
        client, auth_headers, "TPL-NO-IDENTITY",
        {"title": "Test Note"}
    )
    doc_id = result["document_id"]

    response = await client.get(
        f"/api/document-store/documents/{doc_id}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    doc = response.json()
    assert doc["data"]["title"] == "Test Note"
    assert doc["identity_hash"] == ""


@pytest.mark.asyncio
async def test_invalid_api_key(client: AsyncClient, sample_person_data: dict):
    """Test that invalid API key is rejected."""
    response = await client.post(
        "/api/document-store/documents",
        headers={"X-API-Key": "invalid_key"},
        json=[{
            "namespace": "wip",
            "template_id": "PERSON",
            "data": sample_person_data
        }]
    )

    assert response.status_code == 401
