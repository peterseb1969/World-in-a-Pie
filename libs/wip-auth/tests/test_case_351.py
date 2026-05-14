"""Tests for CASE-351 — forward api-key namespace scope to Registry.

The bug: a runtime API key with `namespaces=["aa"]` got 404 on def-store
endpoints because the Registry's internal permission check received a
synthetic identity stripped of `raw_claims["namespaces"]`, so its
"api_key namespace fallback" (grants.py:121-124) became dead code.

The fix: wip-auth forwards the api-key's namespace scope on the outbound
call to the Registry via a new `X-Key-Namespaces` header (matching the
existing `X-User-Groups` pattern). The Registry side parses the header
and reconstructs `raw_claims["namespaces"]` on its synthetic identity.

These tests pin the wip-auth half:

  - `_fetch_permission_from_registry` sends X-Key-Namespaces when the
    identity carries `raw_claims["namespaces"]`.
  - Same for `_fetch_accessible_from_registry`.
  - When `raw_claims is None` or has no namespaces key, the header is
    absent — preserving today's behaviour for OIDC users and for
    unscoped admin/services api-keys.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from wip_auth.models import UserIdentity
from wip_auth.permissions import (
    _fetch_accessible_from_registry,
    _fetch_permission_from_registry,
    clear_permission_cache,
)


@pytest.fixture(autouse=True)
def _clear_caches():
    """Each test starts with cold caches so prior tests don't leak."""
    clear_permission_cache()
    yield
    clear_permission_cache()


def _scoped_api_key_identity(namespaces: list[str]) -> UserIdentity:
    """An api-key identity that the api-key provider would produce
    for a key created with `namespaces=[...]` and no group."""
    return UserIdentity(
        user_id="apikey:laptop-rc",
        username="laptop-rc",
        auth_method="api_key",
        groups=[],
        raw_claims={
            "key_name": "laptop-rc",
            "owner": "peter",
            "namespaces": namespaces,
        },
    )


def _unscoped_admin_identity() -> UserIdentity:
    """An admin api-key with no namespaces list — wip-admins group
    is what grants access, not the namespaces field."""
    return UserIdentity(
        user_id="apikey:admin",
        username="admin",
        auth_method="api_key",
        groups=["wip-admins"],
        raw_claims={
            "key_name": "admin",
            "owner": "system",
            "namespaces": None,
        },
    )


def _oidc_identity() -> UserIdentity:
    """An OIDC user — never had raw_claims["namespaces"]."""
    return UserIdentity(
        user_id="user@example.com",
        username="user@example.com",
        email="user@example.com",
        auth_method="jwt",
        groups=[],
        raw_claims={"sub": "user@example.com", "email": "user@example.com"},
    )


def _mock_response(status: int = 200, json_data: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_data or {"permission": "read"}
    return resp


def _captured_headers(mock_get: AsyncMock) -> dict:
    """Pull the `headers=` kwarg from the last mocked client.get call."""
    return mock_get.call_args.kwargs["headers"]


# ──────────────────────────────────────────────────────────────────────
# _fetch_permission_from_registry — outbound header


class TestFetchPermissionHeaders:
    @pytest.mark.asyncio
    async def test_scoped_key_sends_x_key_namespaces_header(self):
        identity = _scoped_api_key_identity(["aa"])
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=_mock_response())
            mock_client_class.return_value.__aenter__.return_value = mock_client

            await _fetch_permission_from_registry(identity, "aa")

            headers = _captured_headers(mock_client.get)
            assert headers.get("X-Key-Namespaces") == "aa"

    @pytest.mark.asyncio
    async def test_multi_namespace_key_joins_with_comma(self):
        identity = _scoped_api_key_identity(["aa", "kb", "wip"])
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=_mock_response())
            mock_client_class.return_value.__aenter__.return_value = mock_client

            await _fetch_permission_from_registry(identity, "aa")

            headers = _captured_headers(mock_client.get)
            assert headers.get("X-Key-Namespaces") == "aa,kb,wip"

    @pytest.mark.asyncio
    async def test_unscoped_admin_key_no_header(self):
        """Admin api-keys with namespaces=None must NOT send the header.
        Sending it would force them into the scoped branch on the Registry
        side, breaking superadmin behaviour."""
        identity = _unscoped_admin_identity()
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=_mock_response())
            mock_client_class.return_value.__aenter__.return_value = mock_client

            await _fetch_permission_from_registry(identity, "aa")

            headers = _captured_headers(mock_client.get)
            assert "X-Key-Namespaces" not in headers

    @pytest.mark.asyncio
    async def test_oidc_user_no_header(self):
        """OIDC users never have raw_claims['namespaces'] — no header."""
        identity = _oidc_identity()
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=_mock_response())
            mock_client_class.return_value.__aenter__.return_value = mock_client

            await _fetch_permission_from_registry(identity, "aa")

            headers = _captured_headers(mock_client.get)
            assert "X-Key-Namespaces" not in headers

    @pytest.mark.asyncio
    async def test_identity_with_no_raw_claims_no_header(self):
        """raw_claims=None must not crash the forwarding path."""
        identity = UserIdentity(
            user_id="apikey:legacy",
            username="legacy",
            auth_method="api_key",
            groups=[],
            raw_claims=None,
        )
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=_mock_response())
            mock_client_class.return_value.__aenter__.return_value = mock_client

            await _fetch_permission_from_registry(identity, "aa")

            headers = _captured_headers(mock_client.get)
            assert "X-Key-Namespaces" not in headers


# ──────────────────────────────────────────────────────────────────────
# _fetch_accessible_from_registry — outbound header (same pattern)


class TestFetchAccessibleHeaders:
    @pytest.mark.asyncio
    async def test_scoped_key_sends_x_key_namespaces_header(self):
        identity = _scoped_api_key_identity(["aa"])
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                return_value=_mock_response(
                    json_data={"namespaces": ["aa"], "is_superadmin": False}
                )
            )
            mock_client_class.return_value.__aenter__.return_value = mock_client

            await _fetch_accessible_from_registry(identity)

            headers = _captured_headers(mock_client.get)
            assert headers.get("X-Key-Namespaces") == "aa"

    @pytest.mark.asyncio
    async def test_unscoped_admin_no_header(self):
        identity = _unscoped_admin_identity()
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                return_value=_mock_response(
                    json_data={"namespaces": None, "is_superadmin": True}
                )
            )
            mock_client_class.return_value.__aenter__.return_value = mock_client

            await _fetch_accessible_from_registry(identity)

            headers = _captured_headers(mock_client.get)
            assert "X-Key-Namespaces" not in headers
