"""Tests for template dependency checking."""

from unittest.mock import patch

import pytest
from httpx import AsyncClient


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
# Tests: No Dependencies
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dependencies_no_dependents(client: AsyncClient, auth_headers: dict):
    """Test dependency check on a template with no dependents."""
    template_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "NO_DEPS",
        "label": "No Dependencies",
        "fields": [
            {"name": "name", "label": "Name", "type": "string"},
        ],
    })

    resp = await client.get(
        f"/api/template-store/templates/{template_id}/dependencies",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["template_id"] == template_id
    assert data["template_value"] == "NO_DEPS"
    assert data["child_template_count"] == 0
    assert data["child_templates"] == []
    assert data["can_deactivate"] is True


@pytest.mark.asyncio
async def test_dependencies_nonexistent_template(client: AsyncClient, auth_headers: dict):
    """Test dependency check on a non-existent template returns 404."""
    resp = await client.get(
        "/api/template-store/templates/TPL-999999/dependencies",
        headers=auth_headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: Child Template Dependencies
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dependencies_with_child_templates(client: AsyncClient, auth_headers: dict):
    """Test dependency check reports child templates that extend this one."""
    parent_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "DEP_PARENT",
        "label": "Dep Parent",
        "fields": [
            {"name": "id", "label": "ID", "type": "string"},
        ],
    })

    # Create two children
    child1_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "DEP_CHILD_1",
        "label": "Dep Child 1",
        "extends": parent_id,
        "fields": [
            {"name": "extra", "label": "Extra", "type": "string"},
        ],
    })

    child2_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "DEP_CHILD_2",
        "label": "Dep Child 2",
        "extends": parent_id,
        "fields": [],
    })

    resp = await client.get(
        f"/api/template-store/templates/{parent_id}/dependencies",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["template_id"] == parent_id
    assert data["child_template_count"] == 2
    assert len(data["child_templates"]) == 2
    assert data["has_dependencies"] is True
    assert data["can_deactivate"] is False  # Cannot deactivate with children

    # Verify child template details
    child_ids = {c["template_id"] for c in data["child_templates"]}
    assert child1_id in child_ids
    assert child2_id in child_ids

    # Warning message should mention templates
    assert data["warning_message"] is not None
    assert "template" in data["warning_message"].lower()


@pytest.mark.asyncio
async def test_dependencies_prevents_delete_with_children(
    client: AsyncClient, auth_headers: dict
):
    """Test that delete is blocked when child templates exist,
    matching the behavior shown in the dependency check."""
    parent_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "DEL_BLOCK_PARENT",
        "label": "Delete Block Parent",
        "fields": [],
    })

    await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "DEL_BLOCK_CHILD",
        "label": "Delete Block Child",
        "extends": parent_id,
        "fields": [],
    })

    # Try to delete parent
    resp = await client.request(
        "DELETE",
        "/api/template-store/templates",
        headers=auth_headers,
        json=[{"id": parent_id}],
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["failed"] == 1
    assert "extend" in data["results"][0]["error"].lower()


@pytest.mark.asyncio
async def test_dependencies_allows_delete_without_children(
    client: AsyncClient, auth_headers: dict
):
    """Test that delete succeeds when there are no child templates."""
    template_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "DEL_OK",
        "label": "Delete OK",
        "fields": [],
    })

    # Verify no dependencies
    dep_resp = await client.get(
        f"/api/template-store/templates/{template_id}/dependencies",
        headers=auth_headers,
    )
    assert dep_resp.status_code == 200
    assert dep_resp.json()["can_deactivate"] is True

    # Delete should succeed
    resp = await client.request(
        "DELETE",
        "/api/template-store/templates",
        headers=auth_headers,
        json=[{"id": template_id}],
    )
    assert resp.status_code == 200
    assert resp.json()["succeeded"] == 1


