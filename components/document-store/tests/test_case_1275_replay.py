"""Replay streams are freed when nothing can address them any more.

Provenance: CASE-1275 (8632edde R1, and the R4 low).

Cancel was the only path that deleted a replay stream. A session that completed
or failed kept its stream until its message age expired 24 hours later, and a
restart lost the session state entirely — so the streams of an earlier run
belonged to nothing anyone could list, drain or cancel, and piled up by name.
Streams are now deleted when the session that owned them goes out of the API's
reach, and every replay stream left by a previous run is swept at startup.
Completion still keeps the stream: letting a consumer catch up is the point.

A NATS that cannot create the stream is reported as an outage (503 at the route),
not a bare 500.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import pytest_asyncio

from document_store.models.document import Document
from document_store.models.replay import ReplayFilter, ReplaySession, ReplayStatus
from document_store.services import nats_client
from document_store.services.replay_service import REPLAY_STREAM_PREFIX, ReplayService

NS = "replay1275"


@pytest_asyncio.fixture(autouse=True)
async def _init_beanie(session_mongo_client):
    from tests.conftest import _ensure_beanie

    await _ensure_beanie(session_mongo_client)
    await Document.delete_all()


class _FakeJetStream:
    def __init__(self, *, stream_names=(), fail_add_with: Exception | None = None):
        self.stream_names = list(stream_names)
        self.deleted: list[str] = []
        self.created: list = []
        self.fail_add_with = fail_add_with

    async def streams_info(self, offset=0):
        # nats-py's shape: a coroutine answering one page as a list. Pages of
        # two, so a sweep over more streams than one page must walk the offset.
        page = self.stream_names[offset:offset + 2]
        return [SimpleNamespace(config=SimpleNamespace(name=name)) for name in page]

    async def delete_stream(self, name):
        self.deleted.append(name)

    async def add_stream(self, config):
        if self.fail_add_with is not None:
            raise self.fail_add_with
        self.created.append(config)

    async def publish(self, subject, payload):
        pass


def _nats(js: _FakeJetStream):
    return patch.object(nats_client, "get_jetstream", lambda: js)


def _session(session_id: str, status: ReplayStatus) -> ReplaySession:
    return ReplaySession(
        session_id=session_id, filter=ReplayFilter(namespace=NS),
        stream_name=f"{REPLAY_STREAM_PREFIX}{session_id.upper()}",
        subject_prefix=f"wip.replay.{session_id}", status=status,
    )


@pytest.mark.asyncio
async def test_the_startup_sweep_deletes_streams_no_session_owns():
    js = _FakeJetStream(stream_names=[
        f"{REPLAY_STREAM_PREFIX}OLD1", f"{REPLAY_STREAM_PREFIX}OLD2",
        "WIP_DOCUMENTS", "WIP_REPLAY_LIVE",
    ])
    service = ReplayService()
    service._sessions["live"] = _session("live", ReplayStatus.RUNNING)

    with _nats(js):
        swept = await service.sweep_orphan_streams()

    assert swept == 2
    assert js.deleted == [f"{REPLAY_STREAM_PREFIX}OLD1", f"{REPLAY_STREAM_PREFIX}OLD2"]


@pytest.mark.asyncio
async def test_the_sweep_reports_a_nats_it_cannot_ask_rather_than_failing():
    class Broken(_FakeJetStream):
        async def streams_info(self, offset=0):
            raise RuntimeError("nats down")

    service = ReplayService()
    with _nats(Broken()):
        assert await service.sweep_orphan_streams() is None


@pytest.mark.asyncio
async def test_a_forgetting_eviction_frees_the_streams_it_orphans(monkeypatch):
    import document_store.services.replay_service as mod

    monkeypatch.setattr(mod, "KEEP_FINISHED_SESSIONS", 1)
    service = ReplayService()
    for i, status in enumerate([ReplayStatus.COMPLETED, ReplayStatus.FAILED, ReplayStatus.RUNNING]):
        service._sessions[f"s{i}"] = _session(f"s{i}", status)
    js = _FakeJetStream()

    with _nats(js):
        service._evict_finished()
        await asyncio.sleep(0)  # let the scheduled delete run

    # s0 and s1 finished; only the newest stays readable, so s0's stream —
    # unreachable now that its session is gone — is freed with it.
    assert js.deleted == [f"{REPLAY_STREAM_PREFIX}S0"]
    assert "s0" not in service._sessions and "s1" in service._sessions


@pytest.mark.asyncio
async def test_a_stream_nats_cannot_create_is_an_outage_not_a_bare_500(monkeypatch):
    js = _FakeJetStream(fail_add_with=RuntimeError("nats: stream creation failed"))
    await Document(
        namespace=NS, document_id="d1", template_id="T", template_version=1,
        template_value="PROBE", identity_hash="h1", version=1, data={}, status="active",
    ).insert()

    with _nats(js), pytest.raises(RuntimeError, match="could not create the replay stream"):
        await ReplayService().start_replay({"namespace": NS}, throttle_ms=0)


@pytest.mark.asyncio
async def test_the_startup_sweep_waits_for_nats_to_connect():
    """On a whole-stack start NATS connects after startup: the sweep ran once,
    found no connection and did nothing, so an earlier run's streams stayed
    for their whole age-out window (772379ab review R1). It now waits."""
    js = _FakeJetStream(stream_names=[f"{REPLAY_STREAM_PREFIX}OLD1"])
    connected = asyncio.Event()

    async def wait_connected(timeout=None):
        await connected.wait()
        return True

    service = ReplayService()
    with patch.object(nats_client, "wait_nats_connected", wait_connected), \
            patch.object(nats_client, "get_jetstream", lambda: js if connected.is_set() else None):
        task = asyncio.create_task(service.sweep_once_connected())
        await asyncio.sleep(0.05)
        assert not task.done(), "the sweep gave up before NATS connected"
        connected.set()
        assert await asyncio.wait_for(task, 1) == 1
    assert js.deleted == [f"{REPLAY_STREAM_PREFIX}OLD1"]


@pytest.mark.asyncio
async def test_without_nats_the_startup_sweep_does_nothing():
    async def not_configured(timeout=None):
        return False

    with patch.object(nats_client, "wait_nats_connected", not_configured):
        assert await ReplayService().sweep_once_connected() is None
