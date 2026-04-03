"""Tests for fastapi_helpers — namespace derivation from identity."""

from unittest.mock import AsyncMock, patch

import pytest

from wip_auth.fastapi_helpers import (
    _derive_namespace_from_identity,
    resolve_or_404,
)
from wip_auth.identity import set_current_identity
from wip_auth.models import UserIdentity


def _make_identity(namespaces=None, auth_method="api_key"):
    """Create a UserIdentity with given namespace scope."""
    return UserIdentity(
        user_id="apikey:test",
        username="test",
        email=None,
        groups=["wip-writers"],
        auth_method=auth_method,
        provider="api_key",
        raw_claims={"namespaces": namespaces},
    )


class TestDeriveNamespaceFromIdentity:
    """Tests for _derive_namespace_from_identity."""

    def test_single_namespace_key(self):
        set_current_identity(_make_identity(namespaces=["my-app"]))
        assert _derive_namespace_from_identity() == "my-app"

    def test_multi_namespace_key_returns_none(self):
        set_current_identity(_make_identity(namespaces=["ns-a", "ns-b"]))
        assert _derive_namespace_from_identity() is None

    def test_empty_namespace_list_returns_none(self):
        set_current_identity(_make_identity(namespaces=[]))
        assert _derive_namespace_from_identity() is None

    def test_null_namespaces_returns_none(self):
        set_current_identity(_make_identity(namespaces=None))
        assert _derive_namespace_from_identity() is None

    def test_no_identity_returns_none(self):
        set_current_identity(None)
        assert _derive_namespace_from_identity() is None

    def test_jwt_identity_with_no_namespaces(self):
        set_current_identity(_make_identity(namespaces=None, auth_method="jwt"))
        assert _derive_namespace_from_identity() is None


class TestResolveOr404NamespaceDerivation:
    """Tests that resolve_or_404 derives namespace from identity."""

    @pytest.mark.asyncio
    async def test_derives_namespace_from_single_scope_key(self):
        """When namespace is None but key has one namespace, resolution uses it."""
        set_current_identity(_make_identity(namespaces=["aa"]))

        with patch(
            "wip_auth.fastapi_helpers.resolve_entity_id",
            new_callable=AsyncMock,
            return_value="uuid-123",
        ) as mock_resolve:
            result = await resolve_or_404("AA_CHAPTER", "template", None)
            assert result == "uuid-123"
            mock_resolve.assert_called_once_with("AA_CHAPTER", "template", "aa")

    @pytest.mark.asyncio
    async def test_explicit_namespace_takes_precedence(self):
        """Explicit namespace is used even if key has a single namespace."""
        set_current_identity(_make_identity(namespaces=["aa"]))

        with patch(
            "wip_auth.fastapi_helpers.resolve_entity_id",
            new_callable=AsyncMock,
            return_value="uuid-456",
        ) as mock_resolve:
            result = await resolve_or_404("MY_THING", "template", "other-ns")
            assert result == "uuid-456"
            mock_resolve.assert_called_once_with("MY_THING", "template", "other-ns")

    @pytest.mark.asyncio
    async def test_multi_namespace_key_no_derivation(self):
        """Multi-namespace key without explicit namespace returns raw ID."""
        set_current_identity(_make_identity(namespaces=["ns-a", "ns-b"]))

        result = await resolve_or_404("AA_CHAPTER", "template", None)
        assert result == "AA_CHAPTER"  # raw, unresolved

    @pytest.mark.asyncio
    async def test_unscoped_key_no_derivation(self):
        """Unscoped key (namespaces=None) without explicit namespace returns raw ID."""
        set_current_identity(_make_identity(namespaces=None))

        result = await resolve_or_404("AA_CHAPTER", "template", None)
        assert result == "AA_CHAPTER"  # raw, unresolved
