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


# --- The other entry points: trusted headers and identify_key ------------
#
# authenticate was one of three callers of the bcrypt slow path. A request
# carrying X-WIP-User goes to TrustedHeaderProvider first (the Registry turns
# it on), whose proxy-key check called the synchronous lookup; the ingest
# gateway identifies a producer's key from a NATS header through
# identify_key. Both held the event loop for N x ~170 ms on an unknown value.


def _identity_request(api_key: str) -> Request:
    return Request({
        "type": "http", "method": "GET", "path": "/",
        "headers": [(b"x-wip-user", b"someone@example.org"), (b"x-api-key", api_key.encode())],
    })


@pytest.mark.asyncio
async def test_a_flood_with_identity_headers_leaves_the_loop_responsive(slow_checks):
    from wip_auth.providers.trusted_header import TrustedHeaderProvider

    keys = [_key(f"k{i}") for i in range(4)]
    key_provider = APIKeyProvider(keys)
    provider = TrustedHeaderProvider(keys, key_provider=key_provider)
    stop = asyncio.Event()
    watcher = asyncio.create_task(_max_loop_stall(stop))

    results = await asyncio.gather(
        *(provider.authenticate(_identity_request(f"random-{i}")) for i in range(4))
    )
    stop.set()
    stall = await watcher

    assert results == [None] * 4
    assert len(slow_checks) == 16
    assert stall < CHECK_SECONDS, f"the loop stalled {stall * 1000:.0f} ms"


@pytest.mark.asyncio
async def test_the_key_list_walk_runs_off_the_loop_too(slow_checks, monkeypatch):
    from wip_auth.providers import trusted_header
    from wip_auth.providers.trusted_header import TrustedHeaderProvider

    # The walk calls its own import of verify_api_key: slow that one too.
    monkeypatch.setattr(trusted_header, "verify_api_key", api_key_module.verify_api_key)

    provider = TrustedHeaderProvider([_key(f"k{i}") for i in range(4)])
    stop = asyncio.Event()
    watcher = asyncio.create_task(_max_loop_stall(stop))
    await asyncio.sleep(0.02)  # the watcher is measuring before the check starts
    assert await provider.authenticate(_identity_request("random")) is None
    stop.set()
    assert len(slow_checks) == 4
    assert await watcher < CHECK_SECONDS


@pytest.mark.asyncio
async def test_a_privileged_key_still_asserts_an_identity(slow_checks):
    from wip_auth.providers.trusted_header import TrustedHeaderProvider

    proxy = APIKeyRecord(
        name="gateway", key_hash=hash_api_key("secret-gateway"), owner="system:gateway",
        groups=["wip-services"],
    )
    key_provider = APIKeyProvider([proxy, _key("app")])
    provider = TrustedHeaderProvider([proxy], key_provider=key_provider)

    identity = await provider.authenticate(_identity_request("secret-gateway"))
    assert identity is not None and identity.user_id == "someone@example.org"
    assert await provider.authenticate(_identity_request("secret-app")) is None


@pytest.mark.asyncio
async def test_identify_key_async_leaves_the_loop_responsive(slow_checks):
    provider = APIKeyProvider([_key(f"k{i}") for i in range(4)])
    stop = asyncio.Event()
    watcher = asyncio.create_task(_max_loop_stall(stop))

    results = await asyncio.gather(*(provider.identify_key_async(f"random-{i}") for i in range(4)))
    known = await provider.identify_key_async("secret-k2")
    stop.set()
    stall = await watcher

    assert results == [None] * 4
    assert known is not None and known.user_id == "apikey:k2"
    assert stall < CHECK_SECONDS, f"the loop stalled {stall * 1000:.0f} ms"
