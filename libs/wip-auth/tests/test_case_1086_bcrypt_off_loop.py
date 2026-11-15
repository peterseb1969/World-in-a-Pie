"""An unknown API key never blocks the event loop (CASE-1086, fix 1).

A value the provider has not seen is checked with bcrypt against every
stored key (about 170 ms each at cost 12). authenticate ran that on the event
loop, so a client sending a fresh random X-API-Key on every request stalled
the whole service for N x 170 ms per request. The slow path now runs in a
worker thread, at most two threads at a time per event loop with a bounded line
behind them; values the caches already know are still answered inline.

bcrypt's check is replaced by a 50 ms sleep: the cost is what matters here,
and real hashes at cost 12 would make the test slow.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor

import bcrypt
import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from wip_auth import APIKeyRecord
from wip_auth.providers import api_key as api_key_module
from wip_auth.providers.api_key import (
    _SLOW_PATH_CONCURRENCY,
    _SLOW_PATH_WAITERS,
    APIKeyProvider,
    hash_api_key,
)

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


async def _loop_ticks(stop: asyncio.Event) -> int:
    """How many times the loop got to run until `stop`."""
    ticks = 0
    while not stop.is_set():
        await asyncio.sleep(0.005)
        ticks += 1
    return ticks


async def _loop_ticks_for(duration: float) -> int:
    """How many times an idle loop runs in `duration` — what a responsive loop
    looks like on this machine, measured while the machine is as it is."""
    ticks = 0
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        await asyncio.sleep(0.005)
        ticks += 1
    return ticks


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
    provider = TrustedHeaderProvider(key_provider)
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
async def test_a_privileged_key_still_asserts_an_identity(slow_checks):
    from wip_auth.providers.trusted_header import TrustedHeaderProvider

    proxy = APIKeyRecord(
        name="gateway", key_hash=hash_api_key("secret-gateway"), owner="system:gateway",
        groups=["wip-services"],
    )
    key_provider = APIKeyProvider([proxy, _key("app")])
    provider = TrustedHeaderProvider(key_provider)

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


# --- The review of the fix (ea219d56) -------------------------------------


@pytest.mark.asyncio
async def test_real_bcrypt_leaves_the_loop(monkeypatch):
    """The tests above stand in time.sleep, which releases the GIL whatever
    happens; this one runs bcrypt's own check, which must release it too, or
    the worker thread would hold the loop anyway.

    A stall is compared with the same runner, not with a fixed number of
    milliseconds: a loaded machine stalls the loop on its own, for reasons this
    test is not about. The control is a window of the same length with nothing
    else running; a check that held the GIL would tick the loop about never."""
    monkeypatch.undo()  # real hashes, at a cost that takes measurable time
    original = bcrypt.gensalt
    monkeypatch.setattr(bcrypt, "gensalt", lambda rounds=11, prefix=b"2b": original(11, prefix))
    provider = APIKeyProvider([_key(f"k{i}") for i in range(2)])

    stop = asyncio.Event()
    watcher = asyncio.create_task(_loop_ticks(stop))
    await asyncio.sleep(0.02)
    started = time.monotonic()
    assert await _attempt(provider, "random") == "401"
    scan = time.monotonic() - started
    stop.set()
    ticks = await watcher

    assert scan >= 0.1, f"a cost-11 scan took only {scan * 1000:.0f} ms: nothing to measure"
    control = await _loop_ticks_for(scan)
    assert ticks >= control / 2, (
        f"the loop ticked {ticks} times in a {scan * 1000:.0f} ms scan, {control} when idle"
    )


@pytest.mark.asyncio
async def test_a_cancelled_check_keeps_its_permit_until_its_thread_ends(monkeypatch):
    """Two at a time bounds threads, not waiters: a caller that stops
    waiting cannot stop its bcrypt, so its permit stays taken until the
    thread finishes, and replacements wait for it."""
    running = 0
    peak = 0
    original = api_key_module.verify_api_key

    def tracked(key, key_hash, salt="wip_auth_salt"):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        time.sleep(0.2)
        running -= 1
        return original(key, key_hash, salt)

    monkeypatch.setattr(api_key_module, "verify_api_key", tracked)
    provider = APIKeyProvider([_key("k0")])
    first = [asyncio.create_task(_attempt(provider, f"gone-{i}")) for i in range(2)]
    await asyncio.sleep(0.05)
    for task in first:
        task.cancel()
    await asyncio.gather(*first, return_exceptions=True)
    # Their threads still run; two more requests must wait for the permits.
    assert await asyncio.gather(*(_attempt(provider, f"next-{i}") for i in range(2))) == ["401"] * 2
    assert peak <= 2, f"{peak} bcrypt threads ran at once"


# --- The queue is bounded too (the review of c8990623) -------------------
#
# Two threads at a time bounds how many checks run, not how many requests wait
# for one. This case's own attack — a fresh random key on every request —
# misses the caches every time, so with an unbounded queue each of them joined
# the line: the backlog grew for as long as arrivals beat the scan rate, and a
# real key's first check waited behind the flood without limit. A request past
# the bound, or waiting longer than the bound, is refused now instead.


@pytest.mark.asyncio
async def test_a_flood_past_the_queue_is_refused_and_costs_no_bcrypt(slow_checks):
    provider = APIKeyProvider([_key(f"k{i}") for i in range(4)])
    started = time.monotonic()
    results = await asyncio.gather(*(_attempt(provider, f"random-{i}") for i in range(20)))
    took = time.monotonic() - started

    room = _SLOW_PATH_CONCURRENCY + _SLOW_PATH_WAITERS
    assert results == ["401"] * room + ["503"] * (20 - room)
    # The refused checks ran no bcrypt: only the ones with room scanned.
    assert len(slow_checks) == room * 4
    # The refused ones answered at once, not after the backlog ahead of them.
    assert took < room * CHECK_SECONDS * 4


@pytest.mark.asyncio
async def test_a_queued_check_that_runs_out_of_time_is_refused(slow_checks, monkeypatch):
    """A waiter is held for the bound and no longer, whatever the scan ahead of
    it costs. The refusal names a window to retry in — a client that backs off
    stops feeding the queue it was refused for."""
    monkeypatch.setattr(api_key_module, "_SLOW_PATH_WAIT_SECONDS", 0.05)
    provider = APIKeyProvider([_key(f"k{i}") for i in range(4)])  # a scan is 200 ms
    blockers = [asyncio.create_task(_attempt(provider, f"gone-{i}")) for i in range(2)]
    await asyncio.sleep(0.01)  # both permits are taken

    started = time.monotonic()
    with pytest.raises(HTTPException) as refused:
        await provider.authenticate(_request("random-late"))
    waited = time.monotonic() - started
    assert refused.value.status_code == 503
    assert refused.value.headers == {"Retry-After": "1"}
    assert waited < 0.15, f"the waiter was held {waited * 1000:.0f} ms against a 50 ms bound"
    assert await asyncio.gather(*blockers) == ["401", "401"]


@pytest.mark.asyncio
async def test_a_refused_check_caches_nothing(slow_checks, monkeypatch):
    """A refused check ran no bcrypt, so it must not be recorded as a wrong
    key: the same value still has to authenticate once there is room again."""
    provider = APIKeyProvider([_key("a")])
    monkeypatch.setattr(api_key_module, "_SLOW_PATH_WAITERS", 0)
    blockers = [asyncio.create_task(_attempt(provider, f"gone-{i}")) for i in range(2)]
    await asyncio.sleep(0.01)

    assert await _attempt(provider, "secret-a") == "503"
    assert await asyncio.gather(*blockers) == ["401", "401"]

    monkeypatch.setattr(api_key_module, "_SLOW_PATH_WAITERS", 8)
    assert await _attempt(provider, "secret-a") == "a"
    # `_rejected` is keyed by the SHA-256 fingerprint of the value, never by
    # the plaintext, so the plaintext test would pass either way.
    assert hashlib.sha256(b"secret-a").hexdigest() not in provider._rejected


# --- Its own threads (the review of c8990623) ----------------------------


@pytest.mark.asyncio
async def test_a_slow_check_does_not_wait_behind_other_blocking_work(slow_checks):
    """The checks get their own threads. On the loop's shared executor, bcrypt
    waits behind unrelated blocking work while holding a place in line."""
    hog = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hog")
    asyncio.get_running_loop().set_default_executor(hog)
    hog.submit(time.sleep, 0.2)  # the shared pool: one running, one queued
    hog.submit(time.sleep, 0.2)

    provider = APIKeyProvider([_key("k0")])  # one key: one 50 ms check
    started = time.monotonic()
    assert await _attempt(provider, "random-0") == "401"
    took = time.monotonic() - started
    assert took < 0.15, f"the check waited {took * 1000:.0f} ms behind the shared pool"


# --- An identity assertion the key check cannot answer is refused ---------
#
# Passing such a request on to the API-key provider was safe while the only
# reason a key could not be verified was the runtime-key deadline: that state
# holds every key, so the next provider answered 503 too. The queue bound is
# judged per call and the caches fill as requests land, so a second pass can
# succeed where the first was refused — and then the request is served as the
# privileged key itself, the asserted identity silently dropped.


def _asserted_identity_request(api_key: str) -> Request:
    """A proxy's request: a user identity, and the privileged key that vouches
    for it."""
    return Request({
        "type": "http", "method": "GET", "path": "/",
        "headers": [
            (b"x-wip-user", b"alice@example.org"),
            (b"x-wip-groups", b"wip-viewers"),
            (b"x-api-key", api_key.encode()),
        ],
    })


def _privileged_key_provider() -> APIKeyProvider:
    proxy = APIKeyRecord(
        name="gateway", key_hash=hash_api_key("secret-gateway"), owner="system:gateway",
        groups=["wip-services"],
    )
    return APIKeyProvider([proxy, _key("app")])


@pytest.mark.asyncio
async def test_an_overloaded_proxy_key_check_is_refused_rather_than_passed_on(
    slow_checks, monkeypatch
):
    from wip_auth.providers.trusted_header import TrustedHeaderProvider

    key_provider = _privileged_key_provider()
    provider = TrustedHeaderProvider(key_provider)

    monkeypatch.setattr(api_key_module, "_SLOW_PATH_WAITERS", 0)
    blockers = [asyncio.create_task(_attempt(key_provider, f"gone-{i}")) for i in range(2)]
    await asyncio.sleep(0.01)  # both threads taken, and the line holds nobody

    with pytest.raises(HTTPException) as refused:
        await provider.authenticate(_asserted_identity_request("secret-gateway"))
    assert refused.value.status_code == 503
    assert refused.value.headers == {"Retry-After": "1"}
    assert await asyncio.gather(*blockers) == ["401", "401"]

    # The refusal is about the queue, not about the key: once there is room
    # again the same request asserts the identity it carried.
    monkeypatch.setattr(api_key_module, "_SLOW_PATH_WAITERS", 8)
    identity = await provider.authenticate(_asserted_identity_request("secret-gateway"))
    assert identity is not None
    assert identity.user_id == "alice@example.org"
    assert identity.groups == ["wip-viewers"]


@pytest.mark.asyncio
async def test_the_chain_never_answers_a_refused_identity_request_as_the_key(
    slow_checks, monkeypatch
):
    """The chain as the Registry builds it: the trusted-header provider first,
    then the API-key provider for the same X-API-Key header.

    The bound is judged per call, so one refusal and room again is what a burst
    looks like: here the slow path declines the proxy-key check and then
    verifies the very next call for the same value. Waiting on real queue state
    to flip between two calls microseconds apart would be a coin toss, so the
    flip is what the seam injects — one refusal, then the normal path.

    Falling through on that refusal authenticates the request as the privileged
    key, with alice and her wip-viewers group dropped."""
    from fastapi import FastAPI

    from wip_auth import create_auth_middleware, get_current_identity
    from wip_auth.providers.trusted_header import TrustedHeaderProvider

    real_run = api_key_module._SlowPath.run
    declined = False

    async def decline_the_first_check(self, check):
        nonlocal declined
        if not declined:
            declined = True
            raise api_key_module._overloaded()
        return await real_run(self, check)

    monkeypatch.setattr(api_key_module._SlowPath, "run", decline_the_first_check)

    key_provider = _privileged_key_provider()
    app = FastAPI()
    app.add_middleware(create_auth_middleware([TrustedHeaderProvider(key_provider), key_provider]))

    @app.get("/whoami")
    async def whoami() -> dict:
        identity = get_current_identity()
        return {"user_id": identity.user_id if identity else None}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
        response = await client.get("/whoami", headers={
            "X-WIP-User": "alice@example.org",
            "X-WIP-Groups": "wip-viewers",
            "X-API-Key": "secret-gateway",
        })

    assert response.status_code == 503, (
        f"answered {response.status_code} as {response.json()}: the identity "
        "assertion was dropped and the request served as the key"
    )
    assert response.headers.get("retry-after") == "1"


@pytest.mark.asyncio
async def test_a_value_the_caches_stop_knowing_is_checked_on_a_thread(monkeypatch):
    """The inline answer is the caches' own, read in one pass. A rejection
    whose time ran out (or a sync that cleared the caches) between a "known"
    check and the answer once sent that value into a bcrypt scan on the
    loop; it now goes to a key-check thread."""
    import threading

    provider = APIKeyProvider(keys=[_key("a"), _key("b")])
    threads: list[str] = []
    original = api_key_module.verify_api_key

    def recording(key, key_hash, salt="wip_auth_salt"):
        threads.append(threading.current_thread().name)
        return original(key, key_hash, salt)

    monkeypatch.setattr(api_key_module, "verify_api_key", recording)
    assert await _attempt(provider, "unknown-value") == "401"  # scanned once, rejected
    assert threads and all(t.startswith("wip-keycheck") for t in threads)

    threads.clear()
    assert await _attempt(provider, "unknown-value") == "401"  # the cached rejection
    assert threads == []

    # The rejection runs out: the value is unknown again, and its check
    # runs on a thread, never on the loop's.
    fingerprint = hashlib.sha256(b"unknown-value").hexdigest()
    provider._rejected[fingerprint] = time.monotonic() - 1
    assert await _attempt(provider, "unknown-value") == "401"
    assert threads and all(t.startswith("wip-keycheck") for t in threads)


@pytest.mark.asyncio
async def test_a_rejection_expiring_between_check_and_answer_runs_no_bcrypt_on_the_loop(monkeypatch):
    import threading

    provider = APIKeyProvider(keys=[_key("a")])
    on_loop: list[str] = []
    original = api_key_module.verify_api_key

    def recording(key, key_hash, salt="wip_auth_salt"):
        if threading.current_thread() is threading.main_thread():
            on_loop.append(key_hash)
        return original(key, key_hash, salt)

    monkeypatch.setattr(api_key_module, "verify_api_key", recording)
    answers = iter([True])  # rejected at the first look, gone at every later one
    monkeypatch.setattr(provider, "_recently_rejected", lambda _fp: next(answers, False))
    assert await _attempt(provider, "flaky-value") == "401"
    assert on_loop == []
