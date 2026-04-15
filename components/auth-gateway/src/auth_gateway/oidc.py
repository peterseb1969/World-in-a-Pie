"""OIDC integration with Dex via authlib.

Handles the dual-issuer pattern: the browser sees the external issuer URL
(https://hostname:8443/dex) while token exchange and discovery use the
internal URL (http://wip-dex:5556/dex) to avoid container-to-Caddy TLS
issues (CASE-50).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from authlib.integrations.starlette_client import OAuth
from starlette.requests import Request

from .config import settings

logger = logging.getLogger("auth_gateway.oidc")

# ---------------------------------------------------------------------------
# OIDC metadata discovery — fetch from internal Dex URL
# ---------------------------------------------------------------------------

_oidc_metadata: dict[str, Any] | None = None


async def _discover_metadata() -> dict[str, Any]:
    """Fetch OIDC discovery document from the internal Dex URL.

    The metadata contains endpoint URLs (authorization, token, userinfo, jwks).
    Dex returns these with the EXTERNAL issuer URL, so the browser will be
    redirected to the correct public-facing endpoints via Caddy.
    """
    global _oidc_metadata
    if _oidc_metadata is not None:
        return _oidc_metadata

    discovery_url = f"{settings.oidc_internal_issuer}/.well-known/openid-configuration"
    logger.info("Fetching OIDC metadata from %s", discovery_url)

    async with httpx.AsyncClient(verify=False) as client:
        resp = await client.get(discovery_url)
        resp.raise_for_status()
        _oidc_metadata = resp.json()

    logger.info(
        "OIDC metadata loaded: issuer=%s, authorization_endpoint=%s",
        _oidc_metadata.get("issuer"),
        _oidc_metadata.get("authorization_endpoint"),
    )
    return _oidc_metadata


def _rewrite_to_internal(url: str) -> str:
    """Rewrite a URL from the external issuer to the internal Dex address.

    The OIDC metadata contains external URLs (e.g., https://hostname:8443/dex/token).
    For server-side calls (token exchange), we rewrite to the internal URL
    (http://wip-dex:5556/dex/token) to avoid TLS issues.
    """
    if settings.oidc_issuer == settings.oidc_internal_issuer:
        return url  # No rewrite needed (same URL).
    return url.replace(settings.oidc_issuer, settings.oidc_internal_issuer)


# ---------------------------------------------------------------------------
# Authorization URL
# ---------------------------------------------------------------------------

async def get_auth_url(request: Request) -> str:
    """Build the Dex authorization URL with PKCE."""
    import hashlib
    import secrets
    import base64

    metadata = await _discover_metadata()
    authorization_endpoint = metadata["authorization_endpoint"]

    # PKCE: generate code_verifier and code_challenge
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        )
        .rstrip(b"=")
        .decode()
    )

    # Store PKCE verifier in session for the callback
    request.session["code_verifier"] = code_verifier

    # State parameter for CSRF protection
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state

    params = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": settings.callback_url,
        "scope": settings.oidc_scopes,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }

    from urllib.parse import urlencode
    return f"{authorization_endpoint}?{urlencode(params)}"


# ---------------------------------------------------------------------------
# Code exchange
# ---------------------------------------------------------------------------

async def exchange_code(request: Request) -> dict[str, Any]:
    """Exchange the authorization code for tokens and extract user info.

    Returns a dict with: email, name, sub, groups, refresh_token (if available).
    """
    code = request.query_params.get("code")
    state = request.query_params.get("state")

    if not code:
        raise ValueError("Missing authorization code")

    # Verify state
    expected_state = request.session.pop("oauth_state", None)
    if not expected_state or state != expected_state:
        raise ValueError("Invalid OAuth state parameter")

    code_verifier = request.session.pop("code_verifier", None)
    if not code_verifier:
        raise ValueError("Missing PKCE code verifier")

    metadata = await _discover_metadata()

    # Use the INTERNAL token endpoint for server-side exchange
    token_endpoint = _rewrite_to_internal(metadata["token_endpoint"])

    token_data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.callback_url,
        "client_id": settings.oidc_client_id,
        "client_secret": settings.oidc_client_secret,
        "code_verifier": code_verifier,
    }

    async with httpx.AsyncClient(verify=False) as client:
        resp = await client.post(
            token_endpoint,
            data=token_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        tokens = resp.json()

    # Decode ID token to extract claims (skip signature verification — we just
    # got this from Dex over a trusted connection within the container network).
    import json
    import base64

    id_token = tokens.get("id_token", "")
    if not id_token:
        raise ValueError("No id_token in token response")

    # Decode JWT payload (second segment) without verification
    payload_b64 = id_token.split(".")[1]
    # Add padding
    payload_b64 += "=" * (4 - len(payload_b64) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload_b64))

    user_info = {
        "email": claims.get("email", ""),
        "name": claims.get("name", claims.get("preferred_username", "")),
        "sub": claims.get("sub", ""),
        "groups": claims.get("groups", []),
        "refresh_token": tokens.get("refresh_token"),
    }

    if not user_info["email"]:
        raise ValueError("No email claim in ID token")

    return user_info
