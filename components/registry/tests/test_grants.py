"""Comprehensive tests for Registry namespace grants/permissions API."""

import pytest
import pytest_asyncio
from httpx import AsyncClient

from registry.models.grant import NamespaceGrant


@pytest_asyncio.fixture(autouse=True)
async def clean_grants(client: AsyncClient):
    """Clean up grants before each test (conftest doesn't clean them)."""
    await NamespaceGrant.delete_all()
    yield
    await NamespaceGrant.delete_all()


class TestCreateGrants:
    """Tests for creating namespace grants."""

    @pytest.mark.asyncio
    async def test_create_grant(self, client: AsyncClient, auth_headers: dict):
        """Test creating a grant for a namespace and verify it appears in list."""
        response = await client.post(
            "/api/registry/namespaces/default/grants",
            json=[{
                "subject": "alice@example.com",
                "subject_type": "user",
                "permission": "read",
            }],
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["succeeded"] == 1
        assert data["failed"] == 0
        assert data["results"][0]["status"] == "created"
        assert data["results"][0]["subject"] == "alice@example.com"
        assert data["results"][0]["permission"] == "read"

        # Verify grant appears in list
        list_resp = await client.get(
            "/api/registry/namespaces/default/grants",
            headers=auth_headers,
        )
        assert list_resp.status_code == 200
        grants = list_resp.json()
        assert len(grants) == 1
        assert grants[0]["subject"] == "alice@example.com"
        assert grants[0]["subject_type"] == "user"
        assert grants[0]["permission"] == "read"
        assert grants[0]["namespace"] == "default"

    @pytest.mark.asyncio
    async def test_create_multiple_grants_bulk(self, client: AsyncClient, auth_headers: dict):
        """Test creating multiple grants in a single bulk request."""
        response = await client.post(
            "/api/registry/namespaces/default/grants",
            json=[
                {"subject": "alice@example.com", "subject_type": "user", "permission": "read"},
                {"subject": "bob@example.com", "subject_type": "user", "permission": "write"},
                {"subject": "editors", "subject_type": "group", "permission": "write"},
            ],
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert data["succeeded"] == 3
        assert data["failed"] == 0

        # Verify all appear in list
        list_resp = await client.get(
            "/api/registry/namespaces/default/grants",
            headers=auth_headers,
        )
        assert len(list_resp.json()) == 3

    @pytest.mark.asyncio
    async def test_create_grant_for_nonexistent_namespace(self, client: AsyncClient, auth_headers: dict):
        """Test creating a grant for a non-existent namespace returns 404."""
        response = await client.post(
            "/api/registry/namespaces/nonexistent-ns/grants",
            json=[{
                "subject": "alice@example.com",
                "subject_type": "user",
                "permission": "read",
            }],
            headers=auth_headers,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_create_duplicate_grant_upserts(self, client: AsyncClient, auth_headers: dict):
        """Test that creating a duplicate grant updates (upserts) the existing one."""
        # Create initial grant with read
        resp1 = await client.post(
            "/api/registry/namespaces/default/grants",
            json=[{
                "subject": "alice@example.com",
                "subject_type": "user",
                "permission": "read",
            }],
            headers=auth_headers,
        )
        assert resp1.status_code == 200
        assert resp1.json()["results"][0]["status"] == "created"

        # Create same grant again with write — should upsert
        resp2 = await client.post(
            "/api/registry/namespaces/default/grants",
            json=[{
                "subject": "alice@example.com",
                "subject_type": "user",
                "permission": "write",
            }],
            headers=auth_headers,
        )
        assert resp2.status_code == 200
        assert resp2.json()["results"][0]["status"] == "updated"
        assert resp2.json()["results"][0]["permission"] == "write"

        # Verify only one grant exists and it has the updated permission
        list_resp = await client.get(
            "/api/registry/namespaces/default/grants",
            headers=auth_headers,
        )
        grants = list_resp.json()
        assert len(grants) == 1
        assert grants[0]["permission"] == "write"

    @pytest.mark.asyncio
    async def test_create_grant_with_expiry(self, client: AsyncClient, auth_headers: dict):
        """Test creating a grant with an expiration date."""
        response = await client.post(
            "/api/registry/namespaces/default/grants",
            json=[{
                "subject": "temp-user@example.com",
                "subject_type": "user",
                "permission": "read",
                "expires_at": "2099-12-31T23:59:59Z",
            }],
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["results"][0]["status"] == "created"

        # Verify expiry is stored
        list_resp = await client.get(
            "/api/registry/namespaces/default/grants",
            headers=auth_headers,
        )
        grants = list_resp.json()
        assert len(grants) == 1
        assert grants[0]["expires_at"] is not None


class TestListGrants:
    """Tests for listing namespace grants."""

    @pytest.mark.asyncio
    async def test_list_grants_empty(self, client: AsyncClient, auth_headers: dict):
        """Test listing grants for a namespace with no grants."""
        response = await client.get(
            "/api/registry/namespaces/default/grants",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_list_grants_structure(self, client: AsyncClient, auth_headers: dict):
        """Test that listed grants have the correct response structure."""
        await client.post(
            "/api/registry/namespaces/default/grants",
            json=[{
                "subject": "alice@example.com",
                "subject_type": "user",
                "permission": "write",
            }],
            headers=auth_headers,
        )

        response = await client.get(
            "/api/registry/namespaces/default/grants",
            headers=auth_headers,
        )
        assert response.status_code == 200
        grants = response.json()
        assert len(grants) == 1
        grant = grants[0]
        assert "namespace" in grant
        assert "subject" in grant
        assert "subject_type" in grant
        assert "permission" in grant
        assert "granted_by" in grant
        assert "granted_at" in grant
        assert "expires_at" in grant

    @pytest.mark.asyncio
    async def test_list_grants_only_for_requested_namespace(self, client: AsyncClient, auth_headers: dict):
        """Test that listing grants returns only grants for the specified namespace."""
        # Create grants on two different namespaces
        await client.post(
            "/api/registry/namespaces/default/grants",
            json=[{"subject": "user-default@example.com", "subject_type": "user", "permission": "read"}],
            headers=auth_headers,
        )
        await client.post(
            "/api/registry/namespaces/vendor1/grants",
            json=[{"subject": "user-vendor1@example.com", "subject_type": "user", "permission": "read"}],
            headers=auth_headers,
        )

        # List grants for default — should only see the one grant
        response = await client.get(
            "/api/registry/namespaces/default/grants",
            headers=auth_headers,
        )
        assert response.status_code == 200
        grants = response.json()
        assert len(grants) == 1
        assert grants[0]["subject"] == "user-default@example.com"


class TestRevokeGrants:
    """Tests for revoking namespace grants."""

    @pytest.mark.asyncio
    async def test_revoke_grant(self, client: AsyncClient, auth_headers: dict):
        """Test revoking a grant removes it."""
        # Create grant
        await client.post(
            "/api/registry/namespaces/default/grants",
            json=[{"subject": "alice@example.com", "subject_type": "user", "permission": "read"}],
            headers=auth_headers,
        )

        # Revoke it
        response = await client.request(
            "DELETE",
            "/api/registry/namespaces/default/grants",
            json=[{"subject": "alice@example.com", "subject_type": "user"}],
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["succeeded"] == 1
        assert data["results"][0]["status"] == "revoked"

        # Verify grant is gone
        list_resp = await client.get(
            "/api/registry/namespaces/default/grants",
            headers=auth_headers,
        )
        assert list_resp.json() == []

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_grant(self, client: AsyncClient, auth_headers: dict):
        """Test revoking a non-existent grant returns not_found status."""
        response = await client.request(
            "DELETE",
            "/api/registry/namespaces/default/grants",
            json=[{"subject": "nobody@example.com", "subject_type": "user"}],
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["results"][0]["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_revoke_bulk(self, client: AsyncClient, auth_headers: dict):
        """Test revoking multiple grants in a single bulk request."""
        # Create multiple grants
        await client.post(
            "/api/registry/namespaces/default/grants",
            json=[
                {"subject": "alice@example.com", "subject_type": "user", "permission": "read"},
                {"subject": "bob@example.com", "subject_type": "user", "permission": "write"},
            ],
            headers=auth_headers,
        )

        # Revoke both
        response = await client.request(
            "DELETE",
            "/api/registry/namespaces/default/grants",
            json=[
                {"subject": "alice@example.com", "subject_type": "user"},
                {"subject": "bob@example.com", "subject_type": "user"},
            ],
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert data["succeeded"] == 2

        # Verify all gone
        list_resp = await client.get(
            "/api/registry/namespaces/default/grants",
            headers=auth_headers,
        )
        assert list_resp.json() == []


class TestPermissionLevels:
    """Tests for different grant permission levels."""

    @pytest.mark.asyncio
    async def test_create_read_grant(self, client: AsyncClient, auth_headers: dict):
        """Test creating a grant with read permission."""
        response = await client.post(
            "/api/registry/namespaces/default/grants",
            json=[{"subject": "reader@example.com", "subject_type": "user", "permission": "read"}],
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["results"][0]["permission"] == "read"

    @pytest.mark.asyncio
    async def test_create_write_grant(self, client: AsyncClient, auth_headers: dict):
        """Test creating a grant with write permission."""
        response = await client.post(
            "/api/registry/namespaces/default/grants",
            json=[{"subject": "writer@example.com", "subject_type": "user", "permission": "write"}],
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["results"][0]["permission"] == "write"

    @pytest.mark.asyncio
    async def test_create_admin_grant(self, client: AsyncClient, auth_headers: dict):
        """Test creating a grant with admin permission."""
        response = await client.post(
            "/api/registry/namespaces/default/grants",
            json=[{"subject": "admin@example.com", "subject_type": "user", "permission": "admin"}],
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["results"][0]["permission"] == "admin"

    @pytest.mark.asyncio
    async def test_create_group_grant(self, client: AsyncClient, auth_headers: dict):
        """Test creating a grant for a group subject."""
        response = await client.post(
            "/api/registry/namespaces/default/grants",
            json=[{"subject": "engineering", "subject_type": "group", "permission": "write"}],
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["results"][0]["status"] == "created"

        list_resp = await client.get(
            "/api/registry/namespaces/default/grants",
            headers=auth_headers,
        )
        grants = list_resp.json()
        assert len(grants) == 1
        assert grants[0]["subject_type"] == "group"

    @pytest.mark.asyncio
    async def test_create_api_key_grant(self, client: AsyncClient, auth_headers: dict):
        """Test creating a grant for an api_key subject."""
        response = await client.post(
            "/api/registry/namespaces/default/grants",
            json=[{"subject": "etl-service", "subject_type": "api_key", "permission": "write"}],
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["results"][0]["status"] == "created"

        list_resp = await client.get(
            "/api/registry/namespaces/default/grants",
            headers=auth_headers,
        )
        grants = list_resp.json()
        assert len(grants) == 1
        assert grants[0]["subject_type"] == "api_key"


class TestMyNamespaces:
    """Tests for the my_namespaces endpoint."""

    @pytest.mark.asyncio
    async def test_my_namespaces_superadmin_sees_all(self, client: AsyncClient, auth_headers: dict):
        """Test that superadmin (master API key) sees all active namespaces."""
        response = await client.get(
            "/api/registry/my/namespaces",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        # Master API key is superadmin via wip-admins group, so sees all namespaces
        prefixes = [ns["prefix"] for ns in data]
        assert "default" in prefixes
        assert "vendor1" in prefixes
        assert "vendor2" in prefixes
        # Each should have admin permission for superadmin
        for ns in data:
            assert ns["permission"] == "admin"

    @pytest.mark.asyncio
    async def test_my_namespaces_response_structure(self, client: AsyncClient, auth_headers: dict):
        """Test the response structure of my_namespaces."""
        response = await client.get(
            "/api/registry/my/namespaces",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        ns = data[0]
        assert "prefix" in ns
        assert "description" in ns
        assert "permission" in ns


class TestMyNamespacePermission:
    """Tests for the my_namespace_permission endpoint."""

    @pytest.mark.asyncio
    async def test_my_permission_superadmin(self, client: AsyncClient, auth_headers: dict):
        """Test that superadmin gets admin permission on any namespace."""
        response = await client.get(
            "/api/registry/my/namespaces/default/permission",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["namespace"] == "default"
        assert data["permission"] == "admin"

    @pytest.mark.asyncio
    async def test_my_permission_nonexistent_namespace(self, client: AsyncClient, auth_headers: dict):
        """Test that checking permission on non-existent namespace returns 404."""
        response = await client.get(
            "/api/registry/my/namespaces/nonexistent-ns/permission",
            headers=auth_headers,
        )
        assert response.status_code == 404


class TestCheckPermissionInternal:
    """Tests for the internal check_permission endpoint."""

    @pytest.mark.asyncio
    async def test_check_permission_with_grant(self, client: AsyncClient, auth_headers: dict):
        """Test checking permission for a user who has a grant."""
        # Create a grant for a specific user
        await client.post(
            "/api/registry/namespaces/default/grants",
            json=[{
                "subject": "alice@example.com",
                "subject_type": "user",
                "permission": "write",
            }],
            headers=auth_headers,
        )

        # Check permission for that user via internal endpoint
        response = await client.get(
            "/api/registry/my/check-permission",
            params={
                "namespace": "default",
                "user_id": "alice-user-id",
                "email": "alice@example.com",
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["namespace"] == "default"
        assert data["user_id"] == "alice-user-id"
        assert data["permission"] == "write"

    @pytest.mark.asyncio
    async def test_check_permission_no_grant(self, client: AsyncClient, auth_headers: dict):
        """Test checking permission for a user with no grant returns none."""
        response = await client.get(
            "/api/registry/my/check-permission",
            params={
                "namespace": "default",
                "user_id": "nobody-id",
                "email": "nobody@example.com",
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["permission"] == "none"

    @pytest.mark.asyncio
    async def test_check_permission_with_group_grant(self, client: AsyncClient, auth_headers: dict):
        """Test checking permission for a user whose group has a grant."""
        # Create a group grant
        await client.post(
            "/api/registry/namespaces/default/grants",
            json=[{
                "subject": "engineering",
                "subject_type": "group",
                "permission": "read",
            }],
            headers=auth_headers,
        )

        # Check permission for a user in that group
        response = await client.get(
            "/api/registry/my/check-permission",
            params={
                "namespace": "default",
                "user_id": "bob-id",
                "email": "bob@example.com",
                "groups": "engineering,design",
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["permission"] == "read"

    @pytest.mark.asyncio
    async def test_check_permission_superadmin_user(self, client: AsyncClient, auth_headers: dict):
        """Test that a user in wip-admins group gets admin permission everywhere."""
        response = await client.get(
            "/api/registry/my/check-permission",
            params={
                "namespace": "default",
                "user_id": "superadmin-id",
                "email": "admin@example.com",
                "groups": "wip-admins",
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["permission"] == "admin"

    @pytest.mark.asyncio
    async def test_check_permission_via_x_user_groups_header(self, client: AsyncClient, auth_headers: dict):
        """Test that groups can be provided via X-User-Groups header."""
        # Create a group grant
        await client.post(
            "/api/registry/namespaces/default/grants",
            json=[{
                "subject": "reviewers",
                "subject_type": "group",
                "permission": "write",
            }],
            headers=auth_headers,
        )

        # Check permission using X-User-Groups header instead of query param
        headers = {**auth_headers, "X-User-Groups": "reviewers,viewers"}
        response = await client.get(
            "/api/registry/my/check-permission",
            params={
                "namespace": "default",
                "user_id": "carol-id",
                "email": "carol@example.com",
            },
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["permission"] == "write"

    @pytest.mark.asyncio
    async def test_check_permission_highest_grant_wins(self, client: AsyncClient, auth_headers: dict):
        """Test that the highest permission from multiple grants is returned."""
        # Create both a user grant (read) and a group grant (write) for the same namespace
        await client.post(
            "/api/registry/namespaces/default/grants",
            json=[
                {"subject": "multi@example.com", "subject_type": "user", "permission": "read"},
                {"subject": "power-users", "subject_type": "group", "permission": "write"},
            ],
            headers=auth_headers,
        )

        response = await client.get(
            "/api/registry/my/check-permission",
            params={
                "namespace": "default",
                "user_id": "multi-id",
                "email": "multi@example.com",
                "groups": "power-users",
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        # write > read, so write should win
        assert response.json()["permission"] == "write"


class TestAccessibleNamespacesInternal:
    """Tests for the internal accessible-namespaces endpoint."""

    @pytest.mark.asyncio
    async def test_accessible_namespaces_superadmin(self, client: AsyncClient, auth_headers: dict):
        """Test that superadmin user gets is_superadmin=True with no namespace list."""
        response = await client.get(
            "/api/registry/my/accessible-namespaces",
            params={
                "user_id": "admin-id",
                "groups": "wip-admins",
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["is_superadmin"] is True
        assert data["namespaces"] is None

    @pytest.mark.asyncio
    async def test_accessible_namespaces_with_grants(self, client: AsyncClient, auth_headers: dict):
        """Test that a user with grants sees only their accessible namespaces."""
        # Grant on default only
        await client.post(
            "/api/registry/namespaces/default/grants",
            json=[{"subject": "limited@example.com", "subject_type": "user", "permission": "read"}],
            headers=auth_headers,
        )

        response = await client.get(
            "/api/registry/my/accessible-namespaces",
            params={
                "user_id": "limited-id",
                "email": "limited@example.com",
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["is_superadmin"] is False
        assert "default" in data["namespaces"]
        # Should not have access to vendor1/vendor2 without grants
        assert "vendor1" not in data["namespaces"]
        assert "vendor2" not in data["namespaces"]

    @pytest.mark.asyncio
    async def test_accessible_namespaces_no_grants(self, client: AsyncClient, auth_headers: dict):
        """Test that a user with no grants gets an empty list."""
        response = await client.get(
            "/api/registry/my/accessible-namespaces",
            params={
                "user_id": "nobody-id",
                "email": "nobody@example.com",
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["is_superadmin"] is False
        assert data["namespaces"] == []


class TestAuthEnforcement:
    """Tests verifying that grant endpoints require authentication."""

    @pytest.mark.asyncio
    async def test_list_grants_no_auth(self, client: AsyncClient):
        """Test that listing grants without auth is rejected."""
        response = await client.get("/api/registry/namespaces/default/grants")
        assert response.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_create_grants_no_auth(self, client: AsyncClient):
        """Test that creating grants without auth is rejected."""
        response = await client.post(
            "/api/registry/namespaces/default/grants",
            json=[{"subject": "alice@example.com", "subject_type": "user", "permission": "read"}],
        )
        assert response.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_revoke_grants_no_auth(self, client: AsyncClient):
        """Test that revoking grants without auth is rejected."""
        response = await client.request(
            "DELETE",
            "/api/registry/namespaces/default/grants",
            json=[{"subject": "alice@example.com", "subject_type": "user"}],
        )
        assert response.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_my_namespaces_no_auth(self, client: AsyncClient):
        """Test that my_namespaces without auth is rejected."""
        response = await client.get("/api/registry/my/namespaces")
        assert response.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_my_permission_no_auth(self, client: AsyncClient):
        """Test that my_namespace_permission without auth is rejected."""
        response = await client.get("/api/registry/my/namespaces/default/permission")
        assert response.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_check_permission_no_auth(self, client: AsyncClient):
        """Test that check_permission_internal without auth is rejected."""
        response = await client.get(
            "/api/registry/my/check-permission",
            params={"namespace": "default", "user_id": "test"},
        )
        assert response.status_code in (401, 403)


class TestLockedNamespace:
    """Tests for grant behaviour on locked namespaces."""

    @pytest.mark.asyncio
    async def test_locked_namespace_returns_none_permission(self, client: AsyncClient, auth_headers: dict):
        """Test that a locked namespace always returns 'none' permission even for granted users."""
        # Create a full-deletion namespace, grant access, then lock it via deletion
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "to-lock", "deletion_mode": "full"},
            headers=auth_headers,
        )
        await client.post(
            "/api/registry/namespaces/to-lock/grants",
            json=[{"subject": "alice@example.com", "subject_type": "user", "permission": "admin"}],
            headers=auth_headers,
        )

        # Check permission before deletion — alice should have admin
        resp_before = await client.get(
            "/api/registry/my/check-permission",
            params={
                "namespace": "to-lock",
                "user_id": "alice-id",
                "email": "alice@example.com",
            },
            headers=auth_headers,
        )
        assert resp_before.status_code == 200
        assert resp_before.json()["permission"] == "admin"

        # Delete the namespace (which locks it during deletion)
        await client.delete(
            "/api/registry/namespaces/to-lock",
            headers=auth_headers,
        )

        # After deletion, namespace is deleted/locked — permission should be none
        resp_after = await client.get(
            "/api/registry/my/check-permission",
            params={
                "namespace": "to-lock",
                "user_id": "alice-id",
                "email": "alice@example.com",
            },
            headers=auth_headers,
        )
        assert resp_after.status_code == 200
        assert resp_after.json()["permission"] == "none"
