"""A key change reaches every service at once, not at its next poll.

Services learned runtime keys only by polling the Registry every 30 s, so a
key the Registry had just created was refused elsewhere as "Invalid API key"
for up to 30 s, and a revoked one kept working as long. The Registry now
announces key and grant changes over NATS (ACCESS_CHANGED_SUBJECT): a
subscribed process drops its cached grants and, when keys changed, syncs
them at once. Without NATS the poll stays the bound, and the refusal of an
unknown key says that a new key may not have arrived yet.
"""

from __future__ import annotations

import asyncio
import os
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from wip_auth import APIKeyRecord, KeySyncService, cache_events, permissions
from wip_auth.cache_events import (
    ACCESS_CHANGED_SUBJECT,
    announce_access_changed,
    on_keys_changed,
    subscribe_cache_invalidation,
)
from wip_auth.events import WIP_EVENTS_STREAM, EventBus
from wip_auth.providers.api_key import APIKeyProvider, hash_api_key


@pytest.fixture
def resynced():
    calls: list[int] = []

    def resync() -> None:
        calls.append(1)

    on_keys_changed(resync)
    yield calls
    cache_events.off_keys_changed(resync)


def _msg(body: bytes) -> SimpleNamespace:
    return SimpleNamespace(subject=ACCESS_CHANGED_SUBJECT, data=body)


async def test_a_key_change_drops_cached_grants_and_resyncs(resynced):
    permissions._grant_cache["k"] = "write"
    await cache_events._handle(_msg(b'{"keys": true}'))
    assert resynced == [1]
    assert "k" not in permissions._grant_cache


async def test_a_grant_change_drops_cached_grants_only(resynced):
    permissions._grant_cache["k"] = "write"
    await cache_events._handle(_msg(b'{"keys": false}'))
    assert resynced == []
    assert "k" not in permissions._grant_cache


async def test_a_malformed_access_message_is_ignored(resynced):
    await cache_events._handle(_msg(b"nope"))
    assert resynced == []


def test_the_subject_is_stored_by_no_stream():
    prefixes = [s.removesuffix(">") for s in WIP_EVENTS_STREAM.subjects]
    assert not any(ACCESS_CHANGED_SUBJECT.startswith(p) for p in prefixes)


async def test_without_nats_nothing_is_announced():
    assert await announce_access_changed(EventBus(None, name="t"), keys=True) is False


# ── KeySyncService ────────────────────────────────────────────────────────


def _service() -> KeySyncService:
    return KeySyncService("http://registry", "k", APIKeyProvider([]), interval_seconds=3600)


async def test_announcements_during_a_sync_fold_into_one_more():
    service = _service()
    started, release = asyncio.Event(), asyncio.Event()
    calls: list[int] = []

    async def slow_sync() -> None:
        calls.append(1)
        started.set()
        await release.wait()

    service._sync_once = slow_sync  # type: ignore[method-assign]
    first = asyncio.create_task(service.sync_now())
    await started.wait()
    # Three announcements while the first sync runs: one more sync, not three.
    for _ in range(3):
        await service.sync_now()
    release.set()
    await first
    assert len(calls) == 2


async def test_a_started_service_syncs_when_keys_change_and_stops_listening():
    service = _service()
    synced = asyncio.Event()
    calls: list[int] = []

    async def sync() -> None:
        calls.append(1)
        synced.set()

    service._sync_once = sync  # type: ignore[method-assign]
    await service.start()
    try:
        await asyncio.wait_for(synced.wait(), 1)  # the initial sync
        synced.clear()
        cache_events.access_changed(keys=True)
        await asyncio.wait_for(synced.wait(), 1)
        assert len(calls) == 2
    finally:
        await service.stop()
    cache_events.access_changed(keys=True)
    await asyncio.sleep(0.05)
    assert len(calls) == 2


# ── the refusal of an unknown key ─────────────────────────────────────────


def test_a_synced_copy_says_a_new_key_may_not_have_arrived():
    provider = APIKeyProvider([APIKeyRecord(name="c", key_hash=hash_api_key("c"))])
    record, message = provider._validate_key("not-a-known-key")
    assert record is None and message == "Invalid API key"

    provider.confirm_runtime_keys(90)  # what a key sync does
    record, message = provider._validate_key("another-unknown-key")
    assert record is None
    assert message.startswith("Invalid API key") and "may not have reached this service yet" in message


# ── live, against the test NATS ───────────────────────────────────────────

NATS_TEST_URL = os.environ.get("NATS_TEST_URL", "nats://localhost:4223")


async def test_live_an_announced_key_change_triggers_a_sync():
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

    service = _service()
    service._sync_once = MagicMock(side_effect=lambda: asyncio.sleep(0))  # type: ignore[method-assign]
    receiver, sender = EventBus(NATS_TEST_URL, name="t-recv"), EventBus(NATS_TEST_URL, name="t-send")
    await receiver.start()
    await sender.start()
    await service.start()
    try:
        assert await receiver.wait_connected(timeout=5) and await sender.wait_connected(timeout=5)
        subscribe_cache_invalidation(receiver)
        await asyncio.sleep(0.05)
        initial = service._sync_once.call_count
        deadline = time.monotonic() + 5
        while service._sync_once.call_count == initial and time.monotonic() < deadline:
            assert await announce_access_changed(sender, keys=True)
            await asyncio.sleep(0.1)
        assert service._sync_once.call_count > initial
    finally:
        await service.stop()
        await sender.stop()
        await receiver.stop()
