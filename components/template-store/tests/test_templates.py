"""Tests for Template CRUD operations."""

from httpx import AsyncClient
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_one(client: AsyncClient, auth_headers: dict, payload: dict) -> dict:
    """Create a single template via the bulk-first POST and return the
    BulkResponse JSON.  Asserts 200 and succeeded == 1."""
    response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json=[payload],
    )
    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == 1, f"Create failed: {data}"
    return data


async def _create_one_id(client: AsyncClient, auth_headers: dict, payload: dict) -> str:
    """Create a single template and return its template_id."""
    data = await _create_one(client, auth_headers, payload)
    return data["results"][0]["id"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_template(client: AsyncClient, auth_headers: dict):
    """Test creating a new template."""
    data = await _create_one(client, auth_headers, {
        "namespace": "wip",
        "value": "PERSON",
        "label": "Person Template",
        "description": "Template for person records",
        "identity_fields": ["national_id"],
        "fields": [
            {
                "name": "first_name",
                "label": "First Name",
                "type": "string",
                "mandatory": True,
            },
            {
                "name": "last_name",
                "label": "Last Name",
                "type": "string",
                "mandatory": True,
            },
            {
                "name": "national_id",
                "label": "National ID",
                "type": "string",
                "mandatory": True,
            },
        ],
        "created_by": "test",
    })
    result = data["results"][0]
    assert result["status"] == "created"
    assert result["value"] == "PERSON"
    assert result["id"]  # Real Registry assigns the ID format
    assert result["version"] == 1

    # Fetch full entity to verify fields & identity_fields
    get_resp = await client.get(
        f"/api/template-store/templates/{result['id']}",
        headers=auth_headers,
    )
    assert get_resp.status_code == 200
    full = get_resp.json()
    assert full["label"] == "Person Template"
    assert len(full["fields"]) == 3
    assert full["identity_fields"] == ["national_id"]


@pytest.mark.asyncio
async def test_create_template_without_auth(client: AsyncClient):
    """Test that creating a template without auth fails."""
    response = await client.post(
        "/api/template-store/templates",
        json=[{"namespace": "wip", "value": "TEST", "label": "Test"}],
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_template_duplicate_code(client: AsyncClient, auth_headers: dict):
    """Test that creating a template with duplicate code fails."""
    # Create first template
    await _create_one(client, auth_headers, {"namespace": "wip", "value": "UNIQUE", "label": "First Template"})

    # Try to create second with same value
    response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json=[{"namespace": "wip", "value": "UNIQUE", "label": "Second Template"}],
    )
    assert response.status_code == 200
    data = response.json()
    assert data["failed"] == 1
    assert data["results"][0]["status"] == "error"
    assert "already exists" in data["results"][0]["error"]


@pytest.mark.asyncio
async def test_get_template_by_id(client: AsyncClient, auth_headers: dict):
    """Test getting a template by ID."""
    template_id = await _create_one_id(
        client, auth_headers, {"namespace": "wip", "value": "GETBYID", "label": "Get By ID Template"}
    )

    # Get by ID
    response = await client.get(
        f"/api/template-store/templates/{template_id}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["template_id"] == template_id
    assert data["value"] == "GETBYID"


@pytest.mark.asyncio
async def test_get_template_by_value(client: AsyncClient, auth_headers: dict):
    """Test getting a template by value."""
    await _create_one(client, auth_headers, {"namespace": "wip", "value": "GETBYVALUE", "label": "Get By Value Template"})

    # Get by value
    response = await client.get(
        "/api/template-store/templates/by-value/GETBYVALUE",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["value"] == "GETBYVALUE"


@pytest.mark.asyncio
async def test_get_template_not_found(client: AsyncClient, auth_headers: dict):
    """Test getting a non-existent template."""
    response = await client.get(
        "/api/template-store/templates/TPL-999999",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_templates(client: AsyncClient, auth_headers: dict):
    """Test listing templates."""
    # Create some templates
    for i in range(3):
        await _create_one(client, auth_headers, {"namespace": "wip", "value": f"LIST_{i}", "label": f"List Template {i}"})

    # List all
    response = await client.get(
        "/api/template-store/templates",
        headers=auth_headers,
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
        await _create_one(client, auth_headers, {"namespace": "wip", "value": f"PAGE_{i}", "label": f"Page Template {i}"})

    # Get first page
    response = await client.get(
        "/api/template-store/templates?page=1&page_size=2",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["items"]) == 2
    assert data["page"] == 1
    assert data["page_size"] == 2


@pytest.mark.asyncio
async def test_update_template(client: AsyncClient, auth_headers: dict):
    """Test updating a template creates a new version with the same template_id."""
    template_id = await _create_one_id(
        client, auth_headers, {"namespace": "wip", "value": "UPDATE", "label": "Original Name"}
    )

    # Update it via bulk PUT
    response = await client.put(
        "/api/template-store/templates",
        headers=auth_headers,
        json=[{"template_id": template_id, "label": "Updated Name", "updated_by": "test"}],
    )
    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == 1
    result = data["results"][0]
    # Stable ID: template_id stays the same across versions
    assert result["id"] == template_id
    assert result["version"] == 2
    assert result["is_new_version"] is True

    # Fetching by template_id should return the latest version
    get_response = await client.get(
        f"/api/template-store/templates/{template_id}",
        headers=auth_headers,
    )
    assert get_response.status_code == 200
    get_data = get_response.json()
    assert get_data["template_id"] == template_id
    assert get_data["label"] == "Updated Name"
    assert get_data["version"] == 2


@pytest.mark.asyncio
async def test_update_template_add_fields(client: AsyncClient, auth_headers: dict):
    """Test updating a template to add fields creates a new version with same ID."""
    template_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "ADDFIELDS",
        "label": "Add Fields Template",
        "fields": [
            {"name": "field1", "label": "Field 1", "type": "string"}
        ],
    })

    # Update with more fields via bulk PUT
    response = await client.put(
        "/api/template-store/templates",
        headers=auth_headers,
        json=[{
            "template_id": template_id,
            "fields": [
                {"name": "field1", "label": "Field 1", "type": "string"},
                {"name": "field2", "label": "Field 2", "type": "integer"},
            ],
        }],
    )
    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == 1
    result = data["results"][0]
    # Stable ID: same template_id, new version
    assert result["id"] == template_id
    assert result["version"] == 2
    assert result["is_new_version"] is True

    # Verify the latest version has both fields
    get_response = await client.get(
        f"/api/template-store/templates/{template_id}",
        headers=auth_headers,
    )
    assert get_response.status_code == 200
    get_data = get_response.json()
    assert len(get_data["fields"]) == 2


@pytest.mark.asyncio
async def test_delete_template(client: AsyncClient, auth_headers: dict):
    """Test deleting a template."""
    template_id = await _create_one_id(
        client, auth_headers, {"namespace": "wip", "value": "DELETE", "label": "Delete Template"}
    )

    # Delete it via bulk DELETE
    response = await client.request(
        "DELETE",
        "/api/template-store/templates",
        headers=auth_headers,
        json=[{"id": template_id}],
    )
    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == 1
    assert data["results"][0]["status"] == "deleted"

    # Verify it's inactive (not actually deleted)
    get_response = await client.get(
        f"/api/template-store/templates/{template_id}",
        headers=auth_headers,
    )
    assert get_response.status_code == 200
    assert get_response.json()["status"] == "inactive"


@pytest.mark.asyncio
async def test_delete_template_not_found(client: AsyncClient, auth_headers: dict):
    """Test deleting a non-existent template."""
    response = await client.request(
        "DELETE",
        "/api/template-store/templates",
        headers=auth_headers,
        json=[{"id": "TPL-999999"}],
    )
    assert response.status_code == 200
    data = response.json()
    assert data["failed"] == 1
    assert data["results"][0]["status"] == "error"


@pytest.mark.asyncio
async def test_bulk_create_templates(client: AsyncClient, auth_headers: dict):
    """Test creating multiple templates at once."""
    response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json=[
            {"namespace": "wip", "value": "BULK_1", "label": "Bulk Template 1", "created_by": "test"},
            {"namespace": "wip", "value": "BULK_2", "label": "Bulk Template 2", "created_by": "test"},
            {"namespace": "wip", "value": "BULK_3", "label": "Bulk Template 3", "created_by": "test"},
        ],
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert data["succeeded"] == 3
    assert data["failed"] == 0


@pytest.mark.asyncio
async def test_template_with_field_types(client: AsyncClient, auth_headers: dict):
    """Test creating a template with various field types."""
    template_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "FIELDTYPES",
        "label": "Field Types Template",
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
                "terminology_ref": "GENDER",
            },
            {
                "name": "array_field",
                "label": "Array",
                "type": "array",
                "array_item_type": "string",
            },
        ],
    })

    # Fetch to verify fields
    get_resp = await client.get(
        f"/api/template-store/templates/{template_id}",
        headers=auth_headers,
    )
    assert get_resp.status_code == 200
    assert len(get_resp.json()["fields"]) == 8


