"""Tests for Template validation functionality."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_validate_template_valid(client: AsyncClient, auth_headers: dict):
    """Test validating a valid template."""
    # Create a template with valid references
    create_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "code": "VALID_TEMPLATE",
            "name": "Valid Template",
            "fields": [
                {
                    "name": "gender",
                    "label": "Gender",
                    "type": "term",
                    "terminology_ref": "GENDER"  # Mocked to exist
                },
                {"name": "name", "label": "Name", "type": "string"}
            ]
        }
    )
    template_id = create_response.json()["template_id"]

    # Validate template
    response = await client.post(
        f"/api/template-store/templates/{template_id}/validate",
        headers=auth_headers,
        json={"check_terminologies": True, "check_templates": True}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["valid"] == True
    assert len(data["errors"]) == 0


@pytest.mark.asyncio
async def test_validate_template_invalid_terminology(client: AsyncClient, auth_headers: dict):
    """Test validating a template with invalid terminology reference."""
    # Create a template with invalid terminology ref
    create_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "code": "INVALID_TERM_REF",
            "name": "Invalid Term Ref",
            "fields": [
                {
                    "name": "invalid_field",
                    "label": "Invalid Field",
                    "type": "term",
                    "terminology_ref": "NONEXISTENT_TERMINOLOGY"  # Mocked to not exist
                }
            ]
        }
    )
    template_id = create_response.json()["template_id"]

    # Validate template
    response = await client.post(
        f"/api/template-store/templates/{template_id}/validate",
        headers=auth_headers,
        json={"check_terminologies": True}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["valid"] == False
    assert len(data["errors"]) > 0
    assert any("terminology" in e["message"].lower() or "not found" in e["message"].lower()
               for e in data["errors"])


@pytest.mark.asyncio
async def test_validate_template_invalid_extends(client: AsyncClient, auth_headers: dict):
    """Test validating a template with manually set invalid extends."""
    # First create a valid template
    create_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={"code": "ORPHAN", "name": "Orphan Template"}
    )
    template_id = create_response.json()["template_id"]

    # Manually corrupt the extends (simulating data corruption or external modification)
    # Since we can't do this easily via API, we'll create a template with extends
    # and then delete the parent (but this is blocked)
    # Instead, we'll test the validation when a referenced template doesn't exist

    # Create a template that references a non-existent template
    create_response2 = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "code": "NESTED_REF",
            "name": "Nested Ref",
            "fields": [
                {
                    "name": "nested",
                    "label": "Nested",
                    "type": "object",
                    "template_ref": "NONEXISTENT_TEMPLATE"  # Invalid reference
                }
            ]
        }
    )
    template_id2 = create_response2.json()["template_id"]

    # Validate template
    response = await client.post(
        f"/api/template-store/templates/{template_id2}/validate",
        headers=auth_headers,
        json={"check_templates": True}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["valid"] == False
    assert any("template" in e["message"].lower() or "not found" in e["message"].lower()
               for e in data["errors"])


@pytest.mark.asyncio
async def test_validate_template_array_terminology_ref(client: AsyncClient, auth_headers: dict):
    """Test validating array field with terminology reference."""
    # Create a template with array terminology ref
    create_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "code": "ARRAY_TERM",
            "name": "Array Terminology",
            "fields": [
                {
                    "name": "countries",
                    "label": "Countries",
                    "type": "array",
                    "array_item_type": "term",
                    "array_terminology_ref": "COUNTRY"  # Mocked to exist
                }
            ]
        }
    )
    template_id = create_response.json()["template_id"]

    # Validate template
    response = await client.post(
        f"/api/template-store/templates/{template_id}/validate",
        headers=auth_headers,
        json={"check_terminologies": True}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["valid"] == True


@pytest.mark.asyncio
async def test_validate_template_array_template_ref(client: AsyncClient, auth_headers: dict):
    """Test validating array field with template reference."""
    # Create a referenced template first
    addr_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "code": "ADDRESS",
            "name": "Address",
            "fields": [
                {"name": "street", "label": "Street", "type": "string"},
                {"name": "city", "label": "City", "type": "string"}
            ]
        }
    )
    addr_id = addr_response.json()["template_id"]

    # Create template with array of objects
    create_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "code": "PERSON_ADDRESSES",
            "name": "Person with Addresses",
            "fields": [
                {"name": "name", "label": "Name", "type": "string"},
                {
                    "name": "addresses",
                    "label": "Addresses",
                    "type": "array",
                    "array_item_type": "object",
                    "array_template_ref": addr_id
                }
            ]
        }
    )
    template_id = create_response.json()["template_id"]

    # Validate template
    response = await client.post(
        f"/api/template-store/templates/{template_id}/validate",
        headers=auth_headers,
        json={"check_templates": True}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["valid"] == True


@pytest.mark.asyncio
async def test_validate_template_skip_terminology_check(client: AsyncClient, auth_headers: dict):
    """Test validating template with terminology check disabled."""
    # Create a template with invalid terminology ref
    create_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "code": "SKIP_TERM_CHECK",
            "name": "Skip Term Check",
            "fields": [
                {
                    "name": "field",
                    "label": "Field",
                    "type": "term",
                    "terminology_ref": "NONEXISTENT"
                }
            ]
        }
    )
    template_id = create_response.json()["template_id"]

    # Validate template with terminology check disabled
    response = await client.post(
        f"/api/template-store/templates/{template_id}/validate",
        headers=auth_headers,
        json={"check_terminologies": False, "check_templates": False}
    )
    assert response.status_code == 200
    data = response.json()
    # Should be valid because we skipped the terminology check
    assert data["valid"] == True


@pytest.mark.asyncio
async def test_validate_nonexistent_template(client: AsyncClient, auth_headers: dict):
    """Test validating a template that doesn't exist."""
    response = await client.post(
        "/api/template-store/templates/TPL-999999/validate",
        headers=auth_headers,
        json={}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["valid"] == False
    assert any("not found" in e["message"].lower() for e in data["errors"])


@pytest.mark.asyncio
async def test_validate_template_nested_object(client: AsyncClient, auth_headers: dict):
    """Test validating template with nested object field."""
    # Create nested template
    nested_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "code": "CONTACT_INFO",
            "name": "Contact Info",
            "fields": [
                {"name": "email", "label": "Email", "type": "string"},
                {"name": "phone", "label": "Phone", "type": "string"}
            ]
        }
    )
    nested_id = nested_response.json()["template_id"]

    # Create main template with nested object
    create_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "code": "PERSON_CONTACT",
            "name": "Person with Contact",
            "fields": [
                {"name": "name", "label": "Name", "type": "string"},
                {
                    "name": "contact",
                    "label": "Contact",
                    "type": "object",
                    "template_ref": nested_id
                }
            ]
        }
    )
    template_id = create_response.json()["template_id"]

    # Validate template
    response = await client.post(
        f"/api/template-store/templates/{template_id}/validate",
        headers=auth_headers,
        json={"check_templates": True}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["valid"] == True


@pytest.mark.asyncio
async def test_validate_template_with_extends(client: AsyncClient, auth_headers: dict):
    """Test validating a template that extends another."""
    # Create parent
    parent_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "code": "VAL_PARENT",
            "name": "Validation Parent",
            "fields": [{"name": "id", "label": "ID", "type": "string"}]
        }
    )
    parent_id = parent_response.json()["template_id"]

    # Create child
    child_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "code": "VAL_CHILD",
            "name": "Validation Child",
            "extends": parent_id,
            "fields": [{"name": "name", "label": "Name", "type": "string"}]
        }
    )
    child_id = child_response.json()["template_id"]

    # Validate child template (extends reference should be valid)
    response = await client.post(
        f"/api/template-store/templates/{child_id}/validate",
        headers=auth_headers,
        json={"check_templates": True}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["valid"] == True


@pytest.mark.asyncio
async def test_validate_template_multiple_errors(client: AsyncClient, auth_headers: dict):
    """Test that validation returns all errors at once."""
    # Create template with multiple invalid references
    create_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "code": "MULTI_ERROR",
            "name": "Multiple Errors",
            "fields": [
                {
                    "name": "field1",
                    "label": "Field 1",
                    "type": "term",
                    "terminology_ref": "INVALID_TERM_1"
                },
                {
                    "name": "field2",
                    "label": "Field 2",
                    "type": "term",
                    "terminology_ref": "INVALID_TERM_2"
                },
                {
                    "name": "field3",
                    "label": "Field 3",
                    "type": "object",
                    "template_ref": "INVALID_TEMPLATE"
                }
            ]
        }
    )
    template_id = create_response.json()["template_id"]

    # Validate template
    response = await client.post(
        f"/api/template-store/templates/{template_id}/validate",
        headers=auth_headers,
        json={"check_terminologies": True, "check_templates": True}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["valid"] == False
    # Should have errors for each invalid reference
    assert len(data["errors"]) >= 3
