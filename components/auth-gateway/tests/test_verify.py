"""Tests for /auth/verify.

The verify endpoint is called by both Caddy's forward_auth (compose)
and nginx-ingress's auth-url (k8s). Both targets require 401 (not 3xx)
on unauthenticated requests:

  - nginx auth-request treats any non-2xx/401/403 as "unexpected" → 500.
  - Caddy's handle_response @401 directive is how the compose renderer
    converts 401 into the browser redirect to /auth/login.

A 302 direct from /auth/verify breaks nginx. A 401 works for both.
"""

from __future__ import annotations

import os
import time

os.environ.setdefault("OIDC_ISSUER", "https://wip.local/dex")
os.environ.setdefault("OIDC_INTERNAL_ISSUER", "http://wip-dex:5556/dex")
os.environ.setdefault("OIDC_CLIENT_ID", "wip-gateway")
os.environ.setdefault("OIDC_CLIENT_SECRET", "test-secret")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("WIP_HOSTNAME", "wip.local")
os.environ.setdefault("CALLBACK_URL", "https://wip.local/auth/callback")

from fastapi.testclient import TestClient

from auth_gateway.main import app

client = TestClient(app)


def test_verify_unauthenticated_returns_401() -> None:
    """Unauthenticated request MUST return 401 (not 302/3xx).

    nginx auth-request treats anything other than 2xx/401/403 as
    unexpected and returns 500 to the client. 401 is the portable
    answer that both nginx and Caddy can translate into a login flow.
    """
    r = client.get("/auth/verify", follow_redirects=False)
    assert r.status_code == 401


def test_verify_unauthenticated_includes_redirect_hint() -> None:
    """The 401 response carries X-Auth-Redirect so debugging a broken
    renderer doesn't require guessing where /auth/login lives."""
    r = client.get(
        "/auth/verify",
        headers={
            "X-Forwarded-Uri": "/apps/rc/dashboard",
            "X-Forwarded-Host": "wip.local",
            "X-Forwarded-Proto": "https",
        },
        follow_redirects=False,
    )
    assert r.status_code == 401
    redirect = r.headers.get("X-Auth-Redirect", "")
    assert redirect.startswith("/auth/login?return_to=")
    assert "wip.local" in redirect
    # The original URI should survive encoding round-trip.
    assert "apps/rc/dashboard" in redirect


def test_verify_authenticated_returns_200_with_identity_headers() -> None:
    """Happy path: session present, not expired → 200 with identity."""
    # Populate the session by hitting an endpoint that writes to it.
    # Simplest path: construct a request with a pre-populated session
    # by monkey-patching SessionMiddleware state — since the middleware
    # is cookie-based we instead drive it through the public API.
    #
    # We don't have a public endpoint that sets session without OIDC,
    # so this test walks through the production flow's state manually:
    # the /auth/callback endpoint populates the session. For unit-level
    # coverage we verify the verify-200 path via direct session seed.
    from starlette.testclient import TestClient as _TC  # noqa: F401

    # We'll inject the session via a subclass of SessionMiddleware's
    # internals. Easier: call verify() directly in-process.
    from fastapi import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/auth/verify",
        "headers": [],
        "query_string": b"",
        "session": {
            "email": "admin@wip.local",
            "groups": ["wip-admins"],
            "exp": int(time.time()) + 3600,
        },
    }
    request = Request(scope)  # type: ignore[arg-type]

    from auth_gateway.main import verify
    import asyncio

    response = asyncio.run(verify(request))
    assert response.status_code == 200
    assert response.headers["X-WIP-User"] == "admin@wip.local"
    assert response.headers["X-WIP-Groups"] == "wip-admins"
    assert response.headers["X-API-Key"] == "test-api-key"
