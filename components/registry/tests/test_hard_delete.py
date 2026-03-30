"""Tests for hard-delete support in Registry.

Covers:
- Hard-delete permanently removes entry from MongoDB
- Hard-delete requires namespace deletion_mode='full'
- Hard-delete rejected in 'retain' namespace
- Soft-delete unchanged when hard_delete=False (regression)
"""

import pytest

from registry.models.namespace import Namespace

# =========================================================================
# Helpers
# =========================================================================


async def _register_entry(client, auth_headers, namespace="default", entity_type="terms", value="TEST"):
    """Register a single entry and return the entry_id."""
    response = await client.post(
        "/api/registry/entries/register",
        headers=auth_headers,
        json=[{
            "namespace": namespace,
            "entity_type": entity_type,
            "composite_key": {"namespace": namespace, "type": entity_type, "value": value},
        }],
    )
    assert response.status_code == 200
    data = response.json()
    assert data["results"][0]["status"] == "created"
    return data["results"][0]["registry_id"]


async def _set_namespace_deletion_mode(namespace_prefix: str, mode: str):
    """Update namespace deletion_mode directly in MongoDB."""
    ns = await Namespace.find_one({"prefix": namespace_prefix})
    assert ns is not None, f"Namespace '{namespace_prefix}' not found"
    ns.deletion_mode = mode
    await ns.save()


# =========================================================================
# Hard-Delete Entry
# =========================================================================


class TestHardDeleteEntry:
    """Tests for hard-delete via the bulk delete endpoint."""

    @pytest.mark.asyncio
    async def test_hard_delete_permanently_removes_entry(self, client, auth_headers):
        """hard_delete=True removes entry from MongoDB entirely."""
        await _set_namespace_deletion_mode("default", "full")

        entry_id = await _register_entry(client, auth_headers)

        # Hard-delete
        response = await client.request(
            "DELETE",
            "/api/registry/entries",
            headers=auth_headers,
            json=[{"entry_id": entry_id, "hard_delete": True}],
        )
        assert response.status_code == 200
        data = response.json()
        assert data["succeeded"] == 1
        assert data["results"][0]["status"] == "deleted"

        # Verify entry is gone (not just inactive)
        get_resp = await client.get(
            f"/api/registry/entries/{entry_id}",
            headers=auth_headers,
        )
        assert get_resp.status_code == 404

    @pytest.mark.asyncio
    async def test_hard_delete_rejected_in_retain_namespace(self, client, auth_headers):
        """hard_delete=True fails when namespace deletion_mode='retain'."""
        # Default deletion_mode is 'retain'
        entry_id = await _register_entry(client, auth_headers)

        response = await client.request(
            "DELETE",
            "/api/registry/entries",
            headers=auth_headers,
            json=[{"entry_id": entry_id, "hard_delete": True}],
        )
        assert response.status_code == 200
        data = response.json()
        assert data["failed"] == 1
        assert data["results"][0]["status"] == "error"
        assert "deletion_mode" in data["results"][0]["error"]

        # Verify entry still exists
        get_resp = await client.get(
            f"/api/registry/entries/{entry_id}",
            headers=auth_headers,
        )
        assert get_resp.status_code == 200

    @pytest.mark.asyncio
    async def test_hard_delete_nonexistent_entry(self, client, auth_headers):
        """hard_delete=True on nonexistent entry returns not_found."""
        response = await client.request(
            "DELETE",
            "/api/registry/entries",
            headers=auth_headers,
            json=[{"entry_id": "NONEXISTENT-999", "hard_delete": True}],
        )
        assert response.status_code == 200
        data = response.json()
        assert data["failed"] == 1
        assert data["results"][0]["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_hard_delete_bulk_mixed(self, client, auth_headers):
        """Bulk delete with mix of hard-delete and soft-delete items."""
        await _set_namespace_deletion_mode("default", "full")

        entry1 = await _register_entry(client, auth_headers, value="BULK_HD_1")
        entry2 = await _register_entry(client, auth_headers, value="BULK_HD_2")

        response = await client.request(
            "DELETE",
            "/api/registry/entries",
            headers=auth_headers,
            json=[
                {"entry_id": entry1, "hard_delete": True},
                {"entry_id": entry2, "hard_delete": False},
            ],
        )
        assert response.status_code == 200
        data = response.json()
        assert data["succeeded"] == 2

        # entry1 should be gone (hard-deleted)
        get1 = await client.get(f"/api/registry/entries/{entry1}", headers=auth_headers)
        assert get1.status_code == 404

        # entry2 should still exist but be inactive (soft-deleted)
        get2 = await client.get(f"/api/registry/entries/{entry2}", headers=auth_headers)
        assert get2.status_code == 200
        assert get2.json()["status"] == "inactive"


# =========================================================================
# Soft-Delete Regression
# =========================================================================


class TestSoftDeleteRegression:
    """Verify soft-delete behavior is unchanged when hard_delete=False."""

    @pytest.mark.asyncio
    async def test_soft_delete_sets_inactive_status(self, client, auth_headers):
        """Default delete (hard_delete=False) sets status to inactive."""
        entry_id = await _register_entry(client, auth_headers, value="SOFT_REG")

        response = await client.request(
            "DELETE",
            "/api/registry/entries",
            headers=auth_headers,
            json=[{"entry_id": entry_id}],
        )
        assert response.status_code == 200
        data = response.json()
        assert data["succeeded"] == 1
        assert data["results"][0]["status"] == "deactivated"

        # Entry still exists, just inactive
        get_resp = await client.get(
            f"/api/registry/entries/{entry_id}",
            headers=auth_headers,
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "inactive"

    @pytest.mark.asyncio
    async def test_soft_delete_works_in_retain_namespace(self, client, auth_headers):
        """Soft-delete works even in retain namespace (no deletion_mode check)."""
        entry_id = await _register_entry(client, auth_headers, value="RETAIN_SOFT")

        response = await client.request(
            "DELETE",
            "/api/registry/entries",
            headers=auth_headers,
            json=[{"entry_id": entry_id, "hard_delete": False}],
        )
        assert response.status_code == 200
        data = response.json()
        assert data["succeeded"] == 1
        assert data["results"][0]["status"] == "deactivated"
