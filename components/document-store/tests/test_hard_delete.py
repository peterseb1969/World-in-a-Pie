"""Tests for hard-delete support in Document-Store.

Covers:
- All-version hard-delete (removes all versions + Registry entry)
- Version-specific hard-delete (removes one version, others remain)
- File orphan cleanup after hard-delete
- Hard-delete rejected in 'retain' namespace
- Soft-delete regression (unchanged when hard_delete=False)

Uses real Registry via transport injection — no mock registry.
Deletion mode is set directly on the Namespace model (the API
protects the 'wip' namespace from mode changes, but tests are
allowed to set up state that the API wouldn't permit).
"""

import pytest
import pytest_asyncio

from registry.models.entry import RegistryEntry
from registry.models.namespace import Namespace


# =========================================================================
# Fixtures
# =========================================================================


@pytest_asyncio.fixture
async def enable_hard_delete():
    """Set 'wip' namespace to full deletion mode for hard-delete tests."""
    ns = await Namespace.find_one({"prefix": "wip"})
    ns.deletion_mode = "full"
    await ns.save()


# =========================================================================
# Helpers
# =========================================================================


async def create_doc(client, auth_headers, data=None):
    """Create a document and return (document_id, version)."""
    payload = {
        "namespace": "wip",
        "template_id": "TPL-000001",
        "data": data or {
            "national_id": "123456789",
            "first_name": "John",
            "last_name": "Doe",
        },
    }
    response = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json=[payload],
    )
    assert response.status_code == 200
    bulk = response.json()
    assert bulk["succeeded"] >= 1, f"Failed to create document: {bulk}"
    result = bulk["results"][0]
    return result["document_id"], result.get("version", 1)


async def update_doc(client, auth_headers, document_id, data):
    """Update a document (creates a new version)."""
    payload = {
        "namespace": "wip",
        "template_id": "TPL-000001",
        "document_id": document_id,
        "data": data,
    }
    response = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json=[payload],
    )
    assert response.status_code == 200
    return response.json()


async def delete_doc(client, auth_headers, document_id, hard_delete=False, version=None):
    """Delete a document via bulk endpoint."""
    item = {"id": document_id, "hard_delete": hard_delete}
    if version is not None:
        item["version"] = version
    response = await client.request(
        "DELETE",
        "/api/document-store/documents",
        headers=auth_headers,
        json=[item],
    )
    assert response.status_code == 200
    return response.json()


async def get_doc(client, auth_headers, document_id, version=None):
    """Get a document."""
    url = f"/api/document-store/documents/{document_id}"
    if version is not None:
        url += f"?version={version}"
    return await client.get(url, headers=auth_headers)


# =========================================================================
# Hard-Delete All Versions
# =========================================================================


class TestHardDeleteAllVersions:
    """Tests for hard-deleting all versions of a document."""

    @pytest.mark.asyncio
    async def test_hard_delete_removes_document(self, client, auth_headers, enable_hard_delete):
        """hard_delete=True removes document completely."""
        doc_id, _ = await create_doc(client, auth_headers)

        data = await delete_doc(client, auth_headers, doc_id, hard_delete=True)
        assert data["succeeded"] == 1

        # Document completely gone
        resp = await get_doc(client, auth_headers, doc_id)
        assert resp.status_code == 404

        # Registry entry cleaned up
        entry = await RegistryEntry.find_one({"entry_id": doc_id})
        assert entry is None

    @pytest.mark.asyncio
    async def test_hard_delete_rejected_in_retain_namespace(self, client, auth_headers):
        """hard_delete=True fails when namespace deletion_mode='retain'."""
        # 'wip' namespace defaults to 'retain' — no fixture override
        doc_id, _ = await create_doc(client, auth_headers)

        data = await delete_doc(client, auth_headers, doc_id, hard_delete=True)

        assert data["failed"] == 1
        assert "deletion_mode" in data["results"][0]["error"]

        # Document still exists
        resp = await get_doc(client, auth_headers, doc_id)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_hard_delete_nonexistent_document(self, client, auth_headers, enable_hard_delete):
        """hard_delete on nonexistent document returns failure."""
        data = await delete_doc(client, auth_headers, "DOC-NONEXISTENT", hard_delete=True)
        assert data["failed"] == 1


# =========================================================================
# Hard-Delete Specific Version
# =========================================================================


class TestHardDeleteSpecificVersion:
    """Tests for version-specific hard-delete."""

    @pytest.mark.asyncio
    async def test_hard_delete_one_version_keeps_others(
        self, client, auth_headers, sample_person_data, enable_hard_delete
    ):
        """Hard-deleting v1 keeps v2 intact."""
        doc_id, _ = await create_doc(client, auth_headers, sample_person_data)

        # Create v2 by updating
        updated_data = {**sample_person_data, "first_name": "Jane"}
        await update_doc(client, auth_headers, doc_id, updated_data)

        data = await delete_doc(client, auth_headers, doc_id, hard_delete=True, version=1)
        assert data["succeeded"] == 1

        # Document still exists (v2)
        resp = await get_doc(client, auth_headers, doc_id)
        assert resp.status_code == 200

        # Registry entry NOT cleaned up (versions remain)
        entry = await RegistryEntry.find_one({"entry_id": doc_id})
        assert entry is not None

    @pytest.mark.asyncio
    async def test_hard_delete_last_version_cleans_registry(
        self, client, auth_headers, enable_hard_delete
    ):
        """Hard-deleting the only version also removes Registry entry."""
        doc_id, _ = await create_doc(client, auth_headers)

        data = await delete_doc(client, auth_headers, doc_id, hard_delete=True, version=1)
        assert data["succeeded"] == 1

        # Document gone
        resp = await get_doc(client, auth_headers, doc_id)
        assert resp.status_code == 404

        # Registry cleaned up
        entry = await RegistryEntry.find_one({"entry_id": doc_id})
        assert entry is None


# =========================================================================
# File Orphan Cleanup
# =========================================================================


class TestFileOrphanCleanup:
    """Tests for file reference cleanup during hard-delete."""

    @pytest.mark.asyncio
    async def test_hard_delete_calls_orphan_cleanup(
        self, client, auth_headers, enable_hard_delete
    ):
        """Hard-delete triggers _hard_delete_orphaned_files (no-op with no file refs)."""
        doc_id, _ = await create_doc(client, auth_headers)

        data = await delete_doc(client, auth_headers, doc_id, hard_delete=True)

        assert data["succeeded"] == 1
        # Document has no file_references in this test, so orphan cleanup is a no-op
        # but the code path was exercised without error


# =========================================================================
# Soft-Delete Regression
# =========================================================================


class TestSoftDeleteRegression:
    """Verify soft-delete behavior is unchanged."""

    @pytest.mark.asyncio
    async def test_soft_delete_sets_inactive(self, client, auth_headers):
        """Default delete sets document status to inactive."""
        doc_id, _ = await create_doc(client, auth_headers)

        data = await delete_doc(client, auth_headers, doc_id)
        assert data["succeeded"] == 1

        # Document still exists, just inactive
        resp = await get_doc(client, auth_headers, doc_id)
        assert resp.status_code == 200
        assert resp.json()["status"] == "inactive"

    @pytest.mark.asyncio
    async def test_soft_delete_works_in_any_namespace(self, client, auth_headers):
        """Soft-delete works without deletion_mode check."""
        doc_id, _ = await create_doc(client, auth_headers)

        data = await delete_doc(client, auth_headers, doc_id)
        assert data["succeeded"] == 1
        assert data["results"][0]["status"] == "deleted"
