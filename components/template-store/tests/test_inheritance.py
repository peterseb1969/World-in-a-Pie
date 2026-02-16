"""Tests for Template inheritance functionality."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_template_with_extends(client: AsyncClient, auth_headers: dict):
    """Test creating a template that extends another."""
    # Create parent template
    parent_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "value": "PARENT",
            "label": "Parent Template",
            "identity_fields": ["id_field"],
            "fields": [
                {"name": "id_field", "label": "ID Field", "type": "string", "mandatory": True},
                {"name": "parent_field", "label": "Parent Field", "type": "string"}
            ]
        }
    )
    parent_id = parent_response.json()["template_id"]

    # Create child template
    child_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "value": "CHILD",
            "label": "Child Template",
            "extends": parent_id,
            "fields": [
                {"name": "child_field", "label": "Child Field", "type": "string"}
            ]
        }
    )
    assert child_response.status_code == 200
    child_data = child_response.json()
    assert child_data["extends"] == parent_id


@pytest.mark.asyncio
async def test_get_template_with_inheritance_resolved(client: AsyncClient, auth_headers: dict):
    """Test that getting a child template resolves inherited fields."""
    # Create parent template
    parent_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "value": "RESOLVE_PARENT",
            "label": "Resolve Parent",
            "identity_fields": ["parent_id"],
            "fields": [
                {"name": "parent_id", "label": "Parent ID", "type": "string"},
                {"name": "shared_field", "label": "Shared Field", "type": "string"}
            ]
        }
    )
    parent_id = parent_response.json()["template_id"]

    # Create child template
    child_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "value": "RESOLVE_CHILD",
            "label": "Resolve Child",
            "extends": parent_id,
            "fields": [
                {"name": "child_only", "label": "Child Only", "type": "string"}
            ]
        }
    )
    child_id = child_response.json()["template_id"]

    # Get child template (resolved)
    response = await client.get(
        f"/api/template-store/templates/{child_id}",
        headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()

    # Should have both parent and child fields
    field_names = [f["name"] for f in data["fields"]]
    assert "parent_id" in field_names
    assert "shared_field" in field_names
    assert "child_only" in field_names
    assert len(data["fields"]) == 3

    # Should inherit identity_fields from parent
    assert data["identity_fields"] == ["parent_id"]


@pytest.mark.asyncio
async def test_get_template_raw(client: AsyncClient, auth_headers: dict):
    """Test getting a child template without inheritance resolution."""
    # Create parent template
    parent_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "value": "RAW_PARENT",
            "label": "Raw Parent",
            "fields": [
                {"name": "parent_field", "label": "Parent Field", "type": "string"}
            ]
        }
    )
    parent_id = parent_response.json()["template_id"]

    # Create child template
    child_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "value": "RAW_CHILD",
            "label": "Raw Child",
            "extends": parent_id,
            "fields": [
                {"name": "child_field", "label": "Child Field", "type": "string"}
            ]
        }
    )
    child_id = child_response.json()["template_id"]

    # Get child template raw
    response = await client.get(
        f"/api/template-store/templates/{child_id}/raw",
        headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()

    # Should only have child fields (not resolved)
    field_names = [f["name"] for f in data["fields"]]
    assert "child_field" in field_names
    assert "parent_field" not in field_names
    assert len(data["fields"]) == 1


@pytest.mark.asyncio
async def test_field_override(client: AsyncClient, auth_headers: dict):
    """Test that child fields override parent fields of the same name."""
    # Create parent template
    parent_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "value": "OVERRIDE_PARENT",
            "label": "Override Parent",
            "fields": [
                {
                    "name": "common_field",
                    "label": "Parent Label",
                    "type": "string",
                    "mandatory": False
                }
            ]
        }
    )
    parent_id = parent_response.json()["template_id"]

    # Create child template with override
    child_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "value": "OVERRIDE_CHILD",
            "label": "Override Child",
            "extends": parent_id,
            "fields": [
                {
                    "name": "common_field",
                    "label": "Child Label",
                    "type": "string",
                    "mandatory": True
                }
            ]
        }
    )
    child_id = child_response.json()["template_id"]

    # Get resolved child template
    response = await client.get(
        f"/api/template-store/templates/{child_id}",
        headers=auth_headers
    )
    data = response.json()

    # Child's field should override parent's
    assert len(data["fields"]) == 1
    assert data["fields"][0]["label"] == "Child Label"
    assert data["fields"][0]["mandatory"] == True


@pytest.mark.asyncio
async def test_rules_merged(client: AsyncClient, auth_headers: dict):
    """Test that rules from parent and child are merged."""
    # Create parent template with rule
    parent_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "value": "RULES_PARENT",
            "label": "Rules Parent",
            "fields": [
                {"name": "field_a", "label": "Field A", "type": "string"},
                {"name": "field_b", "label": "Field B", "type": "string"}
            ],
            "rules": [
                {
                    "type": "dependency",
                    "description": "Parent rule",
                    "target_field": "field_b",
                    "conditions": [{"field": "field_a", "operator": "exists"}]
                }
            ]
        }
    )
    parent_id = parent_response.json()["template_id"]

    # Create child template with additional rule
    child_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "value": "RULES_CHILD",
            "label": "Rules Child",
            "extends": parent_id,
            "fields": [
                {"name": "field_c", "label": "Field C", "type": "string"}
            ],
            "rules": [
                {
                    "type": "mutual_exclusion",
                    "description": "Child rule",
                    "target_fields": ["field_a", "field_c"]
                }
            ]
        }
    )
    child_id = child_response.json()["template_id"]

    # Get resolved child template
    response = await client.get(
        f"/api/template-store/templates/{child_id}",
        headers=auth_headers
    )
    data = response.json()

    # Should have both parent and child rules
    assert len(data["rules"]) == 2
    rule_types = [r["type"] for r in data["rules"]]
    assert "dependency" in rule_types
    assert "mutual_exclusion" in rule_types


@pytest.mark.asyncio
async def test_child_identity_fields_override(client: AsyncClient, auth_headers: dict):
    """Test that child identity_fields override parent's if specified."""
    # Create parent template
    parent_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "value": "ID_PARENT",
            "label": "ID Parent",
            "identity_fields": ["parent_id"],
            "fields": [
                {"name": "parent_id", "label": "Parent ID", "type": "string"}
            ]
        }
    )
    parent_id = parent_response.json()["template_id"]

    # Create child template with own identity_fields
    child_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "value": "ID_CHILD",
            "label": "ID Child",
            "extends": parent_id,
            "identity_fields": ["child_id"],
            "fields": [
                {"name": "child_id", "label": "Child ID", "type": "string"}
            ]
        }
    )
    child_id = child_response.json()["template_id"]

    # Get resolved child template
    response = await client.get(
        f"/api/template-store/templates/{child_id}",
        headers=auth_headers
    )
    data = response.json()

    # Child's identity_fields should win
    assert data["identity_fields"] == ["child_id"]


