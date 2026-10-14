"""Tests for hard-delete support in Template-Store.

Covers:
- Version-specific hard-delete (removes one version, others remain)
- All-version hard-delete (removes all + Registry entry)
- Hard-delete rejected in 'retain' namespace
- Hard-delete blocked when child templates exist
- Soft-delete regression (unchanged when hard_delete=False)
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from template_store.services import dependency_service

# =========================================================================
# Fake Document Store
# =========================================================================
# The document-count guard calls Document Store over HTTP. Tests used to
# reach for http://localhost:8004, find nothing there, and get an "unknown"
# count — which the guard once let through. Every test here gets a fake
# Document Store instead, so the guard's real counting code runs against
# known answers. Only dependency_service's view of httpx is swapped; the
# test client is untouched.

_REAL_ASYNC_CLIENT = httpx.AsyncClient


class FakeDocumentStore:
    def __init__(self):
        self.active = 0          # rows counted with status=active
        self.all_statuses = 0    # rows counted without a status filter
        self.http_status = 200
        self.unreachable = False
        self.requests: list[dict] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.unreachable:
            raise httpx.ConnectError("Connection refused", request=request)
        params = dict(request.url.params)
        self.requests.append(params)
        if self.http_status != 200:
            return httpx.Response(self.http_status, text="forbidden")
        total = self.active if "status" in params else self.all_statuses
        return httpx.Response(200, json={"items": [], "total": total})


@pytest.fixture(autouse=True)
def doc_store(monkeypatch):
    fake = FakeDocumentStore()
    shim = SimpleNamespace(
        AsyncClient=lambda **kw: _REAL_ASYNC_CLIENT(
            transport=httpx.MockTransport(fake.handle), **kw
        )
    )
    monkeypatch.setattr(dependency_service, "httpx", shim)
    return fake

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
        payload["extends_version"] = 1
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


async def _delete_template(client, auth_headers, template_id, hard_delete=False, version=None, force=False):
    """Delete a template via bulk endpoint."""
    item = {"id": template_id, "hard_delete": hard_delete, "force": force}
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

        data = await _delete_template(client, auth_headers, tid, version=1)
        assert data["succeeded"] == 1

        # Template still exists, just inactive
        resp = await _get_template(client, auth_headers, tid)
        assert resp.status_code == 200
        assert resp.json()["status"] == "inactive"


# =========================================================================
# Hard-delete document guard
# =========================================================================
# Hard-delete is irreversible: a template that disappears orphans every
# document row still carrying its template_id. So it must refuse while any
# such row exists (force is the soft-delete escape hatch and does not
# apply), and refuse whenever the count cannot be established.


def _full_mode_registry():
    mock_rc = AsyncMock()
    mock_rc.get_namespace_deletion_mode = AsyncMock(return_value="full")
    mock_rc.hard_delete_entry = AsyncMock(return_value=True)
    return mock_rc


class TestHardDeleteDocumentGuard:

    @pytest.mark.asyncio
    async def test_documents_block_hard_delete_even_with_force(self, client, auth_headers, doc_store):
        tid = await _create_template(client, auth_headers, "HDG_FORCE", "Guard Force")
        doc_store.active = doc_store.all_statuses = 3
        with patch("template_store.services.template_service.get_registry_client") as mock_get:
            mock_get.return_value = _full_mode_registry()
            data = await _delete_template(client, auth_headers, tid, hard_delete=True, force=True)
        assert data["failed"] == 1
        error = data["results"][0]["error"]
        assert "3 document row(s)" in error
        assert "force=true does not apply" in error
        assert (await _get_template(client, auth_headers, tid)).status_code == 200
        mock_get.return_value.hard_delete_entry.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_archived_only_documents_still_block(self, client, auth_headers, doc_store):
        tid = await _create_template(client, auth_headers, "HDG_ARCHIVED", "Guard Archived")
        doc_store.active = 0
        doc_store.all_statuses = 2  # archived / inactive rows only
        data = await _delete_template(client, auth_headers, tid, hard_delete=True)
        assert data["failed"] == 1
        assert "2 document row(s) of any status" in data["results"][0]["error"]
        # The hard-delete count must not filter by status.
        assert any("status" not in r for r in doc_store.requests)

    @pytest.mark.asyncio
    async def test_non_200_count_refuses(self, client, auth_headers, doc_store):
        tid = await _create_template(client, auth_headers, "HDG_403", "Guard 403")
        doc_store.http_status = 403  # e.g. a stale service key
        data = await _delete_template(client, auth_headers, tid, hard_delete=True)
        assert data["failed"] == 1
        assert "cannot verify" in data["results"][0]["error"]
        assert "403" in data["results"][0]["error"]
        assert (await _get_template(client, auth_headers, tid)).status_code == 200

    @pytest.mark.asyncio
    async def test_unreachable_document_store_refuses(self, client, auth_headers, doc_store):
        tid = await _create_template(client, auth_headers, "HDG_DOWN", "Guard Down")
        doc_store.unreachable = True
        data = await _delete_template(client, auth_headers, tid, hard_delete=True, force=True)
        assert data["failed"] == 1
        assert "cannot verify" in data["results"][0]["error"]
        assert (await _get_template(client, auth_headers, tid)).status_code == 200

    @pytest.mark.asyncio
    async def test_no_documents_hard_delete_proceeds(self, client, auth_headers, doc_store):
        tid = await _create_template(client, auth_headers, "HDG_NONE", "Guard None")
        with patch("template_store.services.template_service.get_registry_client") as mock_get:
            mock_get.return_value = _full_mode_registry()
            data = await _delete_template(client, auth_headers, tid, hard_delete=True)
        assert data["succeeded"] == 1
        assert (await _get_template(client, auth_headers, tid)).status_code == 404

    @pytest.mark.asyncio
    async def test_soft_delete_with_documents_and_force_still_deactivates(self, client, auth_headers, doc_store):
        tid = await _create_template(client, auth_headers, "HDG_SOFT", "Guard Soft")
        doc_store.active = doc_store.all_statuses = 5
        blocked = await _delete_template(client, auth_headers, tid, version=1)
        assert blocked["failed"] == 1  # force=false still blocks a soft-delete
        data = await _delete_template(client, auth_headers, tid, version=1, force=True)
        assert data["succeeded"] == 1
        resp = await _get_template(client, auth_headers, tid, version=1)
        assert resp.json()["status"] == "inactive"

    @pytest.mark.asyncio
    async def test_soft_delete_with_unreachable_document_store_still_deactivates(self, client, auth_headers, doc_store):
        # Deliberately unchanged: deactivation is reversible.
        tid = await _create_template(client, auth_headers, "HDG_SOFT_DOWN", "Guard Soft Down")
        doc_store.unreachable = True
        data = await _delete_template(client, auth_headers, tid, version=1)
        assert data["succeeded"] == 1
