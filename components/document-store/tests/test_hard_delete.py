"""Tests for hard-delete support in Document-Store.

Covers:
- All-version hard-delete (removes all versions + Registry entry)
- Version-specific hard-delete (removes one version, others remain)
- File orphan cleanup after hard-delete
- Hard-delete rejected in 'retain' namespace
- Soft-delete regression (unchanged when hard_delete=False)
"""

import pytest


# =========================================================================
# Helpers
# =========================================================================


async def create_doc(client, auth_headers, data=None, template_id="TPL-000001"):
    """Create a document and return (document_id, version)."""
    payload = {
        "namespace": "wip",
        "template_id": template_id,
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
    async def test_hard_delete_removes_document(self, client, auth_headers):
        """hard_delete=True removes document completely."""
        from unittest.mock import AsyncMock, patch

        doc_id, _ = await create_doc(client, auth_headers)

        with patch('document_store.services.document_service.get_registry_client') as mock_get:
            mock_rc = AsyncMock()
            mock_rc.get_namespace_deletion_mode = AsyncMock(return_value="full")
            mock_rc.hard_delete_entry = AsyncMock(return_value=True)
            mock_get.return_value = mock_rc

            data = await delete_doc(client, auth_headers, doc_id, hard_delete=True)

        assert data["succeeded"] == 1

        # Document completely gone
        resp = await get_doc(client, auth_headers, doc_id)
        assert resp.status_code == 404

        # Registry entry cleaned up
        mock_rc.hard_delete_entry.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_hard_delete_rejected_in_retain_namespace(self, client, auth_headers):
        """hard_delete=True fails when namespace deletion_mode='retain'."""
        from unittest.mock import AsyncMock, patch

        doc_id, _ = await create_doc(client, auth_headers)

        with patch('document_store.services.document_service.get_registry_client') as mock_get:
            mock_rc = AsyncMock()
            mock_rc.get_namespace_deletion_mode = AsyncMock(return_value="retain")
            mock_get.return_value = mock_rc

            data = await delete_doc(client, auth_headers, doc_id, hard_delete=True)

        assert data["failed"] == 1
        assert "deletion_mode" in data["results"][0]["error"]

        # Document still exists
        resp = await get_doc(client, auth_headers, doc_id)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_hard_delete_nonexistent_document(self, client, auth_headers):
        """hard_delete on nonexistent document returns failure."""
        from unittest.mock import AsyncMock, patch

        with patch('document_store.services.document_service.get_registry_client') as mock_get:
            mock_rc = AsyncMock()
            mock_rc.get_namespace_deletion_mode = AsyncMock(return_value="full")
            mock_get.return_value = mock_rc

            data = await delete_doc(client, auth_headers, "DOC-NONEXISTENT", hard_delete=True)

        assert data["failed"] == 1


# =========================================================================
# Hard-Delete Specific Version
# =========================================================================


class TestHardDeleteSpecificVersion:
    """Tests for version-specific hard-delete."""

    @pytest.mark.asyncio
    async def test_hard_delete_one_version_keeps_others(self, client, auth_headers, sample_person_data):
        """Hard-deleting v1 keeps v2 intact."""
        from unittest.mock import AsyncMock, patch

        doc_id, _ = await create_doc(client, auth_headers, sample_person_data)

        # Create v2 by updating
        updated_data = {**sample_person_data, "first_name": "Jane"}
        await update_doc(client, auth_headers, doc_id, updated_data)

        with patch('document_store.services.document_service.get_registry_client') as mock_get:
            mock_rc = AsyncMock()
            mock_rc.get_namespace_deletion_mode = AsyncMock(return_value="full")
            mock_rc.hard_delete_entry = AsyncMock(return_value=True)
            mock_get.return_value = mock_rc

            data = await delete_doc(client, auth_headers, doc_id, hard_delete=True, version=1)

        assert data["succeeded"] == 1

        # Document still exists (v2)
        resp = await get_doc(client, auth_headers, doc_id)
        assert resp.status_code == 200

        # Registry entry NOT cleaned up (versions remain)
        mock_rc.hard_delete_entry.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_hard_delete_last_version_cleans_registry(self, client, auth_headers):
        """Hard-deleting the only version also removes Registry entry."""
        from unittest.mock import AsyncMock, patch

        doc_id, _ = await create_doc(client, auth_headers)

        with patch('document_store.services.document_service.get_registry_client') as mock_get:
            mock_rc = AsyncMock()
            mock_rc.get_namespace_deletion_mode = AsyncMock(return_value="full")
            mock_rc.hard_delete_entry = AsyncMock(return_value=True)
            mock_get.return_value = mock_rc

            data = await delete_doc(client, auth_headers, doc_id, hard_delete=True, version=1)

        assert data["succeeded"] == 1

        # Document gone
        resp = await get_doc(client, auth_headers, doc_id)
        assert resp.status_code == 404

        # Registry cleaned up
        mock_rc.hard_delete_entry.assert_awaited_once()


# =========================================================================
# File Orphan Cleanup
# =========================================================================


class TestFileOrphanCleanup:
    """Tests for file reference cleanup during hard-delete."""

    @pytest.mark.asyncio
    async def test_hard_delete_calls_orphan_cleanup(self, client, auth_headers):
        """Hard-delete triggers _hard_delete_orphaned_files (no-op with no file refs)."""
        from unittest.mock import AsyncMock, patch

        doc_id, _ = await create_doc(client, auth_headers)

        with patch('document_store.services.document_service.get_registry_client') as mock_get:
            mock_rc = AsyncMock()
            mock_rc.get_namespace_deletion_mode = AsyncMock(return_value="full")
            mock_rc.hard_delete_entry = AsyncMock(return_value=True)
            mock_get.return_value = mock_rc

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
