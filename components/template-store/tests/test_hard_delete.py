"""Tests for hard-delete support in Template-Store.

Covers:
- Version-specific hard-delete (removes one version, others remain)
- All-version hard-delete (removes all + Registry entry)
- Hard-delete rejected in 'retain' namespace
- Hard-delete blocked when child templates exist
- Soft-delete regression (unchanged when hard_delete=False)
"""

import pytest

# =========================================================================
# Helpers
# =========================================================================


async def _create_template(client, auth_headers, value, label, fields=None, namespace="wip", extends=None):
    """Create a template and return its ID."""
    payload = {
        "value": value,
        "label": label,
        "namespace": namespace,
        "fields": fields or [
            {"name": "name", "label": "Name", "type": "string", "mandatory": True},
        ],
    }
    if extends:
        payload["extends"] = extends
    response = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json=[payload],
    )
    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] >= 1, f"Failed to create template: {data}"
    return data["results"][0]["id"]


async def _create_version(client, auth_headers, template_id, fields=None):
    """Create a new version of a template via PUT (update)."""
    payload = {
        "template_id": template_id,
        "label": "Updated Version",
        "fields": fields or [
            {"name": "name", "label": "Name", "type": "string", "mandatory": True},
            {"name": "extra", "label": "Extra", "type": "string", "mandatory": False},
        ],
    }
    response = await client.put(
        "/api/template-store/templates",
        headers=auth_headers,
        json=[payload],
    )
    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] >= 1, f"Failed to create version: {data}"
    return data["results"][0]


async def _delete_template(client, auth_headers, template_id, hard_delete=False, version=None):
    """Delete a template via bulk endpoint."""
    item = {"id": template_id, "hard_delete": hard_delete}
    if version is not None:
        item["version"] = version
    response = await client.request(
        "DELETE",
        "/api/template-store/templates",
        headers=auth_headers,
        json=[item],
    )
    assert response.status_code == 200
    return response.json()


async def _get_template(client, auth_headers, template_id, version=None):
    """Get a template, optionally a specific version."""
    url = f"/api/template-store/templates/{template_id}"
    if version is not None:
        url += f"?version={version}"
    return await client.get(url, headers=auth_headers)


# =========================================================================
# Hard-Delete All Versions
# =========================================================================


class TestHardDeleteAllVersions:
    """Tests for hard-deleting all versions of a template."""

    @pytest.mark.asyncio
    async def test_hard_delete_removes_all_versions(self, client, auth_headers):
        """hard_delete=True with version=None removes all versions."""
        from unittest.mock import AsyncMock, patch

        tid = await _create_template(client, auth_headers, "HD_ALL", "Hard Delete All")
        await _create_version(client, auth_headers, tid)  # v2

        with patch('template_store.services.template_service.get_registry_client') as mock_get:
            mock_rc = AsyncMock()
            mock_rc.get_namespace_deletion_mode = AsyncMock(return_value="full")
            mock_rc.hard_delete_entry = AsyncMock(return_value=True)
            mock_get.return_value = mock_rc

            data = await _delete_template(client, auth_headers, tid, hard_delete=True)

        assert data["succeeded"] == 1

        # Template completely gone
        resp = await _get_template(client, auth_headers, tid)
        assert resp.status_code == 404

        # Registry entry should have been hard-deleted
        mock_rc.hard_delete_entry.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_hard_delete_rejected_in_retain_namespace(self, client, auth_headers):
        """hard_delete=True fails when namespace deletion_mode='retain'."""
        from unittest.mock import AsyncMock, patch

        tid = await _create_template(client, auth_headers, "HD_RETAIN", "Retain Test")

        with patch('template_store.services.template_service.get_registry_client') as mock_get:
            mock_rc = AsyncMock()
            mock_rc.get_namespace_deletion_mode = AsyncMock(return_value="retain")
            mock_get.return_value = mock_rc

            data = await _delete_template(client, auth_headers, tid, hard_delete=True)

        assert data["failed"] == 1
        assert "deletion_mode" in data["results"][0]["error"]

        # Template should still exist
        resp = await _get_template(client, auth_headers, tid)
        assert resp.status_code == 200


# =========================================================================
# Hard-Delete Specific Version
# =========================================================================


