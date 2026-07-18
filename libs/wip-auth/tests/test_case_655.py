"""Tests for CASE-655 — config-declared grants resolve locally.

A config-file API key that declares `grants` opts into fully local
permission resolution: namespaces give read, grants give their declared
level, and neither the Registry nor MongoDB is consulted. This is what
makes spec-declared keys survive a Mongo wipe/restore intact — key,
read scope, and write grants all come from the config file.

Pinned here:

  - APIKeyRecord parses `grants` (config.py's _parse_key_dict path).
  - The api-key provider surfaces grants in raw_claims and
    check_namespace_access accepts grant-only namespaces.
  - resolve_permission answers from config grants with NO Registry
    call; namespaces-only → read; undeclared → none.
  - resolve_accessible_namespaces returns the union locally.
  - Keys WITHOUT grants keep today's Registry-resolution behaviour.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from wip_auth.config import AuthConfig
from wip_auth.models import UserIdentity
from wip_auth.permissions import (
    clear_permission_cache,
    resolve_accessible_namespaces,
    resolve_permission,
)
from wip_auth.providers.api_key import APIKeyProvider, hash_api_key


@pytest.fixture(autouse=True)
def _clear_caches():
    clear_permission_cache()
    yield
    clear_permission_cache()


def _identity(
    *,
    namespaces: list[str] | None,
    grants: dict[str, str] | None,
    groups: list[str] | None = None,
) -> UserIdentity:
    """The identity the api-key provider produces for a config key."""
    return UserIdentity(
        user_id="apikey:web-yac",
        username="web-yac",
        auth_method="api_key",
        groups=groups or [],
        raw_claims={
            "key_name": "web-yac",
            "owner": "system:web-yac",
            "namespaces": namespaces,
            "grants": grants,
        },
    )


# ────────────────────────────────────────────────────────────────────
# Config parsing
# ────────────────────────────────────────────────────────────────────


class TestConfigParsing:
    def test_grants_parse_from_key_dict(self) -> None:
        cfg = AuthConfig()
        record = cfg._parse_key_dict(
            {
                "name": "web-yac",
                "key": "kR7mX2pQ9vL4nB8wZ3cF6a",
                "namespaces": ["library", "kb"],
                "grants": {"kb": "write"},
            },
            hash_api_key,
        )
        assert record.grants == {"kb": "write"}
        assert record.namespaces == ["library", "kb"]

    def test_grants_default_none(self) -> None:
        cfg = AuthConfig()
        record = cfg._parse_key_dict(
            {"name": "plain", "key": "kR7mX2pQ9vL4nB8wZ3cF6a"},
            hash_api_key,
        )
        assert record.grants is None

    def test_invalid_grant_level_rejected(self) -> None:
        from pydantic import ValidationError

        cfg = AuthConfig()
        with pytest.raises(ValidationError):
            cfg._parse_key_dict(
                {
                    "name": "bad",
                    "key": "kR7mX2pQ9vL4nB8wZ3cF6a",
                    "grants": {"kb": "superuser"},
                },
                hash_api_key,
            )


# ────────────────────────────────────────────────────────────────────
# Provider: raw_claims + namespace access
# ────────────────────────────────────────────────────────────────────


_PLAINTEXT = "kR7mX2pQ9vL4nB8wZ3cF6a"


def _stub_request(key: str):
    """The minimal request surface authenticate() touches."""
    from types import SimpleNamespace

    return SimpleNamespace(
        headers={"X-API-Key": key},
        method="GET",
        url=SimpleNamespace(path="/api/test"),
    )


class TestProviderIdentity:
    def _provider_with_key(self, **key_kwargs) -> APIKeyProvider:
        cfg = AuthConfig()
        record = cfg._parse_key_dict(
            {"name": "web-yac", "key": _PLAINTEXT, **key_kwargs},
            hash_api_key,
        )
        return APIKeyProvider(keys=[record], hash_salt=cfg.api_key_hash_salt)

    @pytest.mark.asyncio
    async def test_identity_carries_grants(self) -> None:
        provider = self._provider_with_key(
            namespaces=["library", "kb"], grants={"kb": "write"}
        )
        identity = await provider.authenticate(_stub_request(_PLAINTEXT))
        assert identity is not None
        assert identity.raw_claims["grants"] == {"kb": "write"}

    @pytest.mark.asyncio
    async def test_namespace_access_includes_grant_only_namespace(self) -> None:
        provider = self._provider_with_key(
            namespaces=["library"], grants={"kb": "write"}
        )
        identity = await provider.authenticate(_stub_request(_PLAINTEXT))
        assert identity is not None
        assert provider.check_namespace_access(identity, "library")
        assert provider.check_namespace_access(identity, "kb")
        assert not provider.check_namespace_access(identity, "other")


# ────────────────────────────────────────────────────────────────────
# Local permission resolution
# ────────────────────────────────────────────────────────────────────


class TestLocalResolution:
    @pytest.mark.asyncio
    async def test_granted_namespace_resolves_locally(self) -> None:
        identity = _identity(
            namespaces=["library", "kb"], grants={"kb": "write"}
        )
        with patch(
            "wip_auth.permissions._fetch_permission_from_registry",
            new_callable=AsyncMock,
        ) as fetch:
            assert await resolve_permission(identity, "kb") == "write"
            assert await resolve_permission(identity, "library") == "read"
            assert await resolve_permission(identity, "other") == "none"
            fetch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_accessible_namespaces_resolve_locally(self) -> None:
        identity = _identity(
            namespaces=["library", "kb"], grants={"kb": "write"}
        )
        with patch(
            "wip_auth.permissions._fetch_accessible_from_registry",
            new_callable=AsyncMock,
        ) as fetch:
            assert await resolve_accessible_namespaces(identity) == ["kb", "library"]
            fetch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_grant_outside_namespaces_still_accessible(self) -> None:
        """Runtime is permissive for hand-written configs: a grant on a
        namespace missing from `namespaces` still resolves (the deployer
        validates grants ⊆ namespaces at spec level)."""
        identity = _identity(namespaces=["library"], grants={"kb": "write"})
        with patch(
            "wip_auth.permissions._fetch_permission_from_registry",
            new_callable=AsyncMock,
        ) as fetch:
            assert await resolve_permission(identity, "kb") == "write"
            fetch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_key_without_grants_hits_registry_as_before(self) -> None:
        identity = _identity(namespaces=["library"], grants=None)
        with patch(
            "wip_auth.permissions._fetch_permission_from_registry",
            new_callable=AsyncMock,
            return_value="read",
        ) as fetch:
            assert await resolve_permission(identity, "library") == "read"
            fetch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_oidc_identity_unaffected(self) -> None:
        oidc = UserIdentity(
            user_id="user@wip.local",
            username="user",
            auth_method="jwt",
            groups=[],
            raw_claims={"grants": {"kb": "write"}},  # hostile/odd claims ignored
        )
        with patch(
            "wip_auth.permissions._fetch_permission_from_registry",
            new_callable=AsyncMock,
            return_value="none",
        ) as fetch:
            assert await resolve_permission(oidc, "kb") == "none"
            fetch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_grants_dict_is_still_local(self) -> None:
        """grants={} declares 'no write anywhere' — local resolution with
        read-only scope, not a fall-through to the Registry."""
        identity = _identity(namespaces=["library"], grants={})
        with patch(
            "wip_auth.permissions._fetch_permission_from_registry",
            new_callable=AsyncMock,
        ) as fetch:
            assert await resolve_permission(identity, "library") == "read"
            assert await resolve_permission(identity, "kb") == "none"
            fetch.assert_not_awaited()
