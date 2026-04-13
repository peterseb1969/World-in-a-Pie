"""Tests for TrustedHeaderProvider."""

import pytest
from starlette.requests import Request

from wip_auth import (
    APIKeyRecord,
    AuthConfig,
    TrustedHeaderProvider,
    create_providers_from_config,
    hash_api_key,
)


def make_request(headers: dict | None = None) -> Request:
    """Create a mock request with given headers."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    return Request(scope)


@pytest.fixture
def api_keys():
    return [
        APIKeyRecord(
            name="proxy-key",
            key_hash=hash_api_key("proxy_secret"),
            owner="system:proxy",
            groups=["wip-admins"],
        ),
    ]


@pytest.fixture
def provider(api_keys):
    return TrustedHeaderProvider(keys=api_keys)


class TestTrustedHeaderProvider:
    """Tests for TrustedHeaderProvider."""

    @pytest.mark.asyncio
    async def test_valid_user_and_api_key(self, provider):
        """X-WIP-User + valid X-API-Key → returns UserIdentity."""
        request = make_request({
            "X-WIP-User": "admin@wip.local",
            "X-API-Key": "proxy_secret",
        })

        identity = await provider.authenticate(request)

        assert identity is not None
        assert identity.user_id == "admin@wip.local"
        assert identity.username == "admin"
        assert identity.email == "admin@wip.local"
        assert identity.auth_method == "gateway_oidc"
        assert identity.provider == "trusted_header"

    @pytest.mark.asyncio
    async def test_no_user_header_returns_none(self, provider):
        """No X-WIP-User → returns None (fall through)."""
        request = make_request({"X-API-Key": "proxy_secret"})

        identity = await provider.authenticate(request)

        assert identity is None

    @pytest.mark.asyncio
    async def test_user_without_api_key_returns_none(self, provider):
        """X-WIP-User without X-API-Key → returns None (not authenticated)."""
        request = make_request({"X-WIP-User": "admin@wip.local"})

        identity = await provider.authenticate(request)

        assert identity is None

    @pytest.mark.asyncio
    async def test_user_with_wrong_api_key_returns_none(self, provider):
        """X-WIP-User with invalid X-API-Key → returns None."""
        request = make_request({
            "X-WIP-User": "admin@wip.local",
            "X-API-Key": "wrong_key",
        })

        identity = await provider.authenticate(request)

        assert identity is None

    @pytest.mark.asyncio
    async def test_groups_parsed_from_header(self, provider):
        """X-WIP-Groups should be parsed as comma-separated list."""
        request = make_request({
            "X-WIP-User": "admin@wip.local",
            "X-API-Key": "proxy_secret",
            "X-WIP-Groups": "wip-admins,wip-editors",
        })

        identity = await provider.authenticate(request)

        assert identity is not None
        assert identity.groups == ["wip-admins", "wip-editors"]

    @pytest.mark.asyncio
    async def test_groups_whitespace_handling(self, provider):
        """Groups with whitespace should be trimmed."""
        request = make_request({
            "X-WIP-User": "admin@wip.local",
            "X-API-Key": "proxy_secret",
            "X-WIP-Groups": " wip-admins , wip-editors , ",
        })

        identity = await provider.authenticate(request)

        assert identity is not None
        assert identity.groups == ["wip-admins", "wip-editors"]

    @pytest.mark.asyncio
    async def test_empty_groups_uses_defaults(self, provider):
        """Empty X-WIP-Groups → uses default groups."""
        request = make_request({
            "X-WIP-User": "admin@wip.local",
            "X-API-Key": "proxy_secret",
            "X-WIP-Groups": "",
        })

        identity = await provider.authenticate(request)

        assert identity is not None
        assert identity.groups == ["wip-users"]

    @pytest.mark.asyncio
    async def test_no_groups_header_uses_defaults(self, provider):
        """Missing X-WIP-Groups → uses default groups."""
        request = make_request({
            "X-WIP-User": "admin@wip.local",
            "X-API-Key": "proxy_secret",
        })

        identity = await provider.authenticate(request)

        assert identity is not None
        assert identity.groups == ["wip-users"]

    @pytest.mark.asyncio
    async def test_non_email_user(self, provider):
        """X-WIP-User without @ → username = user_id, no email."""
        request = make_request({
            "X-WIP-User": "service-account",
            "X-API-Key": "proxy_secret",
        })

        identity = await provider.authenticate(request)

        assert identity is not None
        assert identity.user_id == "service-account"
        assert identity.username == "service-account"
        assert identity.email is None

    @pytest.mark.asyncio
    async def test_identity_string(self, provider):
        """Identity string should show email for gateway_oidc auth."""
        request = make_request({
            "X-WIP-User": "admin@wip.local",
            "X-API-Key": "proxy_secret",
        })

        identity = await provider.authenticate(request)

        assert identity.identity_string == "admin@wip.local"


class TestTrustProxyHeadersConfig:
    """Tests for trust_proxy_headers config integration."""

    def test_trust_proxy_headers_false_no_provider(self):
        """trust_proxy_headers=False → no TrustedHeaderProvider registered."""
        config = AuthConfig(
            mode="api_key_only",
            legacy_api_key="test_key",
            trust_proxy_headers=False,
        )
        providers = create_providers_from_config(config)

        provider_types = [type(p).__name__ for p in providers]
        assert "TrustedHeaderProvider" not in provider_types

    def test_trust_proxy_headers_true_registers_provider(self):
        """trust_proxy_headers=True → TrustedHeaderProvider prepended."""
        config = AuthConfig(
            mode="api_key_only",
            legacy_api_key="test_key",
            trust_proxy_headers=True,
        )
        providers = create_providers_from_config(config)

        provider_types = [type(p).__name__ for p in providers]
        assert provider_types[0] == "TrustedHeaderProvider"
        assert provider_types[1] == "APIKeyProvider"

    def test_trust_proxy_headers_dual_mode(self):
        """trust_proxy_headers=True in dual mode → provider first."""
        config = AuthConfig(
            mode="dual",
            legacy_api_key="test_key",
            trust_proxy_headers=True,
        )
        providers = create_providers_from_config(config)

        provider_types = [type(p).__name__ for p in providers]
        assert provider_types[0] == "TrustedHeaderProvider"

    def test_trust_proxy_headers_none_mode_ignored(self):
        """trust_proxy_headers in none mode → no effect."""
        config = AuthConfig(
            mode="none",
            trust_proxy_headers=True,
        )
        providers = create_providers_from_config(config)

        provider_types = [type(p).__name__ for p in providers]
        assert "TrustedHeaderProvider" not in provider_types
