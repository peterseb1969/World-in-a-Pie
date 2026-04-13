"""Tests for resolve_namespace_filter — CASE-08 Phase 1.

Covers the four key scenarios:
1. Superadmin with no namespace → empty query (no filter)
2. Scoped identity with no namespace → $in filter with accessible namespaces
3. Any identity with explicit namespace → $in filter with single namespace
4. Identity with no accessible namespaces → 403
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from wip_auth.models import UserIdentity
from wip_auth.permissions import (
    NamespaceFilter,
    resolve_namespace_filter,
)


def _make_identity(user_id: str = "test-user", groups: list[str] | None = None) -> UserIdentity:
    return UserIdentity(
        user_id=user_id,
        username=user_id,
        auth_method="api_key",
        groups=groups or [],
    )


class TestNamespaceFilter:
    """NamespaceFilter dataclass basics."""

    def test_default_is_empty(self):
        nf = NamespaceFilter()
        assert nf.query == {}
        assert nf.namespaces is None

    def test_single_namespace(self):
        nf = NamespaceFilter(query={"namespace": {"$in": ["wip"]}}, namespaces=["wip"])
        assert nf.query == {"namespace": {"$in": ["wip"]}}
        assert nf.namespaces == ["wip"]

    def test_multi_namespace(self):
        nf = NamespaceFilter(
            query={"namespace": {"$in": ["wip", "test"]}},
            namespaces=["wip", "test"],
        )
        assert len(nf.namespaces) == 2


class TestResolveNamespaceFilter:
    """Tests for resolve_namespace_filter()."""

    @pytest.mark.asyncio
    async def test_superadmin_no_namespace_returns_empty_query(self):
        """Superadmin without namespace → no filter (all namespaces)."""
        identity = _make_identity(groups=["wip-admins"])

        with patch("wip_auth.permissions.get_auth_config") as mock_config:
            mock_config.return_value.admin_groups = ["wip-admins"]
            result = await resolve_namespace_filter(identity, None)

        assert result.query == {}
        assert result.namespaces is None

    @pytest.mark.asyncio
    async def test_scoped_identity_no_namespace_returns_in_filter(self):
        """Scoped user without namespace → $in filter with accessible namespaces."""
        identity = _make_identity(groups=["app-users"])

        with (
            patch("wip_auth.permissions.get_auth_config") as mock_config,
            patch("wip_auth.permissions.resolve_accessible_namespaces", new_callable=AsyncMock) as mock_resolve,
        ):
            mock_config.return_value.admin_groups = ["wip-admins"]
            mock_resolve.return_value = ["wip", "test"]

            result = await resolve_namespace_filter(identity, None)

        assert result.query == {"namespace": {"$in": ["wip", "test"]}}
        assert result.namespaces == ["wip", "test"]

    @pytest.mark.asyncio
    async def test_explicit_namespace_returns_single_in_filter(self):
        """Explicit namespace → permission check + single-element $in filter."""
        identity = _make_identity(groups=["app-users"])

        with patch("wip_auth.permissions.check_namespace_permission", new_callable=AsyncMock) as mock_check:
            result = await resolve_namespace_filter(identity, "wip")

        mock_check.assert_awaited_once_with(identity, "wip", "read")
        assert result.query == {"namespace": {"$in": ["wip"]}}
        assert result.namespaces == ["wip"]

    @pytest.mark.asyncio
    async def test_explicit_namespace_with_write_permission(self):
        """Explicit namespace with write required → passes required level through."""
        identity = _make_identity()

        with patch("wip_auth.permissions.check_namespace_permission", new_callable=AsyncMock) as mock_check:
            result = await resolve_namespace_filter(identity, "wip", required="write")

        mock_check.assert_awaited_once_with(identity, "wip", "write")
        assert result.query == {"namespace": {"$in": ["wip"]}}

    @pytest.mark.asyncio
    async def test_no_accessible_namespaces_raises_403(self):
        """Identity with no accessible namespaces → 403."""
        identity = _make_identity(groups=["app-users"])

        with (
            patch("wip_auth.permissions.get_auth_config") as mock_config,
            patch("wip_auth.permissions.resolve_accessible_namespaces", new_callable=AsyncMock) as mock_resolve,
        ):
            mock_config.return_value.admin_groups = ["wip-admins"]
            mock_resolve.return_value = []

            with pytest.raises(HTTPException) as exc_info:
                await resolve_namespace_filter(identity, None)

        assert exc_info.value.status_code == 403
        assert "No accessible namespaces" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_explicit_namespace_permission_denied_propagates(self):
        """Permission denied on explicit namespace → propagates the HTTPException."""
        identity = _make_identity()

        with patch(
            "wip_auth.permissions.check_namespace_permission",
            new_callable=AsyncMock,
            side_effect=HTTPException(404, "Namespace not found"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await resolve_namespace_filter(identity, "secret-ns")

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_query_dict_is_mergeable(self):
        """The returned query dict can be merged into an existing query."""
        identity = _make_identity(groups=["wip-admins"])

        with patch("wip_auth.permissions.get_auth_config") as mock_config:
            mock_config.return_value.admin_groups = ["wip-admins"]
            result = await resolve_namespace_filter(identity, None)

        # Superadmin: merge empty dict into existing query
        query = {"status": "active", "template_id": "abc"}
        query.update(result.query)
        assert query == {"status": "active", "template_id": "abc"}

        # Scoped: merge $in filter
        scoped_filter = NamespaceFilter(
            query={"namespace": {"$in": ["wip"]}},
            namespaces=["wip"],
        )
        query2 = {"status": "active"}
        query2.update(scoped_filter.query)
        assert query2 == {"status": "active", "namespace": {"$in": ["wip"]}}