@pytest.mark.asyncio
async def test_template_with_validation(client: AsyncClient, auth_headers: dict):
    """Test creating a template with field validation."""
    template_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "VALIDATION",
        "label": "Validation Template",
        "fields": [
            {
                "name": "email",
                "label": "Email",
                "type": "string",
                "validation": {
                    "pattern": "^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$",
                    "max_length": 255,
                },
            },
            {
                "name": "age",
                "label": "Age",
                "type": "integer",
                "validation": {
                    "minimum": 0,
                    "maximum": 150,
                },
            },
        ],
    })

    # Fetch to verify validation rules
    get_resp = await client.get(
        f"/api/template-store/templates/{template_id}",
        headers=auth_headers,
    )
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["fields"][0]["validation"]["pattern"] is not None
    assert data["fields"][1]["validation"]["minimum"] == 0


@pytest.mark.asyncio
async def test_template_with_rules(client: AsyncClient, auth_headers: dict):
    """Test creating a template with validation rules."""
    template_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "RULES",
        "label": "Rules Template",
        "fields": [
            {"name": "country", "label": "Country", "type": "term", "terminology_ref": "COUNTRY"},
            {"name": "tax_id", "label": "Tax ID", "type": "string"},
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
                "error_message": "Tax ID is required for German residents",
            }
        ],
    })

    # Fetch to verify rules
    get_resp = await client.get(
        f"/api/template-store/templates/{template_id}",
        headers=auth_headers,
    )
    assert get_resp.status_code == 200
    data = get_resp.json()
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
