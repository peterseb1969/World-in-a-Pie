"""The MCP server's network transports enforce the per-client rate limit.

Same rule and WIP_RATE_LIMIT as every WIP service (wip_auth.ratelimit): a
request that came through Caddy carries X-Forwarded-For and is limited per
client address, before the API-key check; one without it, and /health, is
not. The limit runs first so a flood of made-up keys costs no Registry key
check: the server caches only keys the Registry accepted, so without it each
unknown key would reach the Registry, which exempts the call (it carries no
X-Forwarded-For) from its own limit.

A real server is spawned per test, as in test_transports.py.
"""

import httpx
import pytest

from tests.test_transports import _free_port, _network_server

INIT = {"jsonrpc": "2.0", "method": "initialize", "id": 1, "params": {}}


@pytest.mark.asyncio
async def test_a_flood_of_unknown_keys_is_shed_before_the_registry_is_asked(monkeypatch):
    monkeypatch.setenv("WIP_RATE_LIMIT", "3/minute")
    port = _free_port()
    seen: list[tuple[str, str]] = []
    url = f"http://127.0.0.1:{port}/mcp"
    async with _network_server("--http", port, api_key="good-key", seen=seen), httpx.AsyncClient() as client:
        statuses = []
        for i in range(5):
            resp = await client.post(url, json=INIT, headers={
                "X-API-Key": f"made-up-{i}", "X-Forwarded-For": "203.0.113.101",
            })
            statuses.append(resp.status_code)
        refused = resp

    assert statuses == [401, 401, 401, 429, 429]
    assert "Retry-After" in refused.headers
    assert refused.headers["X-RateLimit-Limit"] == "3"
    # Only the first three keys were ever checked.
    assert [key for _, key in seen] == ["made-up-0", "made-up-1", "made-up-2"]


@pytest.mark.asyncio
async def test_internal_calls_and_health_are_not_limited(monkeypatch):
    monkeypatch.setenv("WIP_RATE_LIMIT", "2/minute")
    port = _free_port()
    url = f"http://127.0.0.1:{port}/mcp"
    async with _network_server("--http", port, api_key="good-key"), httpx.AsyncClient() as client:
        internal = [
            (await client.post(url, json=INIT, headers={"X-API-Key": "wrong"})).status_code
            for _ in range(4)
        ]
        probe = [
            (await client.get(f"http://127.0.0.1:{port}/health",
                              headers={"X-Forwarded-For": "203.0.113.102"})).status_code
            for _ in range(4)
        ]
    assert internal == [401] * 4
    assert probe == [200] * 4
