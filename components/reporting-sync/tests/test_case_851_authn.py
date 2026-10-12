"""reporting-sync authenticates every route (CASE-851, Phase 2b C3).

The walker gate (test_anonymous_refused.py) covers the no-credential
axis. These cover the rest of authentication: a wrong key is refused by
the middleware, the install's admin key gets through, and the probes stay
reachable without a key.
"""

import os

import pytest
from httpx import ASGITransport, AsyncClient

from reporting_sync.main import app


def _client(headers: dict[str, str] | None = None) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
        headers=headers or {},
    )


@pytest.mark.asyncio
async def test_wrong_key_is_refused():
    async with _client({"X-API-Key": "not-a-real-key"}) as client:
        resp = await client.get("/api/reporting-sync/status")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_key_gets_through():
    admin = {"X-API-Key": os.environ["WIP_AUTH_LEGACY_API_KEY"]}
    async with _client(admin) as client:
        resp = await client.get("/api/reporting-sync/status")
    assert resp.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/health", "/api/reporting-sync/health"])
async def test_health_probes_need_no_key(path):
    async with _client() as client:
        resp = await client.get(path)
    assert resp.status_code == 200
    assert resp.json()["service"]


@pytest.mark.asyncio
async def test_prefixed_health_ignores_a_stale_key():
    """A monitor still configured with a rotated key must not turn the
    ingress health probe into a 401."""
    async with _client({"X-API-Key": "rotated-away"}) as client:
        resp = await client.get("/api/reporting-sync/health")
    assert resp.status_code == 200
