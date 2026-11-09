"""ingest-gateway enforces the platform's per-client rate limit.

Same rule as the five data services (wip_auth.ratelimit): a request that came
through Caddy carries X-Forwarded-For and is limited per client address,
before authentication; a call from inside the platform carries none and is
not limited, nor is the health probe. The tests swap in a limiter with a tiny
budget so the limit is reached in three requests.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from ingest_gateway.main import app
from wip_auth.ratelimit import create_limiter

STATUS = "/api/ingest-gateway/status"
HEALTH = "/api/ingest-gateway/health"


@pytest.fixture
def tight_limit(monkeypatch):
    monkeypatch.setenv("WIP_RATE_LIMIT", "2/minute")
    original = app.state.limiter
    app.state.limiter = create_limiter()
    yield
    app.state.limiter = original


async def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_a_client_past_the_limit_gets_429_before_its_key_is_checked(tight_limit):
    xff = {"X-Forwarded-For": "203.0.113.81"}
    async with await _client() as c:
        first = [(await c.get(STATUS, headers=xff)).status_code for _ in range(2)]
        refused = await c.get(STATUS, headers=xff)

    # No key: the budget is spent on 401s, then the limit answers first.
    assert first == [401, 401]
    assert refused.status_code == 429, refused.text
    assert "Retry-After" in refused.headers
    assert refused.headers["X-RateLimit-Limit"] == "2"


@pytest.mark.asyncio
async def test_each_client_address_has_its_own_budget(tight_limit):
    async with await _client() as c:
        for _ in range(2):
            await c.get(STATUS, headers={"X-Forwarded-For": "203.0.113.82"})
        other = await c.get(STATUS, headers={"X-Forwarded-For": "203.0.113.83"})
    assert other.status_code == 401


@pytest.mark.asyncio
async def test_internal_calls_and_the_probe_are_not_limited(tight_limit):
    async with await _client() as c:
        internal = [(await c.get(STATUS)).status_code for _ in range(5)]
        probe = [
            (await c.get(HEALTH, headers={"X-Forwarded-For": "203.0.113.84"})).status_code
            for _ in range(5)
        ]
    assert 429 not in internal
    assert 429 not in probe
