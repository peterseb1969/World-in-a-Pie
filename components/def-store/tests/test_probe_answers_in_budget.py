"""/ready and /health answer inside the probe budget when MongoDB hangs.

A MongoDB that accepts the connection and never answers held both probes
for as long as the driver waited (server selection alone is 30 s by
default), past the orchestrator's 5 s budget. The ping is bounded now.
"""

import asyncio
import time

import pytest
from httpx import AsyncClient

from def_store import main
from wip_auth import mongo


class _HungAdmin:
    async def command(self, name: str) -> None:
        await asyncio.sleep(60)


class _HungClient:
    admin = _HungAdmin()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/ready", "/api/def-store/health"])
async def test_a_hung_mongodb_fails_the_probe_in_time(client: AsyncClient, monkeypatch, path):
    monkeypatch.setattr(mongo, "PING_TIMEOUT_S", 0.1)
    monkeypatch.setattr(main.app.state, "mongodb_client", _HungClient(), raising=False)
    started = time.monotonic()
    r = await client.get(path)
    assert time.monotonic() - started < 3
    assert r.status_code == 503, r.text
