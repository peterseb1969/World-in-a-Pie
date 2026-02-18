"""Tests for template version management and extends_version pinning."""

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


async def _update_template(
    client: AsyncClient, auth_headers: dict, template_id: str, updates: dict
) -> dict:
    """Update a template via the bulk PUT and return the BulkResponse JSON."""
    payload = {"template_id": template_id, **updates}
    response = await client.put(
        "/api/template-store/templates",
        headers=auth_headers,
        json=[payload],
    )
    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == 1, f"Update failed: {data}"
    return data


# ---------------------------------------------------------------------------
# Tests: Get All Versions by Value
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_all_versions_by_value(client: AsyncClient, auth_headers: dict):
    """Test GET /templates/by-value/{value}/versions returns all versions."""
    # Create a template
    template_id = await _create_one_id(client, auth_headers, {
        "value": "VERSIONED",
        "label": "Versioned Template v1",
        "fields": [
            {"name": "field1", "label": "Field 1", "type": "string"},
        ],
    })

    # Update to create version 2
    await _update_template(client, auth_headers, template_id, {
        "label": "Versioned Template v2",
        "fields": [
            {"name": "field1", "label": "Field 1", "type": "string"},
            {"name": "field2", "label": "Field 2", "type": "string"},
        ],
    })

    # Update to create version 3
    await _update_template(client, auth_headers, template_id, {
        "label": "Versioned Template v3",
        "fields": [
            {"name": "field1", "label": "Field 1", "type": "string"},
            {"name": "field2", "label": "Field 2", "type": "string"},
            {"name": "field3", "label": "Field 3", "type": "string"},
        ],
    })

    # Get all versions
    resp = await client.get(
        "/api/template-store/templates/by-value/VERSIONED/versions",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3

    # Should be sorted by version descending (newest first)
    versions = [item["version"] for item in data["items"]]
    assert versions == [3, 2, 1]

    # All should share the same template_id (stable across versions)
    ids = {item["template_id"] for item in data["items"]}
    assert len(ids) == 1
    assert template_id in ids


@pytest.mark.asyncio
async def test_get_versions_not_found(client: AsyncClient, auth_headers: dict):
    """Test GET /templates/by-value/{value}/versions for non-existent template."""
    resp = await client.get(
        "/api/template-store/templates/by-value/NONEXISTENT_VALUE/versions",
        headers=auth_headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: Get Specific Version by Value
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_specific_version_by_value(client: AsyncClient, auth_headers: dict):
    """Test GET /templates/by-value/{value}/versions/{version}."""
    template_id = await _create_one_id(client, auth_headers, {
        "value": "SPECIFIC_VER",
        "label": "Specific Version v1",
        "fields": [
            {"name": "field1", "label": "Field 1", "type": "string"},
        ],
    })

    # Create version 2
    await _update_template(client, auth_headers, template_id, {
        "label": "Specific Version v2",
        "fields": [
            {"name": "field1", "label": "Field 1", "type": "string"},
            {"name": "field2", "label": "Field 2", "type": "string"},
        ],
    })

    # Get version 1 specifically
    resp_v1 = await client.get(
        "/api/template-store/templates/by-value/SPECIFIC_VER/versions/1",
        headers=auth_headers,
    )
    assert resp_v1.status_code == 200
    data_v1 = resp_v1.json()
    assert data_v1["version"] == 1
    assert data_v1["label"] == "Specific Version v1"
    assert len(data_v1["fields"]) == 1

    # Get version 2 specifically
    resp_v2 = await client.get(
        "/api/template-store/templates/by-value/SPECIFIC_VER/versions/2",
        headers=auth_headers,
    )
    assert resp_v2.status_code == 200
    data_v2 = resp_v2.json()
    assert data_v2["version"] == 2
    assert data_v2["label"] == "Specific Version v2"
    assert len(data_v2["fields"]) == 2


@pytest.mark.asyncio
async def test_get_specific_version_not_found(client: AsyncClient, auth_headers: dict):
    """Test GET /templates/by-value/{value}/versions/{version} for
    non-existent version number."""
    await _create_one(client, auth_headers, {
        "value": "VER_404",
        "label": "Version 404",
        "fields": [],
    })

    # Version 99 does not exist
    resp = await client.get(
        "/api/template-store/templates/by-value/VER_404/versions/99",
        headers=auth_headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: Multiple Active Versions Simultaneously
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multiple_active_versions(client: AsyncClient, auth_headers: dict):
    """Test that multiple versions of a template can be active simultaneously.

    When a template is updated, a new version is created but the old one
    remains active. Both can be retrieved.
    """
    template_id = await _create_one_id(client, auth_headers, {
        "value": "MULTI_ACTIVE",
        "label": "Multi Active v1",
        "fields": [
            {"name": "name", "label": "Name", "type": "string"},
        ],
    })

    # Create version 2
    await _update_template(client, auth_headers, template_id, {
        "label": "Multi Active v2",
        "description": "Updated description",
    })

    # Both versions should be retrievable and active
    # Get version 1 by ID + version param
    resp_v1 = await client.get(
        f"/api/template-store/templates/{template_id}?version=1",
        headers=auth_headers,
    )
    assert resp_v1.status_code == 200
    assert resp_v1.json()["version"] == 1
    assert resp_v1.json()["status"] == "active"

    # Get version 2 by ID + version param
    resp_v2 = await client.get(
        f"/api/template-store/templates/{template_id}?version=2",
        headers=auth_headers,
    )
    assert resp_v2.status_code == 200
    assert resp_v2.json()["version"] == 2
    assert resp_v2.json()["status"] == "active"

    # Default (no version param) returns latest
    resp_latest = await client.get(
        f"/api/template-store/templates/{template_id}",
        headers=auth_headers,
    )
    assert resp_latest.status_code == 200
    assert resp_latest.json()["version"] == 2


@pytest.mark.asyncio
async def test_deactivate_specific_version(client: AsyncClient, auth_headers: dict):
    """Test deactivating a specific version while others remain active."""
    template_id = await _create_one_id(client, auth_headers, {
        "value": "DEACT_VER",
        "label": "Deactivate Version v1",
        "fields": [
            {"name": "name", "label": "Name", "type": "string"},
        ],
    })

    # Create version 2
    await _update_template(client, auth_headers, template_id, {
        "label": "Deactivate Version v2",
        "description": "v2 description",
    })

    # Deactivate version 1
    resp = await client.request(
        "DELETE",
        "/api/template-store/templates",
        headers=auth_headers,
        json=[{"id": template_id, "version": 1}],
    )
    assert resp.status_code == 200
    assert resp.json()["succeeded"] == 1

    # Version 1 is now inactive
    resp_v1 = await client.get(
        f"/api/template-store/templates/{template_id}?version=1",
        headers=auth_headers,
    )
    assert resp_v1.status_code == 200
    assert resp_v1.json()["status"] == "inactive"

    # Version 2 remains active
    resp_v2 = await client.get(
        f"/api/template-store/templates/{template_id}?version=2",
        headers=auth_headers,
    )
    assert resp_v2.status_code == 200
    assert resp_v2.json()["status"] == "active"


# ---------------------------------------------------------------------------
# Tests: extends_version Pinning
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_child_with_extends_version(client: AsyncClient, auth_headers: dict):
    """Test creating a child template that pins to a specific parent version."""
    parent_id = await _create_one_id(client, auth_headers, {
        "value": "PIN_PARENT",
        "label": "Pin Parent v1",
        "fields": [
            {"name": "field_a", "label": "Field A", "type": "string"},
        ],
    })

    # Create version 2 of parent
    await _update_template(client, auth_headers, parent_id, {
        "label": "Pin Parent v2",
        "fields": [
            {"name": "field_a", "label": "Field A", "type": "string"},
            {"name": "field_b", "label": "Field B", "type": "string"},
        ],
    })

    # Create child that pins to parent v1
    child_id = await _create_one_id(client, auth_headers, {
        "value": "PIN_CHILD",
        "label": "Pin Child",
        "extends": parent_id,
        "extends_version": 1,
        "fields": [
            {"name": "child_field", "label": "Child Field", "type": "string"},
        ],
    })

    # Verify the child raw data has extends_version set
    resp_raw = await client.get(
        f"/api/template-store/templates/{child_id}/raw",
        headers=auth_headers,
    )
    assert resp_raw.status_code == 200
    raw = resp_raw.json()
    assert raw["extends"] == parent_id
    assert raw["extends_version"] == 1


@pytest.mark.asyncio
async def test_pinned_version_inheritance_uses_correct_version(
    client: AsyncClient, auth_headers: dict
):
    """Test that inheritance resolution uses the pinned version, not latest.

    Parent v1 has field_a. Parent v2 adds field_b. Child pins to v1.
    Resolved child should only inherit field_a from parent, not field_b.
    """
    parent_id = await _create_one_id(client, auth_headers, {
        "value": "PINNED_RESOLVE_PARENT",
        "label": "Pinned Resolve Parent v1",
        "fields": [
            {"name": "field_a", "label": "Field A", "type": "string"},
        ],
    })

    # Create parent version 2 with additional field
    await _update_template(client, auth_headers, parent_id, {
        "label": "Pinned Resolve Parent v2",
        "fields": [
            {"name": "field_a", "label": "Field A", "type": "string"},
            {"name": "field_b", "label": "Field B (v2 only)", "type": "string"},
        ],
    })

    # Create child pinned to parent v1
    child_id = await _create_one_id(client, auth_headers, {
        "value": "PINNED_RESOLVE_CHILD",
        "label": "Pinned Resolve Child",
        "extends": parent_id,
        "extends_version": 1,
        "fields": [
            {"name": "child_field", "label": "Child Field", "type": "string"},
        ],
    })

    # Get resolved child -- should have field_a from parent v1 + child_field
    resp = await client.get(
        f"/api/template-store/templates/{child_id}",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    field_names = [f["name"] for f in data["fields"]]
    assert "field_a" in field_names      # Inherited from parent v1
    assert "child_field" in field_names  # Child's own field
    assert "field_b" not in field_names  # NOT inherited (parent v2 field)
    assert len(data["fields"]) == 2


@pytest.mark.asyncio
async def test_unpinned_inherits_latest_version(client: AsyncClient, auth_headers: dict):
    """Test that without extends_version, the child inherits from the
    latest active parent version."""
    parent_id = await _create_one_id(client, auth_headers, {
        "value": "UNPINNED_PARENT",
        "label": "Unpinned Parent v1",
        "fields": [
            {"name": "field_a", "label": "Field A", "type": "string"},
        ],
    })

    # Create child without extends_version (unpinned)
    child_id = await _create_one_id(client, auth_headers, {
        "value": "UNPINNED_CHILD",
        "label": "Unpinned Child",
        "extends": parent_id,
        "fields": [
            {"name": "child_field", "label": "Child Field", "type": "string"},
        ],
    })

    # Create parent version 2
    await _update_template(client, auth_headers, parent_id, {
        "label": "Unpinned Parent v2",
        "fields": [
            {"name": "field_a", "label": "Field A", "type": "string"},
            {"name": "field_b", "label": "Field B (v2)", "type": "string"},
        ],
    })

    # Get resolved child -- should inherit from latest parent (v2)
    resp = await client.get(
        f"/api/template-store/templates/{child_id}",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    field_names = [f["name"] for f in data["fields"]]
    assert "field_a" in field_names      # From parent v2
    assert "field_b" in field_names      # From parent v2 (new field)
    assert "child_field" in field_names  # Child's own field
    assert len(data["fields"]) == 3


# ---------------------------------------------------------------------------
# Tests: Cascade Update to Children
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cascade_to_children(client: AsyncClient, auth_headers: dict):
    """Test POST /templates/{id}/cascade creates new child versions
    pointing to the updated parent."""
    parent_id = await _create_one_id(client, auth_headers, {
        "value": "CASCADE_PARENT",
        "label": "Cascade Parent v1",
        "fields": [
            {"name": "base", "label": "Base", "type": "string"},
        ],
    })

    # Create two children extending the parent
    child1_id = await _create_one_id(client, auth_headers, {
        "value": "CASCADE_CHILD_1",
        "label": "Cascade Child 1",
        "extends": parent_id,
        "fields": [
            {"name": "c1_field", "label": "C1 Field", "type": "string"},
        ],
    })

    child2_id = await _create_one_id(client, auth_headers, {
        "value": "CASCADE_CHILD_2",
        "label": "Cascade Child 2",
        "extends": parent_id,
        "fields": [
            {"name": "c2_field", "label": "C2 Field", "type": "string"},
        ],
    })

    # Update parent to version 2
    update_data = await _update_template(client, auth_headers, parent_id, {
        "label": "Cascade Parent v2",
        "fields": [
            {"name": "base", "label": "Base", "type": "string"},
            {"name": "new_field", "label": "New Field", "type": "integer"},
        ],
    })
    assert update_data["results"][0]["version"] == 2

    # Cascade to children
    resp = await client.post(
        f"/api/template-store/templates/{parent_id}/cascade",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["parent_template_id"] == parent_id
    assert data["parent_version"] == 2
    assert data["total"] == 2
    assert data["updated"] == 2
    assert data["failed"] == 0

    # Verify each child now has a new version
    for child_id in [child1_id, child2_id]:
        resp_child = await client.get(
            f"/api/template-store/templates/{child_id}",
            headers=auth_headers,
        )
        assert resp_child.status_code == 200
        child_data = resp_child.json()
        # New version was created
        assert child_data["version"] == 2
        # Extends now points to the (same stable) parent_id
        assert child_data["extends"] == parent_id


@pytest.mark.asyncio
async def test_cascade_no_children(client: AsyncClient, auth_headers: dict):
    """Test cascade on a template with no children returns empty results."""
    template_id = await _create_one_id(client, auth_headers, {
        "value": "NO_CHILDREN",
        "label": "No Children",
        "fields": [],
    })

    resp = await client.post(
        f"/api/template-store/templates/{template_id}/cascade",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["updated"] == 0
    assert data["unchanged"] == 0
    assert data["failed"] == 0
    assert data["results"] == []


@pytest.mark.asyncio
async def test_cascade_nonexistent_template(client: AsyncClient, auth_headers: dict):
    """Test cascade on a non-existent template returns 404."""
    resp = await client.post(
        "/api/template-store/templates/TPL-999999/cascade",
        headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cascade_already_pointing_to_latest(client: AsyncClient, auth_headers: dict):
    """Test cascade when children already extend the target parent
    returns unchanged status."""
    parent_id = await _create_one_id(client, auth_headers, {
        "value": "CASCADE_NOOP_PARENT",
        "label": "Cascade Noop Parent",
        "fields": [
            {"name": "base", "label": "Base", "type": "string"},
        ],
    })

    # Create child extending the parent
    await _create_one_id(client, auth_headers, {
        "value": "CASCADE_NOOP_CHILD",
        "label": "Cascade Noop Child",
        "extends": parent_id,
        "fields": [
            {"name": "c_field", "label": "C Field", "type": "string"},
        ],
    })

    # Cascade without updating parent -- children already extend the same parent_id
    resp = await client.post(
        f"/api/template-store/templates/{parent_id}/cascade",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    # Child already points to parent_id, so it should be unchanged
    assert data["unchanged"] == 1
    assert data["updated"] == 0


# ---------------------------------------------------------------------------
# Tests: Update Creates New Version (Stable ID)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_preserves_template_id(client: AsyncClient, auth_headers: dict):
    """Test that updating a template creates a new version document with
    the same template_id (stable ID across versions)."""
    template_id = await _create_one_id(client, auth_headers, {
        "value": "STABLE_ID",
        "label": "Stable ID v1",
        "fields": [
            {"name": "name", "label": "Name", "type": "string"},
        ],
    })

    # Update
    update_data = await _update_template(client, auth_headers, template_id, {
        "label": "Stable ID v2",
        "description": "Updated",
    })

    result = update_data["results"][0]
    assert result["id"] == template_id     # Same stable ID
    assert result["version"] == 2          # New version number
    assert result["is_new_version"] is True


@pytest.mark.asyncio
async def test_no_change_update_returns_same_version(client: AsyncClient, auth_headers: dict):
    """Test that updating with identical data does not create a new version."""
    template_id = await _create_one_id(client, auth_headers, {
        "value": "NOOP_UPDATE",
        "label": "No Op Update",
        "fields": [
            {"name": "name", "label": "Name", "type": "string"},
        ],
    })

    # Send update with same label (no actual change)
    response = await client.put(
        "/api/template-store/templates",
        headers=auth_headers,
        json=[{"template_id": template_id, "label": "No Op Update"}],
    )
    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == 1
    result = data["results"][0]
    assert result["version"] == 1           # Still version 1
    assert result["is_new_version"] is False


@pytest.mark.asyncio
async def test_get_by_value_returns_latest(client: AsyncClient, auth_headers: dict):
    """Test GET /templates/by-value/{value} returns the latest version."""
    template_id = await _create_one_id(client, auth_headers, {
        "value": "LATEST_BY_VALUE",
        "label": "Latest v1",
        "fields": [
            {"name": "name", "label": "Name", "type": "string"},
        ],
    })

    # Create version 2
    await _update_template(client, auth_headers, template_id, {
        "label": "Latest v2",
        "description": "Version 2",
    })

    # Get by value (should return latest)
    resp = await client.get(
        "/api/template-store/templates/by-value/LATEST_BY_VALUE",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == 2
    assert data["label"] == "Latest v2"
