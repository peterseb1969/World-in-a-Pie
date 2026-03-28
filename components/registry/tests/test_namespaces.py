"""Comprehensive tests for Registry namespace CRUD operations."""

import pytest
from httpx import AsyncClient


class TestCreateNamespace:
    """Tests for creating namespaces."""

    @pytest.mark.asyncio
    async def test_create_namespace_minimal(self, client: AsyncClient, auth_headers: dict):
        """Test creating a namespace with only the required prefix field."""
        response = await client.post(
            "/api/registry/namespaces",
            json={"prefix": "test-minimal"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["prefix"] == "test-minimal"
        assert data["description"] == ""
        assert data["isolation_mode"] == "open"
        assert data["allowed_external_refs"] == []
        assert data["status"] == "active"
        assert data["created_by"] is None
        # id_config should contain defaults for all entity types
        assert "terms" in data["id_config"]
        assert "documents" in data["id_config"]
        assert "templates" in data["id_config"]
        assert "terminologies" in data["id_config"]
        assert "files" in data["id_config"]

    @pytest.mark.asyncio
    async def test_create_namespace_with_description(self, client: AsyncClient, auth_headers: dict):
        """Test creating a namespace with a description and created_by."""
        response = await client.post(
            "/api/registry/namespaces",
            json={
                "prefix": "test-described",
                "description": "A test namespace for validation",
                "created_by": "test-user",
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["prefix"] == "test-described"
        assert data["description"] == "A test namespace for validation"
        assert data["created_by"] == "test-user"

    @pytest.mark.asyncio
    async def test_create_namespace_strict_isolation(self, client: AsyncClient, auth_headers: dict):
        """Test creating a namespace with strict isolation mode."""
        response = await client.post(
            "/api/registry/namespaces",
            json={
                "prefix": "strict-ns",
                "isolation_mode": "strict",
                "description": "Strict isolation namespace",
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["isolation_mode"] == "strict"

    @pytest.mark.asyncio
    async def test_create_namespace_with_allowed_external_refs(self, client: AsyncClient, auth_headers: dict):
        """Test creating a namespace with allowed external refs."""
        response = await client.post(
            "/api/registry/namespaces",
            json={
                "prefix": "open-with-refs",
                "isolation_mode": "open",
                "allowed_external_refs": ["vendor1", "vendor2"],
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["allowed_external_refs"] == ["vendor1", "vendor2"]

    @pytest.mark.asyncio
    async def test_create_namespace_with_custom_id_config(self, client: AsyncClient, auth_headers: dict):
        """Test creating a namespace with custom ID algorithm configuration."""
        response = await client.post(
            "/api/registry/namespaces",
            json={
                "prefix": "custom-ids",
                "description": "Namespace with prefixed IDs for terms",
                "id_config": {
                    "terms": {
                        "algorithm": "prefixed",
                        "prefix": "TERM-",
                        "pad": 8,
                    },
                    "documents": {
                        "algorithm": "uuid4",
                    },
                },
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        # Custom config for terms should be set
        assert data["id_config"]["terms"]["algorithm"] == "prefixed"
        assert data["id_config"]["terms"]["prefix"] == "TERM-"
        assert data["id_config"]["terms"]["pad"] == 8
        # Custom config for documents should be set
        assert data["id_config"]["documents"]["algorithm"] == "uuid4"
        # Entity types without custom config should get defaults (uuid7)
        assert data["id_config"]["templates"]["algorithm"] == "uuid7"
        assert data["id_config"]["terminologies"]["algorithm"] == "uuid7"
        assert data["id_config"]["files"]["algorithm"] == "uuid7"

    @pytest.mark.asyncio
    async def test_create_namespace_duplicate_prefix_fails(self, client: AsyncClient, auth_headers: dict):
        """Test that creating a namespace with a duplicate prefix returns 409."""
        # "default" namespace is created by the conftest fixture
        response = await client.post(
            "/api/registry/namespaces",
            json={"prefix": "default"},
            headers=auth_headers,
        )
        assert response.status_code == 409
        assert "already exists" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_create_namespace_returns_timestamps(self, client: AsyncClient, auth_headers: dict):
        """Test that the created namespace has created_at and updated_at timestamps."""
        response = await client.post(
            "/api/registry/namespaces",
            json={"prefix": "ts-check"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "created_at" in data
        assert "updated_at" in data
        assert data["created_at"] is not None
        assert data["updated_at"] is not None


class TestGetNamespace:
    """Tests for getting a namespace by prefix."""

    @pytest.mark.asyncio
    async def test_get_namespace_by_prefix(self, client: AsyncClient, auth_headers: dict):
        """Test getting an existing namespace by its prefix."""
        response = await client.get(
            "/api/registry/namespaces/default",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["prefix"] == "default"
        assert data["description"] == "Default namespace for testing"
        assert data["status"] == "active"

    @pytest.mark.asyncio
    async def test_get_namespace_not_found(self, client: AsyncClient, auth_headers: dict):
        """Test that requesting a non-existent namespace returns 404."""
        response = await client.get(
            "/api/registry/namespaces/nonexistent-ns",
            headers=auth_headers,
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_get_namespace_includes_id_config(self, client: AsyncClient, auth_headers: dict):
        """Test that the namespace response includes full id_config for all entity types."""
        response = await client.get(
            "/api/registry/namespaces/default",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        id_config = data["id_config"]
        expected_entity_types = {"terminologies", "terms", "templates", "documents", "files"}
        assert set(id_config.keys()) == expected_entity_types


class TestListNamespaces:
    """Tests for listing namespaces."""

    @pytest.mark.asyncio
    async def test_list_namespaces(self, client: AsyncClient, auth_headers: dict):
        """Test listing all active namespaces."""
        response = await client.get(
            "/api/registry/namespaces",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        # conftest creates 3 namespaces: default, vendor1, vendor2
        assert len(data) >= 3
        prefixes = [ns["prefix"] for ns in data]
        assert "default" in prefixes
        assert "vendor1" in prefixes
        assert "vendor2" in prefixes

    @pytest.mark.asyncio
    async def test_list_namespaces_excludes_archived_by_default(self, client: AsyncClient, auth_headers: dict):
        """Test that archived namespaces are excluded by default."""
        # Create and archive a namespace
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "to-archive-list"},
            headers=auth_headers,
        )
        await client.post(
            "/api/registry/namespaces/to-archive-list/archive",
            headers=auth_headers,
        )

        # List without include_archived
        response = await client.get(
            "/api/registry/namespaces",
            headers=auth_headers,
        )
        assert response.status_code == 200
        prefixes = [ns["prefix"] for ns in response.json()]
        assert "to-archive-list" not in prefixes

    @pytest.mark.asyncio
    async def test_list_namespaces_include_archived(self, client: AsyncClient, auth_headers: dict):
        """Test that archived namespaces can be included with query parameter."""
        # Create and archive a namespace
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "to-archive-include"},
            headers=auth_headers,
        )
        await client.post(
            "/api/registry/namespaces/to-archive-include/archive",
            headers=auth_headers,
        )

        # List with include_archived=true
        response = await client.get(
            "/api/registry/namespaces",
            params={"include_archived": True},
            headers=auth_headers,
        )
        assert response.status_code == 200
        prefixes = [ns["prefix"] for ns in response.json()]
        assert "to-archive-include" in prefixes

    @pytest.mark.asyncio
    async def test_list_namespaces_excludes_deleted(self, client: AsyncClient, auth_headers: dict):
        """Test that journal-deleted namespaces are excluded from listing."""
        # Create with deletion_mode=full, then delete via journal
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "to-delete-list", "deletion_mode": "full"},
            headers=auth_headers,
        )
        await client.delete(
            "/api/registry/namespaces/to-delete-list",
            headers=auth_headers,
        )

        # Should not appear in any listing
        response = await client.get(
            "/api/registry/namespaces",
            params={"include_archived": True},
            headers=auth_headers,
        )
        assert response.status_code == 200
        prefixes = [ns["prefix"] for ns in response.json()]
        assert "to-delete-list" not in prefixes


class TestGetNamespaceStats:
    """Tests for namespace statistics endpoint."""

    @pytest.mark.asyncio
    async def test_get_stats_empty_namespace(self, client: AsyncClient, auth_headers: dict):
        """Test getting stats for a namespace with no entries."""
        response = await client.get(
            "/api/registry/namespaces/default/stats",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["prefix"] == "default"
        assert data["status"] == "active"
        assert isinstance(data["entity_counts"], dict)
        # All counts should be 0 since no entries exist yet
        for entity_type, count in data["entity_counts"].items():
            assert count == 0

    @pytest.mark.asyncio
    async def test_get_stats_with_entries(self, client: AsyncClient, auth_headers: dict):
        """Test that stats reflect registered entries."""
        # Register some entries in default namespace
        await client.post(
            "/api/registry/entries/register",
            json=[
                {"namespace": "default", "entity_type": "terms", "composite_key": {"k": "v1"}},
                {"namespace": "default", "entity_type": "terms", "composite_key": {"k": "v2"}},
                {"namespace": "default", "entity_type": "documents", "composite_key": {"k": "v3"}},
            ],
            headers=auth_headers,
        )

        response = await client.get(
            "/api/registry/namespaces/default/stats",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["entity_counts"]["terms"] == 2
        assert data["entity_counts"]["documents"] == 1

    @pytest.mark.asyncio
    async def test_get_stats_not_found(self, client: AsyncClient, auth_headers: dict):
        """Test that stats for a non-existent namespace returns 404."""
        response = await client.get(
            "/api/registry/namespaces/nonexistent/stats",
            headers=auth_headers,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_stats_only_counts_active_entries(self, client: AsyncClient, auth_headers: dict):
        """Test that stats only count active entries, not inactive ones."""
        # Register an entry
        reg_resp = await client.post(
            "/api/registry/entries/register",
            json=[{"namespace": "default", "entity_type": "terms", "composite_key": {"stat_key": "stat_val"}}],
            headers=auth_headers,
        )
        entry_id = reg_resp.json()["results"][0]["registry_id"]

        # Soft-delete the entry
        await client.request(
            "DELETE",
            "/api/registry/entries",
            json=[{"entry_id": entry_id}],
            headers=auth_headers,
        )

        response = await client.get(
            "/api/registry/namespaces/default/stats",
            headers=auth_headers,
        )
        assert response.status_code == 200
        # The inactive entry should not be counted
        assert response.json()["entity_counts"]["terms"] == 0


class TestGetNamespaceIdConfig:
    """Tests for namespace ID configuration endpoint."""

    @pytest.mark.asyncio
    async def test_get_id_config(self, client: AsyncClient, auth_headers: dict):
        """Test getting the ID configuration for a namespace."""
        response = await client.get(
            "/api/registry/namespaces/default/id-config",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        expected_entity_types = {"terminologies", "terms", "templates", "documents", "files"}
        assert set(data.keys()) == expected_entity_types
        # Default namespace should have uuid7 for everything
        for entity_type in expected_entity_types:
            assert data[entity_type]["algorithm"] == "uuid7"

    @pytest.mark.asyncio
    async def test_get_id_config_custom_namespace(self, client: AsyncClient, auth_headers: dict):
        """Test getting ID config for a namespace with custom settings."""
        # Create namespace with custom ID config
        await client.post(
            "/api/registry/namespaces",
            json={
                "prefix": "custom-id-cfg",
                "id_config": {
                    "terms": {"algorithm": "prefixed", "prefix": "T-", "pad": 6},
                },
            },
            headers=auth_headers,
        )

        response = await client.get(
            "/api/registry/namespaces/custom-id-cfg/id-config",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["terms"]["algorithm"] == "prefixed"
        assert data["terms"]["prefix"] == "T-"
        assert data["terms"]["pad"] == 6
        # Non-configured entity types should still have defaults
        assert data["documents"]["algorithm"] == "uuid7"

    @pytest.mark.asyncio
    async def test_get_id_config_not_found(self, client: AsyncClient, auth_headers: dict):
        """Test that ID config for a non-existent namespace returns 404."""
        response = await client.get(
            "/api/registry/namespaces/nonexistent/id-config",
            headers=auth_headers,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_id_config_archived_namespace_returns_404(self, client: AsyncClient, auth_headers: dict):
        """Test that ID config endpoint only works for active namespaces."""
        # Create and archive
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "archive-for-config"},
            headers=auth_headers,
        )
        await client.post(
            "/api/registry/namespaces/archive-for-config/archive",
            headers=auth_headers,
        )

        response = await client.get(
            "/api/registry/namespaces/archive-for-config/id-config",
            headers=auth_headers,
        )
        assert response.status_code == 404


class TestUpdateNamespace:
    """Tests for updating namespaces."""

    @pytest.mark.asyncio
    async def test_update_description(self, client: AsyncClient, auth_headers: dict):
        """Test updating a namespace description."""
        response = await client.put(
            "/api/registry/namespaces/default",
            json={
                "description": "Updated description for default namespace",
                "updated_by": "admin-user",
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["description"] == "Updated description for default namespace"
        assert data["updated_by"] == "admin-user"

    @pytest.mark.asyncio
    async def test_update_isolation_mode(self, client: AsyncClient, auth_headers: dict):
        """Test updating the isolation mode."""
        response = await client.put(
            "/api/registry/namespaces/default",
            json={"isolation_mode": "strict"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["isolation_mode"] == "strict"

    @pytest.mark.asyncio
    async def test_update_allowed_external_refs(self, client: AsyncClient, auth_headers: dict):
        """Test updating the allowed external refs list."""
        response = await client.put(
            "/api/registry/namespaces/default",
            json={"allowed_external_refs": ["vendor1", "vendor2", "partner-ns"]},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["allowed_external_refs"] == ["vendor1", "vendor2", "partner-ns"]

    @pytest.mark.asyncio
    async def test_update_not_found(self, client: AsyncClient, auth_headers: dict):
        """Test that updating a non-existent namespace returns 404."""
        response = await client.put(
            "/api/registry/namespaces/nonexistent",
            json={"description": "Does not matter"},
            headers=auth_headers,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_update_preserves_other_fields(self, client: AsyncClient, auth_headers: dict):
        """Test that updating one field does not alter other fields."""
        # Create a namespace with specific settings
        await client.post(
            "/api/registry/namespaces",
            json={
                "prefix": "preserve-fields",
                "description": "Original description",
                "isolation_mode": "strict",
            },
            headers=auth_headers,
        )

        # Update only the description
        response = await client.put(
            "/api/registry/namespaces/preserve-fields",
            json={"description": "New description"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["description"] == "New description"
        assert data["isolation_mode"] == "strict"


class TestArchiveNamespace:
    """Tests for archiving namespaces."""

    @pytest.mark.asyncio
    async def test_archive_namespace(self, client: AsyncClient, auth_headers: dict):
        """Test archiving a namespace."""
        # Create a namespace to archive
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "to-archive"},
            headers=auth_headers,
        )

        response = await client.post(
            "/api/registry/namespaces/to-archive/archive",
            params={"archived_by": "admin-user"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "archived"
        assert data["updated_by"] == "admin-user"

    @pytest.mark.asyncio
    async def test_archive_wip_namespace_forbidden(self, client: AsyncClient, auth_headers: dict):
        """Test that archiving the 'wip' namespace is forbidden."""
        # First initialize the wip namespace
        await client.post(
            "/api/registry/namespaces/initialize-wip",
            headers=auth_headers,
        )

        response = await client.post(
            "/api/registry/namespaces/wip/archive",
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert "Cannot archive" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_archive_not_found(self, client: AsyncClient, auth_headers: dict):
        """Test that archiving a non-existent namespace returns 404."""
        response = await client.post(
            "/api/registry/namespaces/nonexistent/archive",
            headers=auth_headers,
        )
        assert response.status_code == 404


class TestRestoreNamespace:
    """Tests for restoring archived namespaces."""

    @pytest.mark.asyncio
    async def test_restore_archived_namespace(self, client: AsyncClient, auth_headers: dict):
        """Test restoring an archived namespace."""
        # Create and archive
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "to-restore"},
            headers=auth_headers,
        )
        await client.post(
            "/api/registry/namespaces/to-restore/archive",
            headers=auth_headers,
        )

        # Restore
        response = await client.post(
            "/api/registry/namespaces/to-restore/restore",
            params={"restored_by": "admin-user"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "active"
        assert data["updated_by"] == "admin-user"

    @pytest.mark.asyncio
    async def test_restore_active_namespace_fails(self, client: AsyncClient, auth_headers: dict):
        """Test that restoring an active namespace returns 400."""
        response = await client.post(
            "/api/registry/namespaces/default/restore",
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert "not archived" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_restore_not_found(self, client: AsyncClient, auth_headers: dict):
        """Test that restoring a non-existent namespace returns 404."""
        response = await client.post(
            "/api/registry/namespaces/nonexistent/restore",
            headers=auth_headers,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_restore_then_list_shows_active(self, client: AsyncClient, auth_headers: dict):
        """Test that a restored namespace appears in the active namespace list."""
        # Create, archive, and restore
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "restore-list-test"},
            headers=auth_headers,
        )
        await client.post(
            "/api/registry/namespaces/restore-list-test/archive",
            headers=auth_headers,
        )
        await client.post(
            "/api/registry/namespaces/restore-list-test/restore",
            headers=auth_headers,
        )

        # Verify it appears in the active list
        list_resp = await client.get(
            "/api/registry/namespaces",
            headers=auth_headers,
        )
        prefixes = [ns["prefix"] for ns in list_resp.json()]
        assert "restore-list-test" in prefixes


class TestDeleteNamespace:
    """Tests for journal-based namespace deletion."""

    @pytest.mark.asyncio
    async def test_dry_run(self, client: AsyncClient, auth_headers: dict):
        """Test dry run returns impact report without making changes."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "dry-run-ns", "deletion_mode": "full"},
            headers=auth_headers,
        )

        response = await client.delete(
            "/api/registry/namespaces/dry-run-ns",
            params={"dry_run": True},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["dry_run"] is True
        assert "entity_counts" in data

        # Namespace should still exist
        get_resp = await client.get(
            "/api/registry/namespaces/dry-run-ns",
            headers=auth_headers,
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "active"

    @pytest.mark.asyncio
    async def test_delete_full_mode_namespace(self, client: AsyncClient, auth_headers: dict):
        """Test deleting a namespace with deletion_mode=full."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "to-delete", "deletion_mode": "full"},
            headers=auth_headers,
        )

        response = await client.delete(
            "/api/registry/namespaces/to-delete",
            params={"deleted_by": "admin-user"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"

    @pytest.mark.asyncio
    async def test_delete_retain_mode_fails(self, client: AsyncClient, auth_headers: dict):
        """Test that deleting a retain-mode namespace returns 400."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "retain-del"},
            headers=auth_headers,
        )

        response = await client.delete(
            "/api/registry/namespaces/retain-del",
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert "retain" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_delete_wip_namespace_forbidden(self, client: AsyncClient, auth_headers: dict):
        """Test that deleting the 'wip' namespace is forbidden."""
        await client.post(
            "/api/registry/namespaces/initialize-wip",
            headers=auth_headers,
        )

        response = await client.delete(
            "/api/registry/namespaces/wip",
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert "Cannot delete" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_delete_not_found(self, client: AsyncClient, auth_headers: dict):
        """Test that deleting a non-existent namespace returns 404."""
        response = await client.delete(
            "/api/registry/namespaces/nonexistent",
            headers=auth_headers,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_deletion_status(self, client: AsyncClient, auth_headers: dict):
        """Test getting deletion status after a completed deletion."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "status-check", "deletion_mode": "full"},
            headers=auth_headers,
        )
        await client.delete(
            "/api/registry/namespaces/status-check",
            headers=auth_headers,
        )

        response = await client.get(
            "/api/registry/namespaces/status-check/deletion-status",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["namespace"] == "status-check"

    @pytest.mark.asyncio
    async def test_update_deletion_mode(self, client: AsyncClient, auth_headers: dict):
        """Test changing deletion_mode from retain to full."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "mode-change"},
            headers=auth_headers,
        )

        # Without confirm flag should fail
        resp = await client.patch(
            "/api/registry/namespaces/mode-change",
            params={"deletion_mode": "full"},
            headers=auth_headers,
        )
        assert resp.status_code == 400

        # With confirm flag
        resp = await client.patch(
            "/api/registry/namespaces/mode-change",
            params={"deletion_mode": "full", "confirm_enable_deletion": True},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["deletion_mode"] == "full"


class TestInitializeWipNamespace:
    """Tests for the initialize-wip endpoint."""

    @pytest.mark.asyncio
    async def test_initialize_wip_namespace(self, client: AsyncClient, auth_headers: dict):
        """Test initializing the default 'wip' namespace."""
        response = await client.post(
            "/api/registry/namespaces/initialize-wip",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["prefix"] == "wip"
        assert data["description"] == "Default World In a Pie namespace"
        assert data["isolation_mode"] == "open"
        assert data["status"] == "active"

    @pytest.mark.asyncio
    async def test_initialize_wip_namespace_idempotent(self, client: AsyncClient, auth_headers: dict):
        """Test that calling initialize-wip multiple times is idempotent."""
        # First call
        resp1 = await client.post(
            "/api/registry/namespaces/initialize-wip",
            headers=auth_headers,
        )
        assert resp1.status_code == 200
        data1 = resp1.json()

        # Second call - should return the same namespace, not fail
        resp2 = await client.post(
            "/api/registry/namespaces/initialize-wip",
            headers=auth_headers,
        )
        assert resp2.status_code == 200
        data2 = resp2.json()

        # Both responses should represent the same namespace
        assert data1["prefix"] == data2["prefix"]
        assert data1["description"] == data2["description"]

    @pytest.mark.asyncio
    async def test_initialize_wip_then_get(self, client: AsyncClient, auth_headers: dict):
        """Test that the wip namespace is retrievable after initialization."""
        await client.post(
            "/api/registry/namespaces/initialize-wip",
            headers=auth_headers,
        )

        response = await client.get(
            "/api/registry/namespaces/wip",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["prefix"] == "wip"


class TestNamespaceAuthRequired:
    """Tests verifying that namespace endpoints require authentication."""

    @pytest.mark.asyncio
    async def test_list_namespaces_no_auth(self, client: AsyncClient):
        """Test that listing namespaces without auth headers fails."""
        response = await client.get("/api/registry/namespaces")
        assert response.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_get_namespace_no_auth(self, client: AsyncClient):
        """Test that getting a namespace without auth headers fails."""
        response = await client.get("/api/registry/namespaces/default")
        assert response.status_code in (401, 403)
