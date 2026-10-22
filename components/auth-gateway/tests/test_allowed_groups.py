"""Per-route allowed groups on /auth/verify (CASE-901).

The deployer puts a route's `allowed_groups` into the verify URL it renders
(Caddy forward_auth `uri`, NGINX `auth-url`). A logged-in user outside them
gets 403 — never 401, which would loop a signed-in user through login again.
Without the parameter, any logged-in user passes, as before.
"""

from __future__ import annotations

import asyncio
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

import pytest
from fastapi import Request

from auth_gateway.main import verify


def _verify(query: str = "", groups: list[str] | None = None, accept: str = "*/*", session: bool = True):
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/auth/verify",
        "headers": [(b"accept", accept.encode())],
        "query_string": query.encode(),
        "session": {
            "email": "viewer@wip.local",
            "groups": groups if groups is not None else ["wip-viewers"],
            "exp": int(time.time()) + 3600,
        } if session else {},
    }
    return asyncio.run(verify(Request(scope)))  # type: ignore[arg-type]


def test_no_parameter_lets_any_logged_in_user_pass() -> None:
    assert _verify().status_code == 200


@pytest.mark.parametrize("query", ["allowed_groups=wip-viewers", "allowed_groups=wip-admins,wip-viewers"])
def test_a_member_of_an_allowed_group_passes(query) -> None:
    response = _verify(query)
    assert response.status_code == 200
    assert response.headers["X-WIP-User"] == "viewer@wip.local"


def test_a_logged_in_user_outside_the_groups_gets_403_json() -> None:
    response = _verify("allowed_groups=wip-admins", accept="application/json")
    assert response.status_code == 403
    assert b"forbidden" in response.body
    assert "X-API-Key" not in response.headers


def test_a_browser_gets_a_readable_403_page() -> None:
    response = _verify("allowed_groups=wip-admins", accept="text/html,application/xhtml+xml")
    assert response.status_code == 403
    assert b"have access to this app" in response.body


def test_a_user_with_no_groups_is_refused() -> None:
    assert _verify("allowed_groups=wip-admins", groups=[]).status_code == 403


def test_no_session_is_still_401_whatever_the_groups() -> None:
    assert _verify("allowed_groups=wip-admins", session=False).status_code == 401
