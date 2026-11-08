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


# ---------------------------------------------------------------------------
# Entries: names removed, released or moved, entities gone
# ---------------------------------------------------------------------------

from wip_auth.cache_events import (  # noqa: E402
    ENTRIES_CHANGED_SUBJECT,
    announce_entries_changed,
    forget_entries,
)


def test_forgetting_an_entity_drops_every_spelling_cached_for_it():
    resolve.clear_resolution_cache()
    resolve._set_cached("ns:term:Male", "T-1")
    resolve._set_cached("ns:term:M", "T-1")
    resolve._set_cached("ns:term:T-1", "T-1")
    resolve._set_cached("ns:term:Female", "T-2")

    assert forget_entries(["T-1"]) == 3

    assert set(resolve._resolution_cache) == {"ns:term:Female"}
    assert resolve._keys_by_id == {"T-2": {"ns:term:Female"}}


def test_a_key_recached_to_another_entity_leaves_the_old_index():
    resolve.clear_resolution_cache()
    resolve._set_cached("ns:term:Male", "T-old")
    resolve._set_cached("ns:term:Male", "T-new")

    assert forget_entries(["T-old"]) == 0
    assert resolve._get_cached("ns:term:Male") == "T-new"


def test_an_expired_key_leaves_the_index(monkeypatch):
    resolve.clear_resolution_cache()
    resolve._set_cached("ns:term:Male", "T-1")
    later = time.monotonic() + resolve._CACHE_TTL + 1
    monkeypatch.setattr(resolve.time, "monotonic", lambda: later)

    assert resolve._get_cached("ns:term:Male") is None
    assert resolve._keys_by_id == {}


async def test_an_entries_message_drops_their_resolutions():
    resolve.clear_resolution_cache()
    resolve._set_cached("ns:term:Male", "T-1")
    resolve._set_cached("ns:term:Female", "T-2")

    await cache_events._handle(SimpleNamespace(
        subject=ENTRIES_CHANGED_SUBJECT, data=json.dumps({"entry_ids": ["T-1"]}).encode(),
    ))

    assert set(resolve._resolution_cache) == {"ns:term:Female"}


async def test_a_malformed_entries_message_is_ignored():
    resolve.clear_resolution_cache()
    resolve._set_cached("ns:term:Male", "T-1")
    await cache_events._handle(SimpleNamespace(
        subject=ENTRIES_CHANGED_SUBJECT, data=json.dumps({"entry_ids": "T-1"}).encode(),
    ))
    assert "ns:term:Male" in resolve._resolution_cache


class _FakeClient:
    def __init__(self):
        self.sent: list[tuple[str, bytes]] = []

    async def publish(self, subject, payload):
        self.sent.append((subject, payload))

    async def flush(self, timeout=None):
        pass


class _FakeBus:
    configured = True

    def __init__(self):
        self.client = _FakeClient()


async def test_many_ids_are_split_into_messages_of_bounded_size():
    bus = _FakeBus()
    ids = [f"id-{n:05d}" for n in range(2500)]

    assert await announce_entries_changed(bus, ids)

    sent = bus.client.sent
    assert [subject for subject, _ in sent] == [ENTRIES_CHANGED_SUBJECT] * 3
    received = [i for _, payload in sent for i in json.loads(payload)["entry_ids"]]
    assert sorted(received) == ids


async def test_nothing_to_announce_sends_nothing():
    bus = _FakeBus()
    assert await announce_entries_changed(bus, [None, ""])
    assert bus.client.sent == []


async def test_without_nats_entries_are_not_announced():
    assert await announce_entries_changed(EventBus(None, name="test"), ["T-1"]) is False


async def test_live_an_entries_announcement_reaches_a_subscribed_process():
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
        resolve.clear_resolution_cache()
        resolve._set_cached("ns:term:Male", "T-live")
        deadline = time.monotonic() + 5
        while "ns:term:Male" in resolve._resolution_cache and time.monotonic() < deadline:
            assert await announce_entries_changed(sender, ["T-live"])
            await asyncio.sleep(0.1)
        assert "ns:term:Male" not in resolve._resolution_cache
    finally:
        await sender.stop()
        await receiver.stop()
