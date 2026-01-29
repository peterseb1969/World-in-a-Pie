"""Tests for Template CRUD operations."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_template(client: AsyncClient, auth_headers: dict):
    """Test creating a new template."""
    response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "code": "PERSON",
            "name": "Person Template",
            "description": "Template for person records",
            "identity_fields": ["national_id"],
            "fields": [
                {
                    "name": "first_name",
                    "label": "First Name",
                    "type": "string",
                    "mandatory": True
                },
                {
                    "name": "last_name",
                    "label": "Last Name",
                    "type": "string",
                    "mandatory": True
                },
                {
                    "name": "national_id",
                    "label": "National ID",
                    "type": "string",
                    "mandatory": True
                }
            ],
            "created_by": "test"
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert data["code"] == "PERSON"
    assert data["name"] == "Person Template"
    assert data["template_id"].startswith("TPL-")
    assert data["version"] == 1
    assert len(data["fields"]) == 3
    assert data["identity_fields"] == ["national_id"]


@pytest.mark.asyncio
async def test_create_template_without_auth(client: AsyncClient):
    """Test that creating a template without auth fails."""
    response = await client.post(
        "/api/template-store/templates",
        json={"code": "TEST", "name": "Test"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_template_duplicate_code(client: AsyncClient, auth_headers: dict):
    """Test that creating a template with duplicate code fails."""
    # Create first template
    await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={"code": "UNIQUE", "name": "First Template"}
    )

    # Try to create second with same code
    response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={"code": "UNIQUE", "name": "Second Template"}
    )
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_template_by_id(client: AsyncClient, auth_headers: dict):
    """Test getting a template by ID."""
    # Create a template
    create_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={"code": "GETBYID", "name": "Get By ID Template"}
    )
    template_id = create_response.json()["template_id"]

    # Get by ID
    response = await client.get(
        f"/api/template-store/templates/{template_id}",
        headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["template_id"] == template_id
    assert data["code"] == "GETBYID"


@pytest.mark.asyncio
async def test_get_template_by_code(client: AsyncClient, auth_headers: dict):
    """Test getting a template by code."""
    # Create a template
    await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={"code": "GETBYCODE", "name": "Get By Code Template"}
    )

    # Get by code
    response = await client.get(
        "/api/template-store/templates/by-code/GETBYCODE",
        headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["code"] == "GETBYCODE"


@pytest.mark.asyncio
async def test_get_template_not_found(client: AsyncClient, auth_headers: dict):
    """Test getting a non-existent template."""
    response = await client.get(
        "/api/template-store/templates/TPL-999999",
        headers=auth_headers
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_templates(client: AsyncClient, auth_headers: dict):
    """Test listing templates."""
    # Create some templates
    for i in range(3):
        await client.post(
            "/api/template-store/templates",
            headers=auth_headers,
            json={"code": f"LIST_{i}", "name": f"List Template {i}"}
        )

    # List all
    response = await client.get(
        "/api/template-store/templates",
        headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert len(data["items"]) == 3


@pytest.mark.asyncio
async def test_list_templates_with_pagination(client: AsyncClient, auth_headers: dict):
    """Test listing templates with pagination."""
    # Create templates
    for i in range(5):
        await client.post(
            "/api/template-store/templates",
            headers=auth_headers,
            json={"code": f"PAGE_{i}", "name": f"Page Template {i}"}
        )

    # Get first page
    response = await client.get(
        "/api/template-store/templates?page=1&page_size=2",
        headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["items"]) == 2
    assert data["page"] == 1
    assert data["page_size"] == 2


@pytest.mark.asyncio
async def test_update_template(client: AsyncClient, auth_headers: dict):
    """Test updating a template."""
    # Create a template
    create_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={"code": "UPDATE", "name": "Original Name"}
    )
    template_id = create_response.json()["template_id"]

    # Update it
    response = await client.put(
        f"/api/template-store/templates/{template_id}",
        headers=auth_headers,
        json={"name": "Updated Name", "updated_by": "test"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Name"
    assert data["version"] == 2


@pytest.mark.asyncio
async def test_update_template_add_fields(client: AsyncClient, auth_headers: dict):
    """Test updating a template to add fields."""
    # Create a template
    create_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "code": "ADDFIELDS",
            "name": "Add Fields Template",
            "fields": [
                {"name": "field1", "label": "Field 1", "type": "string"}
            ]
        }
    )
    template_id = create_response.json()["template_id"]

    # Update with more fields
    response = await client.put(
        f"/api/template-store/templates/{template_id}",
        headers=auth_headers,
        json={
            "fields": [
                {"name": "field1", "label": "Field 1", "type": "string"},
                {"name": "field2", "label": "Field 2", "type": "integer"}
            ]
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["fields"]) == 2


@pytest.mark.asyncio
async def test_delete_template(client: AsyncClient, auth_headers: dict):
    """Test deleting a template."""
    # Create a template
    create_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={"code": "DELETE", "name": "Delete Template"}
    )
    template_id = create_response.json()["template_id"]

    # Delete it
    response = await client.delete(
        f"/api/template-store/templates/{template_id}",
        headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["status"] == "deleted"

    # Verify it's inactive (not actually deleted)
    get_response = await client.get(
        f"/api/template-store/templates/{template_id}",
        headers=auth_headers
    )
    assert get_response.status_code == 200
    assert get_response.json()["status"] == "inactive"


@pytest.mark.asyncio
async def test_delete_template_not_found(client: AsyncClient, auth_headers: dict):
    """Test deleting a non-existent template."""
    response = await client.delete(
        "/api/template-store/templates/TPL-999999",
        headers=auth_headers
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_bulk_create_templates(client: AsyncClient, auth_headers: dict):
    """Test creating multiple templates at once."""
    response = await client.post(
        "/api/template-store/templates/bulk",
        headers=auth_headers,
        json={
            "templates": [
                {"code": "BULK_1", "name": "Bulk Template 1"},
                {"code": "BULK_2", "name": "Bulk Template 2"},
                {"code": "BULK_3", "name": "Bulk Template 3"}
            ],
            "created_by": "test"
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert data["succeeded"] == 3
    assert data["failed"] == 0


@pytest.mark.asyncio
async def test_template_with_field_types(client: AsyncClient, auth_headers: dict):
    """Test creating a template with various field types."""
    response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "code": "FIELDTYPES",
            "name": "Field Types Template",
            "fields": [
                {"name": "string_field", "label": "String", "type": "string"},
                {"name": "number_field", "label": "Number", "type": "number"},
                {"name": "integer_field", "label": "Integer", "type": "integer"},
                {"name": "boolean_field", "label": "Boolean", "type": "boolean"},
                {"name": "date_field", "label": "Date", "type": "date"},
                {"name": "datetime_field", "label": "DateTime", "type": "datetime"},
                {
                    "name": "term_field",
                    "label": "Term",
                    "type": "term",
                    "terminology_ref": "GENDER"
                },
                {
                    "name": "array_field",
                    "label": "Array",
                    "type": "array",
                    "array_item_type": "string"
                }
            ]
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["fields"]) == 8


@pytest.mark.asyncio
async def test_template_with_validation(client: AsyncClient, auth_headers: dict):
    """Test creating a template with field validation."""
    response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "code": "VALIDATION",
            "name": "Validation Template",
            "fields": [
                {
                    "name": "email",
                    "label": "Email",
                    "type": "string",
                    "validation": {
                        "pattern": "^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$",
                        "max_length": 255
                    }
                },
                {
                    "name": "age",
                    "label": "Age",
                    "type": "integer",
                    "validation": {
                        "minimum": 0,
                        "maximum": 150
                    }
                }
            ]
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert data["fields"][0]["validation"]["pattern"] is not None
    assert data["fields"][1]["validation"]["minimum"] == 0


@pytest.mark.asyncio
async def test_template_with_rules(client: AsyncClient, auth_headers: dict):
    """Test creating a template with validation rules."""
    response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "code": "RULES",
            "name": "Rules Template",
            "fields": [
                {"name": "country", "label": "Country", "type": "term", "terminology_ref": "COUNTRY"},
                {"name": "tax_id", "label": "Tax ID", "type": "string"}
            ],
            "rules": [
                {
                    "type": "conditional_required",
                    "description": "Tax ID required for Germany",
                    "conditions": [
                        {"field": "country", "operator": "equals", "value": "DE"}
                    ],
                    "target_field": "tax_id",
                    "required": True,
                    "error_message": "Tax ID is required for German residents"
                }
            ]
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["rules"]) == 1
    assert data["rules"][0]["type"] == "conditional_required"


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient):
    """Test health check endpoint."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["database"] == "connected"
