"""The Registry's cache-invalidation message: a deleted namespace clears what
every subscribed process cached, and without NATS nothing changes (the
caches expire on their TTL)."""

from __future__ import annotations

import asyncio
import json
import os
import time
from types import SimpleNamespace

import pytest

from wip_auth import cache_events, permissions, resolve
from wip_auth.cache_events import (
    NAMESPACE_DELETED_SUBJECT,
    announce_namespace_deleted,
    flush_namespace,
    on_namespace_deleted,
    subscribe_cache_invalidation,
)
from wip_auth.events import WIP_EVENTS_STREAM, EventBus


@pytest.fixture
def flushed():
    """A registered flusher recording the prefixes it was called with."""
    seen: list[str] = []
    on_namespace_deleted(seen.append)
    yield seen
    cache_events._namespace_flushers.remove(seen.append)


def _fill_caches() -> None:
    resolve._set_cached("other:terminology:other:TL", "id-in-a-deleted-namespace")
    permissions._grant_cache["k"] = "grant"
    permissions._accessible_cache["k"] = "namespaces"


def test_flush_clears_the_shared_caches_and_calls_registered_flushers(flushed):
    _fill_caches()

    flush_namespace("other")

    assert resolve._resolution_cache == {}
    assert len(permissions._grant_cache) == 0 and len(permissions._accessible_cache) == 0
    assert flushed == ["other"]


def test_a_failing_flusher_does_not_keep_the_others_stale(flushed):
    def broken(_prefix: str) -> None:
        raise RuntimeError("boom")

    cache_events._namespace_flushers.insert(0, broken)
    try:
        _fill_caches()
        flush_namespace("other")
    finally:
        cache_events._namespace_flushers.remove(broken)
    assert flushed == ["other"]
    assert resolve._resolution_cache == {}


def test_registering_twice_flushes_once(flushed):
    on_namespace_deleted(flushed.append)
    flush_namespace("ns")
    assert flushed == ["ns"]


async def test_a_message_flushes(flushed):
    _fill_caches()
    await cache_events._handle(SimpleNamespace(
        subject=NAMESPACE_DELETED_SUBJECT, data=json.dumps({"namespace": "gone"}).encode(),
    ))
    assert flushed == ["gone"] and resolve._resolution_cache == {}


async def test_a_malformed_message_is_ignored(flushed):
    await cache_events._handle(SimpleNamespace(subject=NAMESPACE_DELETED_SUBJECT, data=b"nope"))
    assert flushed == []


async def test_without_nats_nothing_is_sent_or_received():
    bus = EventBus(None, name="test")
    assert subscribe_cache_invalidation(bus) is None
    assert await announce_namespace_deleted(bus, "ns") is False


async def test_a_configured_but_unconnected_bus_announces_nothing():
    bus = EventBus("nats://127.0.0.1:1", name="test")  # never started
    assert await announce_namespace_deleted(bus, "ns") is False


def test_the_subject_is_stored_by_no_stream():
    # Plain publish/subscribe: a stream capturing it would store messages
    # nobody replays and make the Registry need stream rights.
    prefixes = [s.removesuffix(">") for s in WIP_EVENTS_STREAM.subjects]
    assert not any(NAMESPACE_DELETED_SUBJECT.startswith(p) for p in prefixes)


NATS_TEST_URL = os.environ.get("NATS_TEST_URL", "nats://localhost:4223")


async def test_live_an_announcement_reaches_a_subscribed_process(flushed):
    """Against a real server: one bus announces, another, subscribed, flushes."""
    nats = pytest.importorskip("nats")
    try:
        probe = await asyncio.wait_for(
            nats.connect(NATS_TEST_URL, connect_timeout=1, max_reconnect_attempts=0,
                         allow_reconnect=False),
            timeout=5,
        )
        await probe.close()
    except Exception:
        pytest.skip(f"no NATS at {NATS_TEST_URL}")

    receiver = EventBus(NATS_TEST_URL, name="test-receiver")
    sender = EventBus(NATS_TEST_URL, name="test-sender")
    await receiver.start()
    await sender.start()
    try:
        assert await receiver.wait_connected(timeout=5)
        assert await sender.wait_connected(timeout=5)
        subscribe_cache_invalidation(receiver)
        _fill_caches()
        # The subscription is made in the background; announce until it lands.
        deadline = time.monotonic() + 5
        while not flushed and time.monotonic() < deadline:
            assert await announce_namespace_deleted(sender, "live-ns")
            await asyncio.sleep(0.1)
        assert flushed and set(flushed) == {"live-ns"}
        assert resolve._resolution_cache == {}
    finally:
        await sender.stop()
        await receiver.stop()
