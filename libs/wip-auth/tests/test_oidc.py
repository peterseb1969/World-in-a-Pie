"""Tests for OIDCProvider and JWKSCache."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import jwt
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from wip_auth import OIDCProvider
from wip_auth.providers.oidc import JWKSCache


def make_request(headers: dict | None = None) -> Request:
    """Create a mock request with given headers."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    return Request(scope)


# ---------------------------------------------------------------------------
# Sample JWKS response data
# ---------------------------------------------------------------------------

SAMPLE_JWKS = {
    "keys": [
        {
            "kid": "key-1",
            "kty": "RSA",
            "alg": "RS256",
            "use": "sig",
            "n": "test-modulus",
            "e": "AQAB",
        },
        {
            "kid": "key-2",
            "kty": "RSA",
            "alg": "RS256",
            "use": "sig",
            "n": "test-modulus-2",
            "e": "AQAB",
        },
    ]
}

SAMPLE_JWKS_ROTATED = {
    "keys": [
        {
            "kid": "key-3",
            "kty": "RSA",
            "alg": "RS256",
            "use": "sig",
            "n": "test-modulus-3",
            "e": "AQAB",
        },
    ]
}


def _mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    """Create a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    return resp


# ===========================================================================
# JWKSCache Tests
# ===========================================================================


class TestJWKSCacheFetchKeys:
    """Test JWKSCache.fetch_keys with mock httpx response."""

    @pytest.mark.asyncio
    async def test_fetch_keys_populates_cache(self):
        """Fetching keys should populate the internal key dict indexed by kid."""
        cache = JWKSCache("https://auth.example.com/.well-known/jwks.json")
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(SAMPLE_JWKS))
        cache._client = mock_client

        await cache.fetch_keys(force=True)

        assert "key-1" in cache._keys
        assert "key-2" in cache._keys
        assert cache._keys["key-1"]["n"] == "test-modulus"
        mock_client.get.assert_awaited_once_with(cache.jwks_url)

    @pytest.mark.asyncio
    async def test_fetch_keys_ignores_keys_without_kid(self):
        """Keys without a kid field should be skipped."""
        jwks = {"keys": [{"kty": "RSA", "n": "no-kid-key"}]}
        cache = JWKSCache("https://auth.example.com/.well-known/jwks.json")
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(jwks))
        cache._client = mock_client

        await cache.fetch_keys(force=True)

        assert len(cache._keys) == 0


class TestJWKSCacheTTL:
    """Test that keys are cached and not re-fetched within TTL."""

    @pytest.mark.asyncio
    async def test_keys_not_refetched_within_ttl(self):
        """Within TTL, fetch_keys should return cached data without calling the endpoint."""
        cache = JWKSCache("https://auth.example.com/.well-known/jwks.json", cache_ttl=3600)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(SAMPLE_JWKS))
        cache._client = mock_client

        # First fetch populates cache
        await cache.fetch_keys(force=True)
        assert mock_client.get.await_count == 1

        # Second fetch within TTL should NOT call the endpoint
        await cache.fetch_keys()
        assert mock_client.get.await_count == 1

    @pytest.mark.asyncio
    async def test_keys_refetched_after_ttl_expiry(self):
        """After TTL expires, fetch_keys should call the endpoint again."""
        cache = JWKSCache("https://auth.example.com/.well-known/jwks.json", cache_ttl=60)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(SAMPLE_JWKS))
        cache._client = mock_client

        # First fetch
        await cache.fetch_keys(force=True)
        assert mock_client.get.await_count == 1

        # Simulate TTL expiry
        cache._last_fetch = time.time() - 120

        # Should re-fetch
        await cache.fetch_keys()
        assert mock_client.get.await_count == 2


class TestJWKSCacheRefreshOnKeyMiss:
    """Test that cache refreshes when a requested key is not found."""

    @pytest.mark.asyncio
    async def test_refresh_on_key_miss(self):
        """get_key should force a refresh if the kid is not in the current cache."""
        cache = JWKSCache("https://auth.example.com/.well-known/jwks.json")
        mock_client = AsyncMock()

        # First call returns initial keys, second call returns rotated keys
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(SAMPLE_JWKS),
                _mock_response(SAMPLE_JWKS_ROTATED),
            ]
        )
        cache._client = mock_client

        # Requesting an unknown key should trigger an initial fetch + forced refresh
        result = await cache.get_key("key-3")

        assert result is not None
        assert result["kid"] == "key-3"
        # First call: normal fetch_keys(); second call: force=True refresh
        assert mock_client.get.await_count == 2

    @pytest.mark.asyncio
    async def test_returns_none_if_key_still_missing_after_refresh(self):
        """get_key should return None if the key is not found even after refresh."""
        cache = JWKSCache("https://auth.example.com/.well-known/jwks.json")
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(SAMPLE_JWKS))
        cache._client = mock_client

        result = await cache.get_key("nonexistent-kid")

        assert result is None


class TestJWKSCacheFallbackOnFailure:
    """Test fallback to stale cache on fetch failure."""

    @pytest.mark.asyncio
    async def test_fallback_to_stale_cache_on_failure(self):
        """If fetch fails but cached keys exist, should keep using stale cache."""
        cache = JWKSCache("https://auth.example.com/.well-known/jwks.json", cache_ttl=60)
        mock_client = AsyncMock()

        # First fetch succeeds
        mock_client.get = AsyncMock(return_value=_mock_response(SAMPLE_JWKS))
        cache._client = mock_client
        await cache.fetch_keys(force=True)
        assert "key-1" in cache._keys

        # Simulate TTL expiry
        cache._last_fetch = time.time() - 120

        # Second fetch fails
        mock_client.get = AsyncMock(
            side_effect=httpx.HTTPError("connection failed")
        )

        # Should not raise, should keep stale keys
        await cache.fetch_keys()
        assert "key-1" in cache._keys

    @pytest.mark.asyncio
    async def test_raises_if_no_cache_and_fetch_fails(self):
        """If no cached keys and fetch fails, should raise RuntimeError."""
        cache = JWKSCache("https://auth.example.com/.well-known/jwks.json")
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=httpx.HTTPError("connection failed")
        )
        cache._client = mock_client

        with pytest.raises(RuntimeError, match="Failed to fetch JWKS"):
            await cache.fetch_keys(force=True)


# ===========================================================================
# OIDCProvider Tests
# ===========================================================================


class TestOIDCProviderInit:
    """Test OIDCProvider initialization."""

    def test_jwks_url_derived_from_issuer(self):
        """JWKS URL should be derived from issuer_url."""
        provider = OIDCProvider(issuer_url="https://auth.example.com")
        assert provider._jwks_url == "https://auth.example.com/.well-known/jwks.json"

    def test_explicit_jwks_url(self):
        """Explicit jwks_url should override issuer-derived URL."""
        provider = OIDCProvider(
            issuer_url="https://auth.example.com",
            jwks_url="https://keys.example.com/jwks",
        )
        assert provider._jwks_url == "https://keys.example.com/jwks"

    def test_requires_either_issuer_or_jwks_url(self):
        """Should raise if neither issuer_url nor jwks_url is provided."""
        with pytest.raises(ValueError, match="Either issuer_url or jwks_url must be provided"):
            OIDCProvider()

    def test_trailing_slash_stripped_from_issuer(self):
        """Trailing slash on issuer_url should be stripped for JWKS derivation."""
        provider = OIDCProvider(issuer_url="https://auth.example.com/")
        assert provider._jwks_url == "https://auth.example.com/.well-known/jwks.json"


class TestOIDCProviderExtractIdentity:
    """Test identity extraction from JWT claims."""

    def test_extract_standard_claims(self):
        """Should extract sub, preferred_username, email, and groups from claims."""
        provider = OIDCProvider(issuer_url="https://auth.example.com")
        claims = {
            "sub": "user-abc-123",
            "preferred_username": "alice",
            "email": "alice@example.com",
            "groups": ["wip-admins", "wip-editors"],
        }

        identity = provider._extract_identity(claims)

        assert identity.user_id == "user-abc-123"
        assert identity.username == "alice"
        assert identity.email == "alice@example.com"
        assert identity.groups == ["wip-admins", "wip-editors"]
        assert identity.auth_method == "jwt"
        assert identity.provider == "https://auth.example.com"

    def test_fallback_username_from_name(self):
        """Username should fall back to 'name' claim if preferred_username absent."""
        provider = OIDCProvider(issuer_url="https://auth.example.com")
        claims = {
            "sub": "user-123",
            "name": "Alice Smith",
        }

        identity = provider._extract_identity(claims)

        assert identity.username == "Alice Smith"

    def test_fallback_username_from_sub(self):
        """Username should fall back to sub if neither preferred_username nor name."""
        provider = OIDCProvider(issuer_url="https://auth.example.com")
        claims = {"sub": "user-123"}

        identity = provider._extract_identity(claims)

        assert identity.username == "user-123"

    def test_groups_from_roles_claim(self):
        """Should fall back to 'roles' claim if groups claim is absent."""
        provider = OIDCProvider(issuer_url="https://auth.example.com")
        claims = {
            "sub": "user-123",
            "roles": ["admin", "editor"],
        }

        identity = provider._extract_identity(claims)

        assert identity.groups == ["admin", "editor"]

    def test_string_group_converted_to_list(self):
        """A single string group should be converted to a list."""
        provider = OIDCProvider(issuer_url="https://auth.example.com")
        claims = {
            "sub": "user-123",
            "groups": "single-group",
        }

        identity = provider._extract_identity(claims)

        assert identity.groups == ["single-group"]

    def test_default_groups_when_none_in_claims(self):
        """Should use default_groups if no groups claim found."""
        provider = OIDCProvider(
            issuer_url="https://auth.example.com",
            default_groups=["wip-viewers"],
        )
        claims = {"sub": "user-123"}

        identity = provider._extract_identity(claims)

        assert identity.groups == ["wip-viewers"]

    def test_custom_groups_claim(self):
        """Should use a custom groups claim name."""
        provider = OIDCProvider(
            issuer_url="https://auth.example.com",
            groups_claim="custom_groups",
        )
        claims = {
            "sub": "user-123",
            "custom_groups": ["team-a"],
        }

        identity = provider._extract_identity(claims)

        assert identity.groups == ["team-a"]

    def test_raw_claims_stored(self):
        """The full claims dict should be stored in raw_claims."""
        provider = OIDCProvider(issuer_url="https://auth.example.com")
        claims = {"sub": "user-123", "custom_field": "custom_value"}

        identity = provider._extract_identity(claims)

        assert identity.raw_claims == claims
        assert identity.raw_claims["custom_field"] == "custom_value"


class TestOIDCProviderTokenExtraction:
    """Test Bearer token extraction from Authorization header."""

    def test_extract_bearer_token(self):
        """Should extract token from 'Bearer <token>' header."""
        provider = OIDCProvider(issuer_url="https://auth.example.com")
        request = make_request({"Authorization": "Bearer my.jwt.token"})

        token = provider._get_token_from_header(request)

        assert token == "my.jwt.token"

    def test_returns_none_without_authorization_header(self):
        """Should return None if no Authorization header."""
        provider = OIDCProvider(issuer_url="https://auth.example.com")
        request = make_request()

        token = provider._get_token_from_header(request)

        assert token is None

    def test_returns_none_for_non_bearer_scheme(self):
        """Should return None for non-Bearer auth schemes."""
        provider = OIDCProvider(issuer_url="https://auth.example.com")
        request = make_request({"Authorization": "Basic dXNlcjpwYXNz"})

        token = provider._get_token_from_header(request)

        assert token is None

    def test_returns_none_for_malformed_header(self):
        """Should return None for malformed Authorization header (too many parts)."""
        provider = OIDCProvider(issuer_url="https://auth.example.com")
        request = make_request({"Authorization": "Bearer token extra"})

        token = provider._get_token_from_header(request)

        assert token is None

    def test_returns_none_for_empty_header(self):
        """Should return None for an empty Authorization header."""
        provider = OIDCProvider(issuer_url="https://auth.example.com")
        request = make_request({"Authorization": ""})

        token = provider._get_token_from_header(request)

        assert token is None

    def test_bearer_case_insensitive(self):
        """Bearer scheme should be matched case-insensitively."""
        provider = OIDCProvider(issuer_url="https://auth.example.com")
        request = make_request({"Authorization": "BEARER my.jwt.token"})

        token = provider._get_token_from_header(request)

        assert token == "my.jwt.token"


class TestOIDCProviderAuthenticate:
    """Test the full authenticate flow with mocked jwt.decode."""

    @pytest.mark.asyncio
    async def test_returns_none_without_authorization_header(self):
        """Should return None (not raise) when there's no Authorization header."""
        provider = OIDCProvider(issuer_url="https://auth.example.com")
        request = make_request()

        result = await provider.authenticate(request)

        assert result is None

    @pytest.mark.asyncio
    async def test_reject_missing_authorization_header_returns_none(self):
        """No Authorization header should return None, allowing other providers."""
        provider = OIDCProvider(issuer_url="https://auth.example.com")
        request = make_request({"X-API-Key": "some-key"})

        result = await provider.authenticate(request)

        assert result is None

    @pytest.mark.asyncio
    @patch("wip_auth.providers.oidc.jwt.decode")
    @patch("wip_auth.providers.oidc.jwt.algorithms.RSAAlgorithm.from_jwk")
    @patch("wip_auth.providers.oidc.jwt.get_unverified_header")
    async def test_successful_authentication(
        self, mock_get_header, mock_from_jwk, mock_decode
    ):
        """Should return UserIdentity on successful token validation."""
        mock_get_header.return_value = {"kid": "key-1", "alg": "RS256"}
        mock_from_jwk.return_value = MagicMock()  # Mock public key
        mock_decode.return_value = {
            "sub": "user-abc",
            "preferred_username": "alice",
            "email": "alice@example.com",
            "groups": ["wip-admins"],
            "iss": "https://auth.example.com",
            "aud": "wip",
        }

        provider = OIDCProvider(issuer_url="https://auth.example.com")
        # Pre-populate JWKS cache to avoid real HTTP call
        provider._jwks_cache._keys = {"key-1": SAMPLE_JWKS["keys"][0]}
        provider._jwks_cache._last_fetch = time.time()

        request = make_request({"Authorization": "Bearer fake.jwt.token"})
        identity = await provider.authenticate(request)

        assert identity is not None
        assert identity.user_id == "user-abc"
        assert identity.username == "alice"
        assert identity.email == "alice@example.com"
        assert identity.groups == ["wip-admins"]
        assert identity.auth_method == "jwt"

    @pytest.mark.asyncio
    @patch("wip_auth.providers.oidc.jwt.decode")
    @patch("wip_auth.providers.oidc.jwt.algorithms.RSAAlgorithm.from_jwk")
    @patch("wip_auth.providers.oidc.jwt.get_unverified_header")
    async def test_reject_expired_token(
        self, mock_get_header, mock_from_jwk, mock_decode
    ):
        """Should raise 401 when token has expired."""
        mock_get_header.return_value = {"kid": "key-1", "alg": "RS256"}
        mock_from_jwk.return_value = MagicMock()
        mock_decode.side_effect = jwt.ExpiredSignatureError("Token expired")

        provider = OIDCProvider(issuer_url="https://auth.example.com")
        provider._jwks_cache._keys = {"key-1": SAMPLE_JWKS["keys"][0]}
        provider._jwks_cache._last_fetch = time.time()

        request = make_request({"Authorization": "Bearer expired.jwt.token"})

        with pytest.raises(HTTPException) as exc_info:
            await provider.authenticate(request)

        assert exc_info.value.status_code == 401
        assert "expired" in exc_info.value.detail.lower()
        assert "WWW-Authenticate" in exc_info.value.headers

    @pytest.mark.asyncio
    @patch("wip_auth.providers.oidc.jwt.decode")
    @patch("wip_auth.providers.oidc.jwt.algorithms.RSAAlgorithm.from_jwk")
    @patch("wip_auth.providers.oidc.jwt.get_unverified_header")
    async def test_reject_invalid_issuer(
        self, mock_get_header, mock_from_jwk, mock_decode
    ):
        """Should raise 401 when token issuer doesn't match."""
        mock_get_header.return_value = {"kid": "key-1", "alg": "RS256"}
        mock_from_jwk.return_value = MagicMock()
        mock_decode.side_effect = jwt.InvalidIssuerError("Invalid issuer")

        provider = OIDCProvider(issuer_url="https://auth.example.com")
        provider._jwks_cache._keys = {"key-1": SAMPLE_JWKS["keys"][0]}
        provider._jwks_cache._last_fetch = time.time()

        request = make_request({"Authorization": "Bearer bad-issuer.jwt.token"})

        with pytest.raises(HTTPException) as exc_info:
            await provider.authenticate(request)

        assert exc_info.value.status_code == 401
        assert "issuer" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    @patch("wip_auth.providers.oidc.jwt.decode")
    @patch("wip_auth.providers.oidc.jwt.algorithms.RSAAlgorithm.from_jwk")
    @patch("wip_auth.providers.oidc.jwt.get_unverified_header")
    async def test_reject_invalid_audience(
        self, mock_get_header, mock_from_jwk, mock_decode
    ):
        """Should raise 401 when token audience doesn't match."""
        mock_get_header.return_value = {"kid": "key-1", "alg": "RS256"}
        mock_from_jwk.return_value = MagicMock()
        mock_decode.side_effect = jwt.InvalidAudienceError("Invalid audience")

        provider = OIDCProvider(issuer_url="https://auth.example.com")
        provider._jwks_cache._keys = {"key-1": SAMPLE_JWKS["keys"][0]}
        provider._jwks_cache._last_fetch = time.time()

        request = make_request({"Authorization": "Bearer bad-aud.jwt.token"})

        with pytest.raises(HTTPException) as exc_info:
            await provider.authenticate(request)

        assert exc_info.value.status_code == 401
        assert "audience" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    @patch("wip_auth.providers.oidc.jwt.get_unverified_header")
    async def test_reject_invalid_token_format(self, mock_get_header):
        """Should raise 401 for a token that can't be parsed."""
        mock_get_header.side_effect = jwt.exceptions.DecodeError("Invalid header")

        provider = OIDCProvider(issuer_url="https://auth.example.com")
        request = make_request({"Authorization": "Bearer not-a-valid-jwt"})

        with pytest.raises(HTTPException) as exc_info:
            await provider.authenticate(request)

        assert exc_info.value.status_code == 401
        assert "Invalid token format" in exc_info.value.detail

    @pytest.mark.asyncio
    @patch("wip_auth.providers.oidc.jwt.get_unverified_header")
    async def test_reject_token_missing_kid(self, mock_get_header):
        """Should raise 401 when token header has no kid."""
        mock_get_header.return_value = {"alg": "RS256"}

        provider = OIDCProvider(issuer_url="https://auth.example.com")
        request = make_request({"Authorization": "Bearer no-kid.jwt.token"})

        with pytest.raises(HTTPException) as exc_info:
            await provider.authenticate(request)

        assert exc_info.value.status_code == 401
        assert "kid" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    @patch("wip_auth.providers.oidc.jwt.get_unverified_header")
    async def test_reject_unknown_signing_key(self, mock_get_header):
        """Should raise 401 when the kid doesn't match any known key."""
        mock_get_header.return_value = {"kid": "unknown-kid", "alg": "RS256"}

        provider = OIDCProvider(issuer_url="https://auth.example.com")
        # Pre-populate cache with different keys
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(SAMPLE_JWKS))
        provider._jwks_cache._client = mock_client

        request = make_request({"Authorization": "Bearer unknown-key.jwt.token"})

        with pytest.raises(HTTPException) as exc_info:
            await provider.authenticate(request)

        assert exc_info.value.status_code == 401
        assert "Unknown signing key" in exc_info.value.detail

    @pytest.mark.asyncio
    @patch("wip_auth.providers.oidc.jwt.decode")
    @patch("wip_auth.providers.oidc.jwt.algorithms.RSAAlgorithm.from_jwk")
    @patch("wip_auth.providers.oidc.jwt.get_unverified_header")
    async def test_reject_generic_jwt_error(
        self, mock_get_header, mock_from_jwk, mock_decode
    ):
        """Should raise 401 for generic PyJWT errors."""
        mock_get_header.return_value = {"kid": "key-1", "alg": "RS256"}
        mock_from_jwk.return_value = MagicMock()
        mock_decode.side_effect = jwt.PyJWTError("Something went wrong")

        provider = OIDCProvider(issuer_url="https://auth.example.com")
        provider._jwks_cache._keys = {"key-1": SAMPLE_JWKS["keys"][0]}
        provider._jwks_cache._last_fetch = time.time()

        request = make_request({"Authorization": "Bearer bad.jwt.token"})

        with pytest.raises(HTTPException) as exc_info:
            await provider.authenticate(request)

        assert exc_info.value.status_code == 401
        assert "Token validation failed" in exc_info.value.detail


class TestOIDCProviderClose:
    """Test resource cleanup."""

    @pytest.mark.asyncio
    async def test_close_cleans_up_jwks_cache(self):
        """Closing the provider should close the JWKS cache client."""
        provider = OIDCProvider(issuer_url="https://auth.example.com")
        # Create a mock client on the cache
        mock_client = AsyncMock()
        provider._jwks_cache._client = mock_client

        await provider.close()

        mock_client.aclose.assert_awaited_once()
