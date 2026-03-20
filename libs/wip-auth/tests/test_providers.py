"""Tests for authentication providers."""

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.testclient import TestClient

from wip_auth import (
    APIKeyProvider,
    APIKeyRecord,
    NoAuthProvider,
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


class TestNoAuthProvider:
    """Tests for NoAuthProvider."""

    @pytest.mark.asyncio
    async def test_returns_anonymous_identity(self):
        """Should return anonymous identity for any request."""
        provider = NoAuthProvider()
        request = make_request()

        identity = await provider.authenticate(request)

        assert identity is not None
        assert identity.user_id == "anonymous"
        assert identity.username == "anonymous"
        assert identity.auth_method == "none"

    @pytest.mark.asyncio
    async def test_default_groups(self):
        """Should use default groups."""
        provider = NoAuthProvider(default_groups=["custom-group"])
        request = make_request()

        identity = await provider.authenticate(request)

        assert "custom-group" in identity.groups


class TestHashApiKey:
    """Tests for hash_api_key function."""

    def test_bcrypt_hashing(self):
        """hash_api_key should produce bcrypt hashes."""
        h = hash_api_key("my_secret_key")
        assert h.startswith("$2b$")

    def test_verify_correct_key(self):
        """verify_api_key should accept the correct key."""
        from wip_auth.providers.api_key import verify_api_key
        h = hash_api_key("my_secret_key")
        assert verify_api_key("my_secret_key", h) is True

    def test_verify_wrong_key(self):
        """verify_api_key should reject the wrong key."""
        from wip_auth.providers.api_key import verify_api_key
        h = hash_api_key("my_secret_key")
        assert verify_api_key("wrong_key", h) is False

    def test_different_keys_different_hashes(self):
        """Different keys should produce different hashes."""
        from wip_auth.providers.api_key import verify_api_key
        h = hash_api_key("key1")
        assert verify_api_key("key2", h) is False

    def test_salt_affects_hash(self):
        """Different salts should produce different hashes."""
        from wip_auth.providers.api_key import verify_api_key
        h = hash_api_key("key", salt="salt1")
        assert verify_api_key("key", h, salt="salt1") is True
        assert verify_api_key("key", h, salt="salt2") is False

    def test_legacy_sha256_fallback(self):
        """Should verify legacy SHA-256 hashes."""
        import hashlib
        from wip_auth.providers.api_key import verify_api_key
        legacy_hash = hashlib.sha256("wip_auth_salt:test_key".encode()).hexdigest()
        assert verify_api_key("test_key", legacy_hash) is True
        assert verify_api_key("wrong_key", legacy_hash) is False


class TestAPIKeyProvider:
    """Tests for APIKeyProvider."""

    @pytest.fixture
    def provider(self):
        """Create provider with test keys."""
        keys = [
            APIKeyRecord(
                name="test-key",
                key_hash=hash_api_key("secret123"),
                owner="test-user",
                groups=["wip-admins"],
            ),
            APIKeyRecord(
                name="service-key",
                key_hash=hash_api_key("service456"),
                owner="service",
                groups=["wip-services"],
            ),
        ]
        return APIKeyProvider(keys)

    @pytest.mark.asyncio
    async def test_no_header_returns_none(self, provider):
        """Should return None if no API key header."""
        request = make_request()
        identity = await provider.authenticate(request)
        assert identity is None

    @pytest.mark.asyncio
    async def test_valid_key_returns_identity(self, provider):
        """Should return identity for valid key."""
        request = make_request({"X-API-Key": "secret123"})

        identity = await provider.authenticate(request)

        assert identity is not None
        assert identity.username == "test-key"
        assert identity.auth_method == "api_key"
        assert "wip-admins" in identity.groups

    @pytest.mark.asyncio
    async def test_invalid_key_raises_401(self, provider):
        """Should raise 401 for invalid key."""
        request = make_request({"X-API-Key": "wrong_key"})

        with pytest.raises(HTTPException) as exc_info:
            await provider.authenticate(request)

        assert exc_info.value.status_code == 401
        assert "Invalid API key" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_custom_header_name(self):
        """Should use custom header name."""
        keys = [
            APIKeyRecord(name="key", key_hash=hash_api_key("secret"))
        ]
        provider = APIKeyProvider(keys, header_name="X-Custom-Key")

        # Wrong header name
        request1 = make_request({"X-API-Key": "secret"})
        assert await provider.authenticate(request1) is None

        # Correct header name
        request2 = make_request({"X-Custom-Key": "secret"})
        identity = await provider.authenticate(request2)
        assert identity is not None

    @pytest.mark.asyncio
    async def test_disabled_key_rejected(self):
        """Disabled keys should not authenticate."""
        keys = [
            APIKeyRecord(
                name="disabled",
                key_hash=hash_api_key("secret"),
                enabled=False,
            )
        ]
        provider = APIKeyProvider(keys)
        request = make_request({"X-API-Key": "secret"})

        with pytest.raises(HTTPException) as exc_info:
            await provider.authenticate(request)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_identity_string(self, provider):
        """Identity string should use apikey: prefix."""
        request = make_request({"X-API-Key": "secret123"})
        identity = await provider.authenticate(request)
        assert identity.identity_string == "apikey:test-key"

    def test_add_key(self, provider):
        """Should be able to add keys at runtime."""
        new_key = APIKeyRecord(
            name="new-key",
            key_hash=hash_api_key("new_secret"),
        )
        provider.add_key(new_key)

        # Should have 3 keys now
        assert len(provider._keys) == 3

    def test_remove_key(self, provider):
        """Should be able to remove keys by hash."""
        # Get the hash of the first key to remove
        key_hash = provider._keys[0].key_hash
        removed = provider.remove_key(key_hash)
        assert removed is True
        assert len(provider._keys) == 1

    def test_namespace_access_check(self, provider):
        """Should check namespace access for API key identities."""
        from wip_auth import UserIdentity

        # Identity with no namespace restrictions
        identity1 = UserIdentity(
            user_id="apikey:test",
            username="test",
            auth_method="api_key",
            raw_claims={"namespaces": None},
        )
        assert provider.check_namespace_access(identity1, "any-namespace") is True

        # Identity with namespace restrictions
        identity2 = UserIdentity(
            user_id="apikey:test",
            username="test",
            auth_method="api_key",
            raw_claims={"namespaces": ["allowed-ns"]},
        )
        assert provider.check_namespace_access(identity2, "allowed-ns") is True
        assert provider.check_namespace_access(identity2, "other-ns") is False

        # JWT identity (no namespace concept)
        identity3 = UserIdentity(
            user_id="user:123",
            username="user",
            auth_method="jwt",
        )
        assert provider.check_namespace_access(identity3, "any-namespace") is True