class TestHardDeleteSpecificVersion:
    """Tests for version-specific hard-delete."""

    @pytest.mark.asyncio
    async def test_hard_delete_one_version_keeps_others(self, client, auth_headers):
        """Hard-deleting v1 keeps v2 intact."""
        from unittest.mock import AsyncMock, patch

        tid = await _create_template(client, auth_headers, "HD_VER", "Version Test")
        await _create_version(client, auth_headers, tid)  # v2

        with patch('template_store.services.template_service.get_registry_client') as mock_get:
            mock_rc = AsyncMock()
            mock_rc.get_namespace_deletion_mode = AsyncMock(return_value="full")
            mock_rc.hard_delete_entry = AsyncMock(return_value=True)
            mock_get.return_value = mock_rc

            data = await _delete_template(client, auth_headers, tid, hard_delete=True, version=1)

        assert data["succeeded"] == 1

        # v1 should be gone, but template still exists (v2 remains)
        resp = await _get_template(client, auth_headers, tid)
        assert resp.status_code == 200

        # Registry entry should NOT have been hard-deleted (versions remain)
        mock_rc.hard_delete_entry.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_hard_delete_last_version_cleans_registry(self, client, auth_headers):
        """Hard-deleting the only version also removes Registry entry."""
        from unittest.mock import AsyncMock, patch

        tid = await _create_template(client, auth_headers, "HD_LAST", "Last Version")

        with patch('template_store.services.template_service.get_registry_client') as mock_get:
            mock_rc = AsyncMock()
            mock_rc.get_namespace_deletion_mode = AsyncMock(return_value="full")
            mock_rc.hard_delete_entry = AsyncMock(return_value=True)
            mock_get.return_value = mock_rc

            data = await _delete_template(client, auth_headers, tid, hard_delete=True, version=1)

        assert data["succeeded"] == 1

        # Template gone
        resp = await _get_template(client, auth_headers, tid)
        assert resp.status_code == 404

        # Registry entry should have been cleaned up
        mock_rc.hard_delete_entry.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_hard_delete_nonexistent_version(self, client, auth_headers):
        """Hard-deleting a version that doesn't exist returns failure."""
        from unittest.mock import AsyncMock, patch

        tid = await _create_template(client, auth_headers, "HD_NOVERSION", "No Version")

        with patch('template_store.services.template_service.get_registry_client') as mock_get:
            mock_rc = AsyncMock()
            mock_rc.get_namespace_deletion_mode = AsyncMock(return_value="full")
            mock_get.return_value = mock_rc

            data = await _delete_template(client, auth_headers, tid, hard_delete=True, version=99)

        assert data["failed"] == 1


# =========================================================================
# Hard-Delete Blocked by Children
# =========================================================================


class TestHardDeleteBlockedByChildren:
    """Tests that hard-delete is blocked when child templates extend the target."""

    @pytest.mark.asyncio
    async def test_hard_delete_blocked_by_child_template(self, client, auth_headers):
        """Cannot hard-delete a template that has children extending it."""
        from unittest.mock import AsyncMock, patch

        parent_id = await _create_template(client, auth_headers, "HD_PARENT", "Parent")
        await _create_template(
            client, auth_headers, "HD_CHILD", "Child",
            extends=parent_id,
            fields=[{"name": "child_field", "label": "CF", "type": "string", "mandatory": False}],
        )

        with patch('template_store.services.template_service.get_registry_client') as mock_get:
            mock_rc = AsyncMock()
            mock_rc.get_namespace_deletion_mode = AsyncMock(return_value="full")
            mock_get.return_value = mock_rc

            data = await _delete_template(client, auth_headers, parent_id, hard_delete=True)

        assert data["failed"] == 1
        assert "extend" in data["results"][0]["error"].lower()


# =========================================================================
# Soft-Delete Regression
# =========================================================================


class TestSoftDeleteRegression:
    """Verify soft-delete behavior is unchanged."""

    @pytest.mark.asyncio
    async def test_soft_delete_sets_inactive(self, client, auth_headers):
        """Default delete sets template status to inactive."""
        tid = await _create_template(client, auth_headers, "SOFT_TPL", "Soft Delete")

        data = await _delete_template(client, auth_headers, tid)
        assert data["succeeded"] == 1

        # Template still exists, just inactive
        resp = await _get_template(client, auth_headers, tid)
        assert resp.status_code == 200
        assert resp.json()["status"] == "inactive"
