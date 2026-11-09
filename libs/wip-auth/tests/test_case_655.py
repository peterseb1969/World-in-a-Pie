"""Tests for CASE-655 — config-declared key grants.

A config-file API key may declare `grants`. CASE-655 resolved them locally
in every service; since CASE-1035 only the Registry holds the key file, it
imports the grants as the key's own, and (CASE-1193) every service asks
the Registry for every permission, so a namespace locked for deletion is
closed to these keys too.

Pinned here:

  - APIKeyRecord parses `grants` (config.py's _parse_key_dict path).
  - The api-key provider surfaces grants in raw_claims (the Registry's own
    resolution reads them), and loading a key that declares them warns that
    the service does not read them (CASE-1220).
  - resolve_permission and resolve_accessible_namespaces ask the Registry
    for a key that declares grants, exactly as for one that does not.
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


# Permission resolution: through the Registry (CASE-1035, CASE-1193)
# ────────────────────────────────────────────────────────────────────


class TestGrantsResolveThroughRegistry:
    """A service never answers a permission from a key's declared grants:
    the Registry imported them as the key's grants and is the one that
    resolves them, after checking that the namespace is not locked."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("grants", [{"kb": "write"}, {}])
    async def test_declared_grants_are_asked_of_the_registry(self, grants) -> None:
        identity = _identity(namespaces=["library", "kb"], grants=grants)
        with patch(
            "wip_auth.permissions._fetch_permission_from_registry",
            new_callable=AsyncMock,
            return_value="none",  # e.g. the namespace is locked for deletion
        ) as fetch:
            assert await resolve_permission(identity, "kb") == "none"
            fetch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_accessible_namespaces_are_asked_of_the_registry(self) -> None:
        identity = _identity(namespaces=["library", "kb"], grants={"kb": "write"})
        with patch(
            "wip_auth.permissions._fetch_accessible_from_registry",
            new_callable=AsyncMock,
            return_value=["library"],
        ) as fetch:
            assert await resolve_accessible_namespaces(identity) == ["library"]
            fetch.assert_awaited_once()