# ---------------------------------------------------------------------------
# Tests: Document Store Unavailable
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dependencies_document_store_unavailable(
    client: AsyncClient, auth_headers: dict
):
    """Test dependency check when Document Store is unavailable.

    The document_count should be -1 (unknown) and the response should
    include a warning about the Document Store being unreachable.
    """
    template_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "DS_UNAVAIL",
        "label": "DS Unavailable",
        "fields": [],
    })

    # Mock _get_document_count to raise an exception (simulating unavailable Document Store)
    from template_store.services.dependency_service import DependencyService

    with patch.object(DependencyService, "_get_document_count", side_effect=Exception("Connection refused")):
        resp = await client.get(
            f"/api/template-store/templates/{template_id}/dependencies",
            headers=auth_headers,
        )
    assert resp.status_code == 200
    data = resp.json()

    # Document count should be -1 (unknown)
    assert data["document_count"] == -1

    # Warning should mention unavailability
    assert data["warning_message"] is not None
    assert "unavailable" in data["warning_message"].lower()

    # Can still deactivate (no child templates)
    assert data["can_deactivate"] is True


@pytest.mark.asyncio
async def test_dependencies_document_store_unavailable_with_children(
    client: AsyncClient, auth_headers: dict
):
    """Test dependency check when Document Store is unavailable AND children exist.

    Should report both the child templates and the unknown document count.
    """
    parent_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "DS_UNAVAIL_PARENT",
        "label": "DS Unavailable Parent",
        "fields": [],
    })

    await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "DS_UNAVAIL_CHILD",
        "label": "DS Unavailable Child",
        "extends": parent_id,
        "fields": [],
    })

    # Mock _get_document_count to raise an exception (simulating unavailable Document Store)
    from template_store.services.dependency_service import DependencyService

    with patch.object(DependencyService, "_get_document_count", side_effect=Exception("Connection refused")):
        resp = await client.get(
            f"/api/template-store/templates/{parent_id}/dependencies",
            headers=auth_headers,
        )
    assert resp.status_code == 200
    data = resp.json()

    assert data["child_template_count"] == 1
    assert data["document_count"] == -1
    assert data["has_dependencies"] is True
    assert data["can_deactivate"] is False  # Children prevent deactivation


# ---------------------------------------------------------------------------
# Tests: Dependency Details
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dependencies_child_details_include_value_and_label(
    client: AsyncClient, auth_headers: dict
):
    """Test that child template details include template_id, value, and label."""
    parent_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "DETAIL_PARENT",
        "label": "Detail Parent",
        "fields": [],
    })

    child_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "DETAIL_CHILD",
        "label": "Detail Child Label",
        "extends": parent_id,
        "fields": [],
    })

    resp = await client.get(
        f"/api/template-store/templates/{parent_id}/dependencies",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()

    assert len(data["child_templates"]) == 1
    child = data["child_templates"][0]
    assert child["template_id"] == child_id
    assert child["value"] == "DETAIL_CHILD"
    assert child["label"] == "Detail Child Label"


@pytest.mark.asyncio
async def test_dependencies_structure_with_no_children(client: AsyncClient, auth_headers: dict):
    """Test the full structure of the dependency response when there are no
    child templates. Document Store is unavailable in tests, so document_count
    is -1, but child-related fields should all be clean."""
    template_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "CLEAN_DEPS",
        "label": "Clean Dependencies",
        "fields": [],
    })

    resp = await client.get(
        f"/api/template-store/templates/{template_id}/dependencies",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()

    # No child templates
    assert data["child_template_count"] == 0
    assert data["child_templates"] == []

    # Can deactivate since no children
    assert data["can_deactivate"] is True

    # Template info is correct
    assert data["template_id"] == template_id
    assert data["template_value"] == "CLEAN_DEPS"


@pytest.mark.asyncio
async def test_dependencies_with_deep_hierarchy(client: AsyncClient, auth_headers: dict):
    """Test dependency check only reports direct children, not grandchildren."""
    grandparent_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "DEEP_GP",
        "label": "Deep Grandparent",
        "fields": [],
    })

    parent_id = await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "DEEP_P",
        "label": "Deep Parent",
        "extends": grandparent_id,
        "fields": [],
    })

    await _create_one_id(client, auth_headers, {
        "namespace": "wip",
        "value": "DEEP_C",
        "label": "Deep Child",
        "extends": parent_id,
        "fields": [],
    })

    # Grandparent dependencies: only the direct child (parent), not grandchild
    resp = await client.get(
        f"/api/template-store/templates/{grandparent_id}/dependencies",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["child_template_count"] == 1
    assert data["child_templates"][0]["template_id"] == parent_id

    # Parent dependencies: only the grandchild (its direct child)
    resp2 = await client.get(
        f"/api/template-store/templates/{parent_id}/dependencies",
        headers=auth_headers,
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["child_template_count"] == 1
