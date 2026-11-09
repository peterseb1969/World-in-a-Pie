"""auth-gateway enforces the platform's per-client rate limit.

Same rule and same WIP_RATE_LIMIT as every WIP service (wip_auth.ratelimit):
a request that came through Caddy carries X-Forwarded-For and is limited per
client address, ahead of the session middleware; a request without it and
the health probe are not limited. /auth/verify is limited like every other
/auth path. The tests swap in a limiter with a tiny budget so the limit is
reached in three requests.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from auth_gateway.main import app
from wip_auth.ratelimit import create_limiter

client = TestClient(app)


@pytest.fixture
def tight_limit(monkeypatch):
    monkeypatch.setenv("WIP_RATE_LIMIT", "2/minute")
    original = app.state.limiter
    app.state.limiter = create_limiter()
    yield
    app.state.limiter = original


def test_verify_past_the_limit_answers_429_with_its_window(tight_limit):
    xff = {"X-Forwarded-For": "203.0.113.91"}
    first = [client.get("/auth/verify", headers=xff).status_code for _ in range(2)]
    refused = client.get("/auth/verify", headers=xff)

    # No session: the budget is spent on 401s, then the limit answers first.
    assert first == [401, 401]
    assert refused.status_code == 429, refused.text
    assert "Retry-After" in refused.headers
    assert refused.headers["X-RateLimit-Limit"] == "2"


def test_the_budget_covers_every_auth_path(tight_limit):
    xff = {"X-Forwarded-For": "203.0.113.92"}
    client.get("/auth/verify", headers=xff)
    client.get("/auth/userinfo", headers=xff)
    assert client.get("/auth/verify", headers=xff).status_code == 429


def test_each_client_address_has_its_own_budget(tight_limit):
    for _ in range(2):
        client.get("/auth/verify", headers={"X-Forwarded-For": "203.0.113.93"})
    assert client.get("/auth/verify", headers={"X-Forwarded-For": "203.0.113.94"}).status_code == 401


def test_requests_without_the_proxy_header_and_the_probe_are_not_limited(tight_limit):
    internal = [client.get("/auth/verify").status_code for _ in range(5)]
    probe = [
        client.get("/health", headers={"X-Forwarded-For": "203.0.113.95"}).status_code
        for _ in range(5)
    ]
    assert 429 not in internal
    assert probe == [200] * 5