@pytest.mark.asyncio
async def test_create_with_invalid_extends(client: AsyncClient, auth_headers: dict):
    """Test creating a template with non-existent parent fails."""
    response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "value": "INVALID_EXTENDS",
            "label": "Invalid Extends",
            "extends": "TPL-999999"
        }
    )
    assert response.status_code == 400
    assert "not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_children(client: AsyncClient, auth_headers: dict):
    """Test getting templates that directly extend a template."""
    # Create parent template
    parent_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={"value": "CHILDREN_PARENT", "label": "Children Parent"}
    )
    parent_id = parent_response.json()["template_id"]

    # Create child templates
    await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={"value": "CHILDREN_CHILD_1", "label": "Child 1", "extends": parent_id}
    )
    await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={"value": "CHILDREN_CHILD_2", "label": "Child 2", "extends": parent_id}
    )

    # Get children
    response = await client.get(
        f"/api/template-store/templates/{parent_id}/children",
        headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2


@pytest.mark.asyncio
async def test_get_descendants(client: AsyncClient, auth_headers: dict):
    """Test getting all templates that extend a template (including indirect)."""
    # Create hierarchy: grandparent -> parent -> child
    grandparent_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={"value": "GRANDPARENT", "label": "Grandparent"}
    )
    grandparent_id = grandparent_response.json()["template_id"]

    parent_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={"value": "MIDDLE_PARENT", "label": "Parent", "extends": grandparent_id}
    )
    parent_id = parent_response.json()["template_id"]

    await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={"value": "GRANDCHILD", "label": "Grandchild", "extends": parent_id}
    )

    # Get descendants of grandparent
    response = await client.get(
        f"/api/template-store/templates/{grandparent_id}/descendants",
        headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2  # parent and grandchild


@pytest.mark.asyncio
async def test_delete_template_with_children_fails(client: AsyncClient, auth_headers: dict):
    """Test that deleting a template with children fails."""
    # Create parent template
    parent_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={"value": "DELETE_PARENT", "label": "Delete Parent"}
    )
    parent_id = parent_response.json()["template_id"]

    # Create child template
    await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={"value": "DELETE_CHILD", "label": "Delete Child", "extends": parent_id}
    )

    # Try to delete parent - should fail
    response = await client.delete(
        f"/api/template-store/templates/{parent_id}",
        headers=auth_headers
    )
    assert response.status_code == 409
    assert "extend" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_multi_level_inheritance(client: AsyncClient, auth_headers: dict):
    """Test inheritance across multiple levels."""
    # Create 3-level hierarchy
    level1_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "value": "LEVEL1",
            "label": "Level 1",
            "fields": [{"name": "field1", "label": "Field 1", "type": "string"}]
        }
    )
    level1_id = level1_response.json()["template_id"]

    level2_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "value": "LEVEL2",
            "label": "Level 2",
            "extends": level1_id,
            "fields": [{"name": "field2", "label": "Field 2", "type": "string"}]
        }
    )
    level2_id = level2_response.json()["template_id"]

    level3_response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json={
            "value": "LEVEL3",
            "label": "Level 3",
            "extends": level2_id,
            "fields": [{"name": "field3", "label": "Field 3", "type": "string"}]
        }
    )
    level3_id = level3_response.json()["template_id"]

    # Get level 3 template - should have all 3 fields
    response = await client.get(
        f"/api/template-store/templates/{level3_id}",
        headers=auth_headers
    )
    data = response.json()
    field_names = [f["name"] for f in data["fields"]]
    assert "field1" in field_names
    assert "field2" in field_names
    assert "field3" in field_names
    assert len(data["fields"]) == 3
