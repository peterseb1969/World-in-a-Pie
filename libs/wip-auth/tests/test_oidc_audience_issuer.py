"""Real-signature tests for the audience, issuer and key-endpoint contract.

An OIDC provider such as Dex sets `aud` to the client that requested the
token and `iss` to its public URL, while services fetch the signing keys
over an internal URL. These tests sign genuine RS256 tokens, so they exercise
PyJWT's own audience and issuer checks rather than a mocked decode.
"""

import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from starlette.requests import Request

from wip_auth import OIDCProvider
from wip_auth.config import AuthConfig

PUBLIC_ISSUER = "https://wip.example.com/dex"
INTERNAL_KEYS = "http://wip-dex:5556/dex/keys"
KID = "test-key"

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_JWK = {
    **jwt.algorithms.RSAAlgorithm.to_jwk(_PRIVATE_KEY.public_key(), as_dict=True),
    "kid": KID,
    "alg": "RS256",
    "use": "sig",
}


def _token(aud: str, iss: str = PUBLIC_ISSUER) -> str:
    now = int(time.time())
    claims = {
        "iss": iss, "aud": aud, "sub": "u-1", "email": "e2e@wip.local",
        "groups": ["wip-admins"], "iat": now, "exp": now + 300,
    }
    return jwt.encode(claims, _PRIVATE_KEY, algorithm="RS256", headers={"kid": KID})


def _request(token: str) -> Request:
    return Request({
        "type": "http", "method": "GET", "path": "/",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
    })


def _provider(audience: str | list[str]) -> OIDCProvider:
    provider = OIDCProvider(
        issuer_url=PUBLIC_ISSUER, jwks_url=INTERNAL_KEYS, audience=audience,
    )
    provider._jwks_cache._keys = {KID: _JWK}
    provider._jwks_cache._last_fetch = time.time()
    return provider


class TestAudienceList:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("aud", ["wip-gateway", "dnd"])
    async def test_any_listed_client_is_accepted(self, aud: str) -> None:
        identity = await _provider(["wip-gateway", "dnd"]).authenticate(_request(_token(aud)))
        assert identity is not None
        assert identity.email == "e2e@wip.local"
        assert identity.auth_method == "jwt"

    @pytest.mark.asyncio
    async def test_unlisted_client_is_refused(self) -> None:
        with pytest.raises(HTTPException) as exc:
            await _provider(["wip-gateway", "dnd"]).authenticate(_request(_token("intruder")))
        assert exc.value.status_code == 401
        assert "audience" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_single_string_audience_still_works(self) -> None:
        identity = await _provider("wip-gateway").authenticate(_request(_token("wip-gateway")))
        assert identity is not None


class TestIssuerAndKeys:
    def test_explicit_key_url_wins_over_issuer_derivation(self) -> None:
        assert _provider("wip-gateway")._jwks_url == INTERNAL_KEYS

    @pytest.mark.asyncio
    async def test_issuer_is_compared_against_the_public_url(self) -> None:
        internal_iss = _token("wip-gateway", iss="http://wip-dex:5556/dex")
        with pytest.raises(HTTPException) as exc:
            await _provider("wip-gateway").authenticate(_request(internal_iss))
        assert exc.value.status_code == 401
        assert "issuer" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_unreachable_keys_refuse_with_503(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = OIDCProvider(issuer_url=PUBLIC_ISSUER, jwks_url=INTERNAL_KEYS, audience="wip-gateway")

        async def fail(*_args: object, **_kwargs: object) -> None:
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(httpx.AsyncClient, "get", fail)
        with pytest.raises(HTTPException) as exc:
            await provider.authenticate(_request(_token("wip-gateway")))
        assert exc.value.status_code == 503


class TestAudienceSetting:
    def test_comma_separated_setting_parses_to_a_list(self) -> None:
        config = AuthConfig(jwt_audience="wip-gateway, dnd ,")
        assert config.jwt_audiences == ["wip-gateway", "dnd"]

    def test_empty_setting_is_refused(self) -> None:
        with pytest.raises(ValueError, match="WIP_AUTH_JWT_AUDIENCE is empty"):
            _ = AuthConfig(jwt_audience=" , ").jwt_audiences
