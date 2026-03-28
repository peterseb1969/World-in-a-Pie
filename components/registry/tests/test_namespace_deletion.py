"""Extensive tests for namespace deletion feature.

Covers: deletion_mode lifecycle, journal-based deletion, dry-run,
inbound reference detection, force flag, locked namespace behavior,
crash recovery, deletion status, and MCP-facing APIs.
"""

import pytest
from httpx import AsyncClient

# =============================================================================
# deletion_mode lifecycle
# =============================================================================


class TestDeletionMode:
    """Tests for the deletion_mode field on namespaces."""

    @pytest.mark.asyncio
    async def test_default_deletion_mode_is_retain(self, client: AsyncClient, auth_headers: dict):
        """Namespaces default to deletion_mode='retain'."""
        resp = await client.post(
            "/api/registry/namespaces",
            json={"prefix": "dm-default"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["deletion_mode"] == "retain"

    @pytest.mark.asyncio
    async def test_create_with_full_deletion_mode(self, client: AsyncClient, auth_headers: dict):
        """Can create a namespace with deletion_mode='full'."""
        resp = await client.post(
            "/api/registry/namespaces",
            json={"prefix": "dm-full", "deletion_mode": "full"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["deletion_mode"] == "full"

    @pytest.mark.asyncio
    async def test_deletion_mode_in_get_response(self, client: AsyncClient, auth_headers: dict):
        """deletion_mode is present in GET namespace response."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "dm-get", "deletion_mode": "full"},
            headers=auth_headers,
        )
        resp = await client.get(
            "/api/registry/namespaces/dm-get",
            headers=auth_headers,
        )
        assert resp.json()["deletion_mode"] == "full"

    @pytest.mark.asyncio
    async def test_deletion_mode_in_list_response(self, client: AsyncClient, auth_headers: dict):
        """deletion_mode is present in list namespaces response."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "dm-list", "deletion_mode": "full"},
            headers=auth_headers,
        )
        resp = await client.get(
            "/api/registry/namespaces",
            headers=auth_headers,
        )
        ns = next(n for n in resp.json() if n["prefix"] == "dm-list")
        assert ns["deletion_mode"] == "full"

    @pytest.mark.asyncio
    async def test_deletion_mode_in_stats_response(self, client: AsyncClient, auth_headers: dict):
        """deletion_mode is present in stats response."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "dm-stats", "deletion_mode": "full"},
            headers=auth_headers,
        )
        resp = await client.get(
            "/api/registry/namespaces/dm-stats/stats",
            headers=auth_headers,
        )
        assert resp.json()["deletion_mode"] == "full"

    @pytest.mark.asyncio
    async def test_update_retain_to_full_requires_confirm(self, client: AsyncClient, auth_headers: dict):
        """Changing retain -> full without confirm_enable_deletion fails."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "dm-upgrade"},
            headers=auth_headers,
        )
        resp = await client.patch(
            "/api/registry/namespaces/dm-upgrade",
            params={"deletion_mode": "full"},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "confirm" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_update_retain_to_full_with_confirm(self, client: AsyncClient, auth_headers: dict):
        """Changing retain -> full with confirm_enable_deletion=true succeeds."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "dm-upgrade2"},
            headers=auth_headers,
        )
        resp = await client.patch(
            "/api/registry/namespaces/dm-upgrade2",
            params={"deletion_mode": "full", "confirm_enable_deletion": True},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["deletion_mode"] == "full"

    @pytest.mark.asyncio
    async def test_update_full_to_retain(self, client: AsyncClient, auth_headers: dict):
        """Changing full -> retain does not require confirmation."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "dm-downgrade", "deletion_mode": "full"},
            headers=auth_headers,
        )
        resp = await client.patch(
            "/api/registry/namespaces/dm-downgrade",
            params={"deletion_mode": "retain"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["deletion_mode"] == "retain"

    @pytest.mark.asyncio
    async def test_update_wip_to_full_forbidden(self, client: AsyncClient, auth_headers: dict):
        """Cannot enable deletion on the 'wip' namespace."""
        await client.post(
            "/api/registry/namespaces/initialize-wip",
            headers=auth_headers,
        )
        resp = await client.patch(
            "/api/registry/namespaces/wip",
            params={"deletion_mode": "full", "confirm_enable_deletion": True},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "wip" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_update_invalid_mode(self, client: AsyncClient, auth_headers: dict):
        """Invalid deletion_mode values are rejected."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "dm-invalid"},
            headers=auth_headers,
        )
        resp = await client.patch(
            "/api/registry/namespaces/dm-invalid",
            params={"deletion_mode": "nuke"},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_update_nonexistent_namespace(self, client: AsyncClient, auth_headers: dict):
        """PATCH on nonexistent namespace returns 404."""
        resp = await client.patch(
            "/api/registry/namespaces/no-such-ns",
            params={"deletion_mode": "full", "confirm_enable_deletion": True},
            headers=auth_headers,
        )
        assert resp.status_code == 404


# =============================================================================
# Dry run
# =============================================================================


class TestDryRun:
    """Tests for dry-run deletion impact reports."""

    @pytest.mark.asyncio
    async def test_dry_run_empty_namespace(self, client: AsyncClient, auth_headers: dict):
        """Dry run on empty namespace returns zero counts."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "dr-empty", "deletion_mode": "full"},
            headers=auth_headers,
        )
        resp = await client.delete(
            "/api/registry/namespaces/dr-empty",
            params={"dry_run": True},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["dry_run"] is True
        assert data["namespace"] == "dr-empty"
        assert "entity_counts" in data
        assert data["safe_to_delete"] is True

    @pytest.mark.asyncio
    async def test_dry_run_with_registry_entries(self, client: AsyncClient, auth_headers: dict):
        """Dry run counts registry entries in the namespace."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "dr-entries", "deletion_mode": "full"},
            headers=auth_headers,
        )
        # Register some entries in this namespace
        await client.post(
            "/api/registry/entries/register",
            json=[
                {"namespace": "dr-entries", "entity_type": "templates",
                 "composite_key": {"value": "template_a"}, "created_by": "test"},
                {"namespace": "dr-entries", "entity_type": "templates",
                 "composite_key": {"value": "template_b"}, "created_by": "test"},
            ],
            headers=auth_headers,
        )
        resp = await client.delete(
            "/api/registry/namespaces/dr-entries",
            params={"dry_run": True},
            headers=auth_headers,
        )
        data = resp.json()
        assert data["entity_counts"]["registry_entries"] == 2

    @pytest.mark.asyncio
    async def test_dry_run_does_not_modify_data(self, client: AsyncClient, auth_headers: dict):
        """Dry run leaves namespace and entries intact."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "dr-safe", "deletion_mode": "full"},
            headers=auth_headers,
        )
        await client.post(
            "/api/registry/entries/register",
            json=[{"namespace": "dr-safe", "entity_type": "documents",
                   "composite_key": {"value": "doc1"}, "created_by": "test"}],
            headers=auth_headers,
        )

        # Dry run
        await client.delete(
            "/api/registry/namespaces/dr-safe",
            params={"dry_run": True},
            headers=auth_headers,
        )

        # Verify namespace still exists and is active
        resp = await client.get(
            "/api/registry/namespaces/dr-safe",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

        # Verify entries still exist
        resp = await client.get(
            "/api/registry/entries",
            params={"namespace": "dr-safe"},
            headers=auth_headers,
        )
        assert resp.json()["total"] == 1

    @pytest.mark.asyncio
    async def test_dry_run_on_retain_namespace(self, client: AsyncClient, auth_headers: dict):
        """Dry run works on retain-mode namespaces (informational only)."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "dr-retain"},
            headers=auth_headers,
        )
        resp = await client.delete(
            "/api/registry/namespaces/dr-retain",
            params={"dry_run": True},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["deletion_mode"] == "retain"

    @pytest.mark.asyncio
    async def test_dry_run_shows_no_inbound_refs(self, client: AsyncClient, auth_headers: dict):
        """Dry run shows empty inbound_references when none exist."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "dr-norefs", "deletion_mode": "full"},
            headers=auth_headers,
        )
        resp = await client.delete(
            "/api/registry/namespaces/dr-norefs",
            params={"dry_run": True},
            headers=auth_headers,
        )
        data = resp.json()
        assert data["inbound_references"] == []
        assert data["requires_force"] is False


# =============================================================================
# Actual deletion
# =============================================================================


class TestNamespaceDeletion:
    """Tests for actual namespace deletion (journal-based)."""

    @pytest.mark.asyncio
    async def test_delete_empty_namespace(self, client: AsyncClient, auth_headers: dict):
        """Deleting an empty full-mode namespace succeeds."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "del-empty", "deletion_mode": "full"},
            headers=auth_headers,
        )
        resp = await client.delete(
            "/api/registry/namespaces/del-empty",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"

    @pytest.mark.asyncio
    async def test_delete_with_entries(self, client: AsyncClient, auth_headers: dict):
        """Deleting a namespace with registry entries cleans them up."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "del-entries", "deletion_mode": "full"},
            headers=auth_headers,
        )
        await client.post(
            "/api/registry/entries/register",
            json=[
                {"namespace": "del-entries", "entity_type": "templates",
                 "composite_key": {"value": f"t{i}"}, "created_by": "test"}
                for i in range(5)
            ],
            headers=auth_headers,
        )

        resp = await client.delete(
            "/api/registry/namespaces/del-entries",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["summary"]["registry_entries"] == 5

    @pytest.mark.asyncio
    async def test_delete_cleans_up_grants(self, client: AsyncClient, auth_headers: dict):
        """Deleting a namespace removes its grants."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "del-grants", "deletion_mode": "full"},
            headers=auth_headers,
        )
        # Create a grant
        await client.post(
            "/api/registry/namespaces/del-grants/grants",
            json=[{"subject": "user@test.com", "subject_type": "user", "permission": "write"}],
            headers=auth_headers,
        )

        resp = await client.delete(
            "/api/registry/namespaces/del-grants",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["summary"]["namespace_grants"] >= 1

    @pytest.mark.asyncio
    async def test_delete_retain_mode_rejected(self, client: AsyncClient, auth_headers: dict):
        """Cannot delete a retain-mode namespace."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "del-retain"},
            headers=auth_headers,
        )
        resp = await client.delete(
            "/api/registry/namespaces/del-retain",
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "retain" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_delete_wip_namespace_rejected(self, client: AsyncClient, auth_headers: dict):
        """Cannot delete the default 'wip' namespace."""
        await client.post(
            "/api/registry/namespaces/initialize-wip",
            headers=auth_headers,
        )
        resp = await client.delete(
            "/api/registry/namespaces/wip",
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "Cannot delete" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_delete_nonexistent_namespace(self, client: AsyncClient, auth_headers: dict):
        """Cannot delete a namespace that doesn't exist."""
        resp = await client.delete(
            "/api/registry/namespaces/no-such-ns",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_deleted_namespace_not_in_list(self, client: AsyncClient, auth_headers: dict):
        """After deletion, namespace does not appear in list."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "del-gone", "deletion_mode": "full"},
            headers=auth_headers,
        )
        await client.delete(
            "/api/registry/namespaces/del-gone",
            headers=auth_headers,
        )
        resp = await client.get(
            "/api/registry/namespaces",
            params={"include_archived": True},
            headers=auth_headers,
        )
        prefixes = [ns["prefix"] for ns in resp.json()]
        assert "del-gone" not in prefixes

    @pytest.mark.asyncio
    async def test_deleted_namespace_entries_gone(self, client: AsyncClient, auth_headers: dict):
        """After deletion, registry entries for that namespace are removed."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "del-check-entries", "deletion_mode": "full"},
            headers=auth_headers,
        )
        await client.post(
            "/api/registry/entries/register",
            json=[{"namespace": "del-check-entries", "entity_type": "documents",
                   "composite_key": {"value": "doc1"}, "created_by": "test"}],
            headers=auth_headers,
        )

        await client.delete(
            "/api/registry/namespaces/del-check-entries",
            headers=auth_headers,
        )

        resp = await client.get(
            "/api/registry/entries",
            params={"namespace": "del-check-entries"},
            headers=auth_headers,
        )
        assert resp.json()["total"] == 0

    @pytest.mark.asyncio
    async def test_double_delete_fails(self, client: AsyncClient, auth_headers: dict):
        """Deleting an already-deleted namespace returns 404."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "del-double", "deletion_mode": "full"},
            headers=auth_headers,
        )
        resp1 = await client.delete(
            "/api/registry/namespaces/del-double",
            headers=auth_headers,
        )
        assert resp1.status_code == 200

        resp2 = await client.delete(
            "/api/registry/namespaces/del-double",
            headers=auth_headers,
        )
        assert resp2.status_code == 404

    @pytest.mark.asyncio
    async def test_other_namespace_entries_untouched(self, client: AsyncClient, auth_headers: dict):
        """Deleting one namespace does not affect entries in other namespaces."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "del-target", "deletion_mode": "full"},
            headers=auth_headers,
        )
        # Register entries in both the target and default namespaces
        await client.post(
            "/api/registry/entries/register",
            json=[
                {"namespace": "del-target", "entity_type": "documents",
                 "composite_key": {"value": "target-doc"}, "created_by": "test"},
                {"namespace": "default", "entity_type": "documents",
                 "composite_key": {"value": "default-doc"}, "created_by": "test"},
            ],
            headers=auth_headers,
        )

        await client.delete(
            "/api/registry/namespaces/del-target",
            headers=auth_headers,
        )

        # Default namespace entries should still be there
        resp = await client.get(
            "/api/registry/entries",
            params={"namespace": "default"},
            headers=auth_headers,
        )
        assert resp.json()["total"] >= 1

    @pytest.mark.asyncio
    async def test_delete_with_id_counters(self, client: AsyncClient, auth_headers: dict):
        """Deleting a namespace cleans up its ID counters."""
        await client.post(
            "/api/registry/namespaces",
            json={
                "prefix": "del-counters",
                "deletion_mode": "full",
                "id_config": {
                    "templates": {"algorithm": "prefixed", "prefix": "TPL-", "pad": 4}
                },
            },
            headers=auth_headers,
        )
        # Provision IDs to create counters
        await client.post(
            "/api/registry/entries/provision",
            json={"namespace": "del-counters", "entity_type": "templates",
                  "count": 3, "created_by": "test"},
            headers=auth_headers,
        )

        resp = await client.delete(
            "/api/registry/namespaces/del-counters",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"
        assert resp.json()["summary"].get("id_counters", 0) >= 1

    @pytest.mark.asyncio
    async def test_upgrade_then_delete(self, client: AsyncClient, auth_headers: dict):
        """Can upgrade retain -> full and then delete."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "del-upgrade"},
            headers=auth_headers,
        )
        # Upgrade
        await client.patch(
            "/api/registry/namespaces/del-upgrade",
            params={"deletion_mode": "full", "confirm_enable_deletion": True},
            headers=auth_headers,
        )
        # Delete
        resp = await client.delete(
            "/api/registry/namespaces/del-upgrade",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"


# =============================================================================
# Deletion status and resume
# =============================================================================


class TestDeletionStatus:
    """Tests for deletion status and resume endpoints."""

    @pytest.mark.asyncio
    async def test_status_after_completed_deletion(self, client: AsyncClient, auth_headers: dict):
        """Deletion status shows completed journal."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "stat-complete", "deletion_mode": "full"},
            headers=auth_headers,
        )
        await client.delete(
            "/api/registry/namespaces/stat-complete",
            headers=auth_headers,
        )

        resp = await client.get(
            "/api/registry/namespaces/stat-complete/deletion-status",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["namespace"] == "stat-complete"
        assert data["completed_at"] is not None
        assert isinstance(data["steps"], list)
        assert all(s["status"] == "completed" for s in data["steps"])

    @pytest.mark.asyncio
    async def test_status_with_summary(self, client: AsyncClient, auth_headers: dict):
        """Completed deletion has a summary dict."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "stat-summary", "deletion_mode": "full"},
            headers=auth_headers,
        )
        await client.post(
            "/api/registry/entries/register",
            json=[{"namespace": "stat-summary", "entity_type": "templates",
                   "composite_key": {"value": "t1"}, "created_by": "test"}],
            headers=auth_headers,
        )
        await client.delete(
            "/api/registry/namespaces/stat-summary",
            headers=auth_headers,
        )

        resp = await client.get(
            "/api/registry/namespaces/stat-summary/deletion-status",
            headers=auth_headers,
        )
        data = resp.json()
        assert data["summary"] is not None
        assert data["summary"]["registry_entries"] == 1

    @pytest.mark.asyncio
    async def test_status_nonexistent(self, client: AsyncClient, auth_headers: dict):
        """Status for a never-deleted namespace returns 404."""
        resp = await client.get(
            "/api/registry/namespaces/never-deleted/deletion-status",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_resume_when_no_in_progress(self, client: AsyncClient, auth_headers: dict):
        """Resume on a namespace with no in-progress deletion returns 404."""
        resp = await client.post(
            "/api/registry/namespaces/never-deleted/resume-delete",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_resume_after_completed_returns_404(self, client: AsyncClient, auth_headers: dict):
        """Resume after successful deletion returns 404 (no in-progress journal)."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "resume-done", "deletion_mode": "full"},
            headers=auth_headers,
        )
        await client.delete(
            "/api/registry/namespaces/resume-done",
            headers=auth_headers,
        )

        resp = await client.post(
            "/api/registry/namespaces/resume-done/resume-delete",
            headers=auth_headers,
        )
        assert resp.status_code == 404


# =============================================================================
# Locked namespace behavior
# =============================================================================


class TestLockedNamespace:
    """Tests that locked namespaces are inaccessible."""

    @pytest.mark.asyncio
    async def test_locked_namespace_not_in_active_list(self, client: AsyncClient, auth_headers: dict):
        """A locked namespace does not appear in the active namespace list.

        Note: deletion happens synchronously in tests, so the namespace is
        fully deleted (not just locked). This test verifies the end state.
        """
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "lock-list", "deletion_mode": "full"},
            headers=auth_headers,
        )
        await client.delete(
            "/api/registry/namespaces/lock-list",
            headers=auth_headers,
        )

        resp = await client.get(
            "/api/registry/namespaces",
            headers=auth_headers,
        )
        prefixes = [ns["prefix"] for ns in resp.json()]
        assert "lock-list" not in prefixes

    @pytest.mark.asyncio
    async def test_cannot_register_entries_in_deleted_namespace(self, client: AsyncClient, auth_headers: dict):
        """Cannot register entries in a namespace that has been deleted."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "lock-reg", "deletion_mode": "full"},
            headers=auth_headers,
        )
        await client.delete(
            "/api/registry/namespaces/lock-reg",
            headers=auth_headers,
        )

        # Try to register an entry — should fail because namespace doesn't exist
        resp = await client.post(
            "/api/registry/entries/register",
            json=[{"namespace": "lock-reg", "entity_type": "documents",
                   "composite_key": {"value": "doc1"}, "created_by": "test"}],
            headers=auth_headers,
        )
        # The entry registration should fail (namespace validation)
        data = resp.json()
        assert data["results"][0]["status"] == "error"


# =============================================================================
# Audit trail
# =============================================================================


class TestAuditTrail:
    """Tests for the deletion audit trail."""

    @pytest.mark.asyncio
    async def test_journal_records_requested_by(self, client: AsyncClient, auth_headers: dict):
        """Journal records the user who requested deletion."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "audit-user", "deletion_mode": "full"},
            headers=auth_headers,
        )
        await client.delete(
            "/api/registry/namespaces/audit-user",
            params={"deleted_by": "admin@test.com"},
            headers=auth_headers,
        )

        resp = await client.get(
            "/api/registry/namespaces/audit-user/deletion-status",
            headers=auth_headers,
        )
        assert resp.json()["requested_by"] == "admin@test.com"

    @pytest.mark.asyncio
    async def test_journal_has_timestamps(self, client: AsyncClient, auth_headers: dict):
        """Journal has both requested_at and completed_at timestamps."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "audit-time", "deletion_mode": "full"},
            headers=auth_headers,
        )
        await client.delete(
            "/api/registry/namespaces/audit-time",
            headers=auth_headers,
        )

        resp = await client.get(
            "/api/registry/namespaces/audit-time/deletion-status",
            headers=auth_headers,
        )
        data = resp.json()
        assert data["requested_at"] is not None
        assert data["completed_at"] is not None

    @pytest.mark.asyncio
    async def test_journal_steps_have_detail(self, client: AsyncClient, auth_headers: dict):
        """Each journal step has a human-readable detail string."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "audit-detail", "deletion_mode": "full"},
            headers=auth_headers,
        )
        await client.post(
            "/api/registry/entries/register",
            json=[{"namespace": "audit-detail", "entity_type": "templates",
                   "composite_key": {"value": "t1"}, "created_by": "test"}],
            headers=auth_headers,
        )
        await client.delete(
            "/api/registry/namespaces/audit-detail",
            headers=auth_headers,
        )

        resp = await client.get(
            "/api/registry/namespaces/audit-detail/deletion-status",
            headers=auth_headers,
        )
        steps = resp.json()["steps"]
        assert len(steps) >= 1
        for step in steps:
            assert step["detail"] is not None
            assert len(step["detail"]) > 0

    @pytest.mark.asyncio
    async def test_journal_steps_have_deleted_count(self, client: AsyncClient, auth_headers: dict):
        """Completed steps record how many items were deleted."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "audit-count", "deletion_mode": "full"},
            headers=auth_headers,
        )
        await client.post(
            "/api/registry/entries/register",
            json=[
                {"namespace": "audit-count", "entity_type": "templates",
                 "composite_key": {"value": f"t{i}"}, "created_by": "test"}
                for i in range(3)
            ],
            headers=auth_headers,
        )
        await client.delete(
            "/api/registry/namespaces/audit-count",
            headers=auth_headers,
        )

        resp = await client.get(
            "/api/registry/namespaces/audit-count/deletion-status",
            headers=auth_headers,
        )
        steps = resp.json()["steps"]
        registry_step = next(
            (s for s in steps if s.get("collection") == "registry_entries"), None
        )
        assert registry_step is not None
        assert registry_step["deleted_count"] == 3


# =============================================================================
# Re-creation after deletion
# =============================================================================


class TestRecreateAfterDeletion:
    """Tests for recreating a namespace after deletion."""

    @pytest.mark.asyncio
    async def test_can_recreate_deleted_namespace(self, client: AsyncClient, auth_headers: dict):
        """A namespace prefix can be reused after deletion."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "recreate-me", "deletion_mode": "full"},
            headers=auth_headers,
        )
        await client.delete(
            "/api/registry/namespaces/recreate-me",
            headers=auth_headers,
        )

        # Recreate
        resp = await client.post(
            "/api/registry/namespaces",
            json={"prefix": "recreate-me", "deletion_mode": "retain"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["prefix"] == "recreate-me"
        assert resp.json()["deletion_mode"] == "retain"
        assert resp.json()["status"] == "active"

    @pytest.mark.asyncio
    async def test_recreated_namespace_is_empty(self, client: AsyncClient, auth_headers: dict):
        """A recreated namespace starts with zero entries."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "recreate-empty", "deletion_mode": "full"},
            headers=auth_headers,
        )
        await client.post(
            "/api/registry/entries/register",
            json=[{"namespace": "recreate-empty", "entity_type": "documents",
                   "composite_key": {"value": "doc1"}, "created_by": "test"}],
            headers=auth_headers,
        )
        await client.delete(
            "/api/registry/namespaces/recreate-empty",
            headers=auth_headers,
        )

        # Recreate
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "recreate-empty"},
            headers=auth_headers,
        )

        resp = await client.get(
            "/api/registry/namespaces/recreate-empty/stats",
            headers=auth_headers,
        )
        counts = resp.json()["entity_counts"]
        assert all(v == 0 for v in counts.values())
