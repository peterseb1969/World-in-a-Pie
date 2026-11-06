"""An unknown API key never blocks the event loop (CASE-1086, fix 1).

A value the provider has not seen is checked with bcrypt against every
stored key (about 170 ms each at cost 12). authenticate ran that on the event
loop, so a client sending a fresh random X-API-Key on every request stalled
the whole service for N x 170 ms per request. The slow path now runs in a
worker thread, at most two at a time per event loop; values the caches
already know are still answered inline.

bcrypt's check is replaced by a 50 ms sleep: the cost is what matters here,
and real hashes at cost 12 would make the test slow.
"""

from __future__ import annotations

import asyncio
import time

import bcrypt
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from wip_auth import APIKeyRecord
from wip_auth.providers import api_key as api_key_module
from wip_auth.providers.api_key import APIKeyProvider, hash_api_key

CHECK_SECONDS = 0.05


@pytest.fixture(autouse=True)
def cheap_hashes(monkeypatch):
    original = bcrypt.gensalt
    monkeypatch.setattr(bcrypt, "gensalt", lambda rounds=4, prefix=b"2b": original(4, prefix))


@pytest.fixture
def slow_checks(monkeypatch):
    """Every bcrypt check takes CHECK_SECONDS, as a real one would."""
    calls: list[str] = []
    original = api_key_module.verify_api_key

    def slow(key, key_hash, salt="wip_auth_salt"):
        calls.append(key_hash)
        time.sleep(CHECK_SECONDS)
        return original(key, key_hash, salt)

    monkeypatch.setattr(api_key_module, "verify_api_key", slow)
    return calls


def _key(name: str) -> APIKeyRecord:
    return APIKeyRecord(name=name, key_hash=hash_api_key(f"secret-{name}"), owner=f"system:{name}")


def _request(api_key: str) -> Request:
    return Request({
        "type": "http", "method": "GET", "path": "/",
        "headers": [(b"x-api-key", api_key.encode())],
    })


async def _max_loop_stall(stop: asyncio.Event) -> float:
    """The longest the loop went without running this coroutine."""
    worst = 0.0
    last = time.monotonic()
    while not stop.is_set():
        await asyncio.sleep(0.005)
        now = time.monotonic()
        worst = max(worst, now - last - 0.005)
        last = now
    return worst


async def _attempt(provider: APIKeyProvider, value: str) -> str:
    try:
        identity = await provider.authenticate(_request(value))
    except HTTPException as exc:
        return str(exc.status_code)
    return identity.username if identity else "none"


@pytest.mark.asyncio
async def test_a_flood_of_unknown_keys_leaves_the_loop_responsive(slow_checks):
    provider = APIKeyProvider([_key(f"k{i}") for i in range(4)])
    stop = asyncio.Event()
    watcher = asyncio.create_task(_max_loop_stall(stop))

    # Each unknown value costs 4 checks x 50 ms = 200 ms of bcrypt.
    results = await asyncio.gather(*(_attempt(provider, f"random-{i}") for i in range(4)))
    stop.set()
    stall = await watcher

    assert results == ["401"] * 4
    assert len(slow_checks) == 16
    # On the loop this would have stalled at least 200 ms per request.
    assert stall < CHECK_SECONDS, f"the loop stalled {stall * 1000:.0f} ms"


@pytest.mark.asyncio
async def test_slow_checks_run_at_most_two_at_a_time(monkeypatch):
    running = 0
    peak = 0
    original = api_key_module.verify_api_key

    def tracked(key, key_hash, salt="wip_auth_salt"):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        time.sleep(CHECK_SECONDS)
        running -= 1
        return original(key, key_hash, salt)

    monkeypatch.setattr(api_key_module, "verify_api_key", tracked)
    provider = APIKeyProvider([_key("k0")])
    await asyncio.gather(*(_attempt(provider, f"random-{i}") for i in range(6)))
    assert peak <= 2


@pytest.mark.asyncio
async def test_known_values_are_still_answered_from_the_caches(slow_checks):
    provider = APIKeyProvider([_key("a"), _key("b")])
    assert await _attempt(provider, "secret-b") == "b"
    assert await _attempt(provider, "nope") == "401"
    slow_checks.clear()

    assert await _attempt(provider, "secret-b") == "b"
    assert await _attempt(provider, "nope") == "401"
    assert slow_checks == []
