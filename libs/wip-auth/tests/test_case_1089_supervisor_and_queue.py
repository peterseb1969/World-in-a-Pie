"""The event bus: supervision survives a cancelled run; retries stay deduplicated.

Provenance: CASE-1089.
- A consumer run that ended cancelled, without the supervisor being cancelled,
  re-raised CancelledError inside the supervisor and ended it for good: the
  worker stayed down until the process restarted.
- JetStream dedups a retried Nats-Msg-Id only within the stream's duplicate
  window, which was the server default (2 minutes) while queued events may wait
  an hour.
- A publish failure while disconnected counted as a rejection, so a later
  single refusal could drop the event.
- stop() iterated its task list while done-callbacks removed finished tasks,
  skipping (never awaiting) the task after each one.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

pytest.importorskip("nats")

from wip_auth.events import StreamSpec, ensure_stream

from .test_events import FakeJS, FakeServer, _bus, _config, _until


@pytest.fixture
def server(monkeypatch) -> FakeServer:
    import nats

    srv = FakeServer()
    monkeypatch.setattr(nats, "connect", srv.connect)
    return srv

# -- R1: a cancelled run is restarted ------------------------------------------


async def test_a_run_cancelled_from_elsewhere_is_restarted(server: FakeServer) -> None:
    server.up.set()
    bus = _bus()
    await bus.start()
    runs: list[Any] = []
    forever = asyncio.Event()

    async def run(js):
        runs.append(js)
        if len(runs) == 1:
            asyncio.current_task().cancel()  # e.g. a library cancelling its own task
            await asyncio.sleep(1)
        await forever.wait()

    supervisor = bus.supervise("worker", run)
    await _until(lambda: len(runs) == 2, timeout=5)
    assert not supervisor.done()
    consumer = bus.stats()["consumers"]["worker"]
    assert consumer["restarts"] == 1
    assert consumer["last_error"] == "cancelled"
    assert consumer["running"] is True
    await bus.stop()


async def test_cancelling_the_supervisor_still_stops_it(server: FakeServer) -> None:
    server.up.set()
    bus = _bus()
    await bus.start()
    started = asyncio.Event()

    async def run(js):
        started.set()
        await asyncio.Event().wait()

    supervisor = bus.supervise("worker", run)
    await asyncio.wait_for(started.wait(), 2)
    supervisor.cancel()
    with pytest.raises(asyncio.CancelledError):
        await supervisor
    assert bus.stats()["consumers"]["worker"]["restarts"] == 0
    await bus.stop()


# -- R4: the duplicate window covers the queue's max age -----------------------

SPEC = StreamSpec(name="T", subjects=("t.>",), max_age_seconds=7 * 86400)


async def test_a_new_stream_gets_the_wanted_duplicate_window() -> None:
    js = FakeJS()
    await ensure_stream(js, SPEC, duplicate_window_seconds=3600)
    assert js.streams["T"].duplicate_window == 3600


async def test_the_window_is_capped_at_the_streams_max_age() -> None:
    js = FakeJS()
    short = StreamSpec(name="T", subjects=("t.>",), max_age_seconds=600)
    await ensure_stream(js, short, duplicate_window_seconds=3600)
    assert js.streams["T"].duplicate_window == 600


async def test_an_existing_shorter_window_is_raised_never_shortened() -> None:
    js = FakeJS({"T": _config(
        name="T", subjects=["t.>"], max_msgs=-1, max_bytes=-1,
        max_age=SPEC.max_age_seconds, duplicate_window=120,
    )})
    await ensure_stream(js, SPEC, duplicate_window_seconds=3600)
    assert js.streams["T"].duplicate_window == 3600
    await ensure_stream(js, SPEC, duplicate_window_seconds=60)
    assert js.streams["T"].duplicate_window == 3600
    assert len(js.updates) == 1


async def test_the_bus_asks_for_its_queue_age(server: FakeServer) -> None:
    server.up.set()
    bus = _bus(streams=[SPEC], queue_max_age_seconds=1800)
    await bus.start()
    await bus.wait_connected(1)
    await _until(lambda: "T" in server.js.streams)
    assert server.js.streams["T"].duplicate_window == 1800
    await bus.stop()


# -- R5: failures while disconnected are not rejections ------------------------


async def test_disconnected_failures_do_not_count_as_rejections(server: FakeServer) -> None:
    server.up.set()
    bus = _bus(max_rejections=2)
    await bus.start()
    await bus.wait_connected(1)
    nc = server.connects[0]
    real_publish = server.js.publish
    calls: list[int] = []

    async def publish(subject, payload, timeout=None, headers=None):
        calls.append(1)
        if len(calls) <= 2:  # the connection goes during the publish
            await nc.drop()
            raise ConnectionError("connection lost")
        if len(calls) == 3:  # connected, refused once
            raise RuntimeError("stream being repaired")
        return await real_publish(subject, payload, timeout=timeout, headers=headers)

    server.js.publish = publish
    await bus.publish("wip.documents.t.created", b"x")  # call 1: in publish()
    await _until(lambda: not bus.connected)
    await nc.restore()  # call 2: in the drain
    await _until(lambda: len(calls) >= 2 and not bus.connected)
    await nc.restore()  # call 3 refused while connected, call 4 succeeds
    await _until(lambda: bus.stats()["queued"] == 0, timeout=10)
    assert bus.stats()["dropped_by_reason"].get("rejected", 0) == 0
    assert [p[1] for p in server.js.published] == [b"x"]
    await bus.stop()


# -- R6: stop() awaits every task ----------------------------------------------


async def test_stop_awaits_every_task_even_as_finished_ones_leave_the_list(server: FakeServer) -> None:
    server.up.set()
    bus = _bus()
    await bus.start()
    await bus.wait_connected(1)
    unwound: list[int] = []

    async def quick():
        await asyncio.Event().wait()  # ends the moment it is cancelled

    async def slow(i: int):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(0.3)  # unwinding outlasts the rest of stop()
            unwound.append(i)
            raise

    # Tasks that end at once when cancelled, so they leave the list while
    # stop() awaits them, each followed by one that takes a while to unwind.
    for i in range(3):
        bus._spawn(quick())
        bus._spawn(slow(i))
    await asyncio.sleep(0)  # let every task start
    await bus.stop()
    assert sorted(unwound) == [0, 1, 2]


# -- R4 against a real server ----------------------------------------------------


async def test_live_duplicate_window_is_applied_and_dedups() -> None:
    """A real JetStream accepts the window (capped at max_age), raises a
    shorter existing one, and discards a retried Nats-Msg-Id inside it."""
    import uuid

    from .test_events import _live_js

    nc, js = await _live_js()
    tag = uuid.uuid4().hex[:8]
    spec = StreamSpec(name=f"TEST_DUP_{tag}", subjects=(f"d{tag}.>",), max_age_seconds=1800)
    try:
        await js.add_stream(name=spec.name, subjects=[f"d{tag}.>"], max_age=1800)
        assert (await js.stream_info(spec.name)).config.duplicate_window == 120  # server default
        await ensure_stream(js, spec, duplicate_window_seconds=3600)
        assert (await js.stream_info(spec.name)).config.duplicate_window == 1800  # capped
        first = await js.publish(f"d{tag}.x", b"1", headers={"Nats-Msg-Id": "same"})
        again = await js.publish(f"d{tag}.x", b"1", headers={"Nats-Msg-Id": "same"})
        assert again.duplicate and again.seq == first.seq
    finally:
        try:
            await js.delete_stream(spec.name)
        finally:
            await nc.close()
