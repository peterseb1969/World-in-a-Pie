"""Contract tests for the shared self-healing NATS connection (wip_auth.events).

Most tests drive EventBus against a fake nats client, so they run without a
NATS server. The stream-convergence test at the bottom runs against a real
server when one is reachable at NATS_TEST_URL (CI's test-nats), using a
uniquely named stream so it never touches WIP_EVENTS.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

nats = pytest.importorskip("nats")

from nats.js.errors import NotFoundError  # noqa: E402

from wip_auth.events import (  # noqa: E402
    WIP_EVENTS_STREAM,
    ConnectionState,
    EventBus,
    PublishOutcome,
    StreamSpec,
    ensure_stream,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeJS:
    def __init__(self, streams: dict[str, Any] | None = None) -> None:
        self.published: list[tuple[str, bytes, str]] = []
        self.fail: Any = None  # callable(subject) -> Exception | None
        self.streams = streams if streams is not None else {}
        self.updates: list[Any] = []

    async def publish(self, subject, payload, timeout=None, headers=None):
        if self.fail is not None:
            exc = self.fail(subject)
            if exc is not None:
                raise exc
        self.published.append((subject, payload, headers["Nats-Msg-Id"]))
        return SimpleNamespace(seq=len(self.published))

    async def stream_info(self, name):
        if name not in self.streams:
            raise NotFoundError
        return SimpleNamespace(config=self.streams[name])

    async def add_stream(self, config):
        self.streams[config.name] = config

    async def update_stream(self, config):
        self.updates.append(config)
        self.streams[config.name] = config


class FakeNC:
    def __init__(self, js: FakeJS, callbacks: dict[str, Any]) -> None:
        self._js = js
        self.cb = callbacks
        self.is_connected = True
        self.is_closed = False

    def jetstream(self):
        return self._js

    async def drop(self) -> None:
        self.is_connected = False
        await self.cb["disconnected_cb"]()

    async def restore(self) -> None:
        self.is_connected = True
        await self.cb["reconnected_cb"]()

    async def close(self) -> None:
        self.is_connected = False
        self.is_closed = True
        await self.cb["closed_cb"]()


class FakeServer:
    """Replaces nats.connect; connects only while `up` is set."""

    def __init__(self, js: FakeJS | None = None) -> None:
        self.js = js or FakeJS()
        self.up = asyncio.Event()
        self.connects: list[FakeNC] = []
        self.options: list[dict[str, Any]] = []

    async def connect(self, url, **kwargs):
        self.options.append(kwargs)
        await self.up.wait()
        nc = FakeNC(self.js, kwargs)
        self.connects.append(nc)
        return nc


@pytest.fixture
def server(monkeypatch) -> FakeServer:
    srv = FakeServer()
    monkeypatch.setattr(nats, "connect", srv.connect)
    return srv


async def _until(pred, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not pred():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


def _bus(**kw) -> EventBus:
    kw.setdefault("streams", [WIP_EVENTS_STREAM])
    kw.setdefault("reconnect_wait_seconds", 0.01)
    return EventBus("nats://fake:4222", name="test", **kw)


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


async def test_unconfigured_bus_is_disabled_not_failed() -> None:
    bus = EventBus(None, name="test")
    await bus.start()
    assert bus.state is ConnectionState.NOT_CONFIGURED
    assert await bus.publish("wip.documents.x.created", b"{}") is PublishOutcome.DISABLED
    assert bus.stats()["queued"] == 0
    await bus.stop()


async def test_start_never_blocks_on_a_down_nats(server: FakeServer) -> None:
    bus = _bus()
    await asyncio.wait_for(bus.start(), timeout=0.5)
    assert bus.state is ConnectionState.CONNECTING
    assert not bus.connected
    await bus.stop()


async def test_reconnect_is_unlimited(server: FakeServer) -> None:
    bus = _bus()
    await bus.start()
    server.up.set()
    await bus.wait_connected(1)
    assert server.options[0]["max_reconnect_attempts"] == -1
    await bus.stop()


async def test_no_latch_a_late_nats_is_picked_up(server: FakeServer) -> None:
    """A failed first connect must not disable publishing for good."""
    bus = _bus()
    await bus.start()
    assert await bus.publish("wip.documents.t.created", b"1") is PublishOutcome.QUEUED
    server.up.set()
    assert await bus.wait_connected(1)
    await _until(lambda: bus.stats()["queued"] == 0)
    assert await bus.publish("wip.documents.t.created", b"2") is PublishOutcome.PUBLISHED
    assert [p[1] for p in server.js.published] == [b"1", b"2"]
    assert "WIP_EVENTS" in server.js.streams
    await bus.stop()


async def test_a_closed_client_is_rebuilt(server: FakeServer) -> None:
    server.up.set()
    bus = _bus()
    await bus.start()
    await bus.wait_connected(1)
    await server.connects[0].close()  # nats-py gave up / fatal error
    await _until(lambda: len(server.connects) == 2 and bus.connected)
    assert await bus.publish("wip.documents.t.created", b"x") is PublishOutcome.PUBLISHED
    await bus.stop()


async def test_disconnect_reports_reconnecting_and_streams_reensured(server: FakeServer) -> None:
    server.up.set()
    bus = _bus()
    await bus.start()
    await bus.wait_connected(1)
    nc = server.connects[0]
    await nc.drop()
    assert bus.state is ConnectionState.RECONNECTING
    assert bus.stats()["state"] == "reconnecting"
    server.js.streams.clear()  # e.g. NATS came back without its storage
    await nc.restore()
    await _until(lambda: "WIP_EVENTS" in server.js.streams and bus.connected)
    await bus.stop()


# ---------------------------------------------------------------------------
# Publishing and the retry queue
# ---------------------------------------------------------------------------


async def test_queue_drains_in_order_after_reconnect(server: FakeServer) -> None:
    server.up.set()
    bus = _bus()
    await bus.start()
    await bus.wait_connected(1)
    nc = server.connects[0]
    await nc.drop()
    outcomes = [await bus.publish("wip.documents.t.updated", str(i).encode()) for i in range(5)]
    assert set(outcomes) == {PublishOutcome.QUEUED}
    assert bus.stats()["queued"] == 5
    await nc.restore()
    await _until(lambda: bus.stats()["queued"] == 0)
    assert [p[1] for p in server.js.published] == [str(i).encode() for i in range(5)]
    assert bus.stats()["published"] == 5
    await bus.stop()


async def test_new_events_do_not_overtake_queued_ones(server: FakeServer) -> None:
    server.up.set()
    bus = _bus()
    await bus.start()
    await bus.wait_connected(1)
    gate = {"fail": True}
    server.js.fail = lambda s: TimeoutError() if gate["fail"] else None
    assert await bus.publish("wip.documents.t.created", b"first") is PublishOutcome.QUEUED
    gate["fail"] = False
    # Connected and publishable, but something is queued: join the queue.
    assert await bus.publish("wip.documents.t.updated", b"second") is PublishOutcome.QUEUED
    await _until(lambda: bus.stats()["queued"] == 0)
    assert [p[1] for p in server.js.published] == [b"first", b"second"]
    await bus.stop()


async def test_retry_keeps_the_same_msg_id(server: FakeServer) -> None:
    """JetStream dedups a retried event only if its Nats-Msg-Id is unchanged."""
    server.up.set()
    bus = _bus()
    await bus.start()
    await bus.wait_connected(1)
    attempts: list[str] = []
    real_publish = server.js.publish

    async def flaky(subject, payload, timeout=None, headers=None):
        attempts.append(headers["Nats-Msg-Id"])
        if len(attempts) == 1:
            raise TimeoutError
        return await real_publish(subject, payload, timeout=timeout, headers=headers)

    server.js.publish = flaky
    await bus.publish("wip.documents.t.created", b"x")
    await _until(lambda: bus.stats()["queued"] == 0)
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    await bus.stop()


async def test_overflow_drops_oldest_and_counts(server: FakeServer) -> None:
    bus = _bus(queue_max_events=2)
    await bus.start()  # never connects
    for i in range(3):
        await bus.publish("wip.documents.t.updated", str(i).encode())
    stats = bus.stats()
    assert stats["queued"] == 2
    assert stats["dropped_by_reason"]["overflow"] == 1
    server.up.set()
    await _until(lambda: bus.stats()["queued"] == 0)
    assert [p[1] for p in server.js.published] == [b"1", b"2"]
    await bus.stop()


async def test_expired_events_are_dropped_and_counted(server: FakeServer) -> None:
    bus = _bus(queue_max_age_seconds=0.05)
    await bus.start()
    await bus.publish("wip.documents.t.updated", b"old")
    await asyncio.sleep(0.1)
    await bus.publish("wip.documents.t.updated", b"new")
    stats = bus.stats()
    assert stats["queued"] == 1
    assert stats["dropped_by_reason"]["expired"] == 1
    await bus.stop()


async def test_a_refused_event_cannot_block_the_queue(server: FakeServer) -> None:
    server.up.set()
    bus = _bus(max_rejections=3)
    await bus.start()
    await bus.wait_connected(1)
    server.js.fail = lambda s: RuntimeError("no stream") if s.startswith("wip.unrouted") else None
    await bus.publish("wip.unrouted.x", b"poison")
    await bus.publish("wip.documents.t.created", b"ok")
    await _until(lambda: bus.stats()["queued"] == 0, timeout=10)
    assert bus.stats()["dropped_by_reason"]["rejected"] == 1
    assert [p[1] for p in server.js.published] == [b"ok"]
    await bus.stop()


async def test_stop_counts_what_is_still_queued(server: FakeServer) -> None:
    bus = _bus()
    await bus.start()
    await bus.publish("wip.documents.t.updated", b"x")
    await bus.stop()
    assert bus.stats()["dropped_by_reason"]["stopped"] == 1
    assert bus.state is ConnectionState.STOPPED


# ---------------------------------------------------------------------------
# Consumer supervision
# ---------------------------------------------------------------------------


async def test_supervised_consumer_waits_for_connection_and_restarts(server: FakeServer) -> None:
    bus = _bus()
    await bus.start()
    runs: list[Any] = []
    forever = asyncio.Event()

    async def run(js):
        runs.append(js)
        if len(runs) == 1:
            raise RuntimeError("stream not found")
        await forever.wait()

    bus.supervise("worker", run)
    await asyncio.sleep(0.05)
    assert runs == []  # no connection, no run
    server.up.set()
    await _until(lambda: len(runs) == 2, timeout=5)
    consumer = bus.stats()["consumers"]["worker"]
    assert consumer["running"] is True
    assert consumer["restarts"] == 1
    assert "stream not found" in consumer["last_error"]
    await bus.stop()


async def test_consumer_bound_to_a_closed_client_is_restarted(server: FakeServer) -> None:
    """A subscription dies with its client; a consumer that swallows fetch
    errors would otherwise spin on it forever."""
    server.up.set()
    bus = _bus()
    await bus.start()
    runs: list[Any] = []

    async def run(js):
        runs.append(js)
        while True:  # like the sync workers: never raises on its own
            await asyncio.sleep(0.01)

    bus.supervise("worker", run)
    await _until(lambda: len(runs) == 1)
    await server.connects[0].close()
    await _until(lambda: len(runs) == 2 and len(server.connects) == 2, timeout=5)
    consumer = bus.stats()["consumers"]["worker"]
    assert consumer["restarts"] == 1
    assert consumer["last_error"] == "client closed"
    await bus.stop()


# ---------------------------------------------------------------------------
# Stream convergence
# ---------------------------------------------------------------------------


def _config(**kw):
    from nats.js.api import StreamConfig
    return StreamConfig(**kw)


async def test_partial_stream_converges_to_the_canonical_definition() -> None:
    """Whoever created WIP_EVENTS first, every service ends with the full one."""
    js = FakeJS({"WIP_EVENTS": _config(
        name="WIP_EVENTS",
        subjects=["wip.documents.>", "wip.templates.>", "wip.terminologies.>",
                  "wip.terms.>", "wip.term_relations.>", "wip.custom.>"],
        max_msgs=1_000_000, max_bytes=1024**3, max_age=0,
    )})
    await ensure_stream(js, WIP_EVENTS_STREAM)
    cfg = js.streams["WIP_EVENTS"]
    assert set(WIP_EVENTS_STREAM.subjects) <= set(cfg.subjects)
    assert "wip.custom.>" in cfg.subjects  # never removes what others added
    assert cfg.max_age == WIP_EVENTS_STREAM.max_age_seconds
    # Converged: a second ensure is a no-op.
    await ensure_stream(js, WIP_EVENTS_STREAM)
    assert len(js.updates) == 1


def test_canonical_stream_includes_file_events() -> None:
    assert "wip.files.>" in WIP_EVENTS_STREAM.subjects


NATS_TEST_URL = os.environ.get("NATS_TEST_URL", "nats://localhost:4223")


async def _live_js():
    try:
        nc = await nats.connect(NATS_TEST_URL, connect_timeout=1, max_reconnect_attempts=0,
                                allow_reconnect=False)
    except Exception:
        pytest.skip(f"no NATS at {NATS_TEST_URL}")
    return nc, nc.jetstream()


async def test_live_partial_stream_converges() -> None:
    """Against a real server: a stream created with a partial subject list
    converges to the full list and declared limits."""
    nc, js = await _live_js()
    tag = uuid.uuid4().hex[:8]
    spec = StreamSpec(
        name=f"TEST_EVENTS_{tag}",
        subjects=(f"t{tag}.documents.>", f"t{tag}.files.>"),
        max_msgs=1000, max_bytes=1024 * 1024, max_age_seconds=3600,
    )
    try:
        await js.add_stream(name=spec.name, subjects=[f"t{tag}.documents.>"])
        await ensure_stream(js, spec)
        info = await js.stream_info(spec.name)
        assert sorted(info.config.subjects) == sorted(spec.subjects)
        assert info.config.max_age == 3600
        assert info.config.max_msgs == 1000
        await js.publish(f"t{tag}.files.f1.uploaded", b"{}")
    finally:
        try:
            await js.delete_stream(spec.name)
        finally:
            await nc.close()
