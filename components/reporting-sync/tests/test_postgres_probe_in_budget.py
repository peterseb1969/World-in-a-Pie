"""The PostgreSQL probe answers within its second, acquisition included.

/health, /metrics, /alerts/test and the alert loop each acquired a pool
connection and ran SELECT 1 with no bound: a pool with no free connection,
or a server that never answered, held the probe. They share one bounded
probe now.
"""

import asyncio
import time
from contextlib import asynccontextmanager

import pytest

from reporting_sync import main


class _ExhaustedPool:
    @asynccontextmanager
    async def acquire(self):
        await asyncio.sleep(60)
        yield None


class _HungServerPool:
    class _Conn:
        async def fetchval(self, sql: str):
            await asyncio.sleep(60)

    @asynccontextmanager
    async def acquire(self):
        yield self._Conn()


@pytest.mark.asyncio
@pytest.mark.parametrize("pool", [_ExhaustedPool(), _HungServerPool()], ids=["no-connection", "no-answer"])
async def test_the_probe_fails_within_a_second(monkeypatch, pool):
    monkeypatch.setattr(main.state, "postgres_pool", pool)
    started = time.monotonic()
    assert await main._postgres_live_check() is False
    assert time.monotonic() - started < 2


@pytest.mark.asyncio
async def test_no_pool_is_not_connected(monkeypatch):
    monkeypatch.setattr(main.state, "postgres_pool", None)
    assert await main._postgres_live_check() is False
