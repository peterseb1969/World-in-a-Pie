"""Replay states its namespace and errors, and keeps nothing forever.

- A replay names its namespace: the request requires it (it used to default
  to 'wip'), and every session response carries it.
- A failed session says why (`error`), where it used to say only "failed".
- The count runs without a sort, so a large namespace does not meet the
  in-memory sort limit; the publish scan may spill to disk.
- Each replay stream has a max_age, so unconsumed events expire even when
  nothing ever drains or deletes the stream.
- Finished sessions beyond the newest few are forgotten.
- A MongoDB failure counting the documents is a 503, not a bare 500.
- Timing statistics are kept in bounded memory.
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from pymongo.errors import OperationFailure

from document_store.models.document import Document
from document_store.models.replay import ReplayFilter, ReplaySession, ReplayStatus
from document_store.services import nats_client, replay_service
from document_store.services.replay_service import ReplayService, get_replay_service
from document_store.services.timing_stats import PERCENTILE_WINDOW, TimingStats

NS = "replaybounds"
API = "/api/document-store/replay"


@pytest_asyncio.fixture(autouse=True)
async def _init_beanie(session_mongo_client):
    from tests.conftest import _ensure_beanie
    await _ensure_beanie(session_mongo_client)
    await Document.delete_all()


class _FakeJetStream:
    def __init__(self):
        self.configs: list = []

    async def add_stream(self, config):
        self.configs.append(config)

    async def publish(self, subject, payload):
        pass

    async def delete_stream(self, name):
        pass


@pytest.fixture
def fake_nats(monkeypatch):
    js = _FakeJetStream()
    monkeypatch.setattr(nats_client, "get_jetstream", lambda: js)
    return js


async def _seed(n: int = 2) -> None:
    for i in range(n):
        await Document(
            namespace=NS, document_id=f"rb-{i}", template_id="0190c145-0000-7000-0000-000000000001",
            template_version=1, template_value="PROBE", identity_hash=f"h-{i}",
            version=1, data={"i": i}, status="active",
        ).insert()


def test_the_count_pipeline_does_not_sort():
    pipeline = ReplayService._latest_docs_pipeline(ReplayFilter(namespace=NS), count_only=True)
    assert not any("$sort" in stage for stage in pipeline)
    assert pipeline[-1] == {"$count": "total"}


@pytest.mark.asyncio
async def test_a_replay_stream_ages_out_and_the_scan_may_spill(fake_nats, monkeypatch):
    await _seed()
    seen: list[dict] = []
    collection = Document.get_motor_collection()

    class Recording:
        def __getattr__(self, name):
            return getattr(collection, name)

        def aggregate(self, pipeline, **kwargs):
            seen.append(kwargs)
            return collection.aggregate(pipeline, **kwargs)

    monkeypatch.setattr(Document, "get_motor_collection", classmethod(lambda cls: Recording()))
    service = ReplayService()
    session = await service.start_replay({"namespace": NS}, throttle_ms=0)
    await asyncio.wait_for(service._tasks[session["session_id"]], timeout=10.0)

    assert fake_nats.configs[0].max_age == replay_service.REPLAY_STREAM_MAX_AGE_SECONDS
    assert seen and all(kw.get("allowDiskUse") is True for kw in seen)
    assert service.get_session(session["session_id"])["status"] == "completed"


def test_finished_sessions_beyond_the_newest_are_forgotten(monkeypatch):
    monkeypatch.setattr(replay_service, "KEEP_FINISHED_SESSIONS", 2)
    service = ReplayService()
    for i, status in enumerate(
        [ReplayStatus.COMPLETED, ReplayStatus.FAILED, ReplayStatus.RUNNING,
         ReplayStatus.CANCELLED, ReplayStatus.COMPLETED]
    ):
        service._sessions[f"s{i}"] = ReplaySession(
            session_id=f"s{i}", filter=ReplayFilter(namespace=NS), stream_name=f"S{i}",
            subject_prefix=f"wip.replay.s{i}", status=status,
        )
    service._evict_finished()
    # The running session stays; of the four finished, the newest two stay.
    assert list(service._sessions) == ["s2", "s3", "s4"]


@pytest.mark.asyncio
async def test_a_replay_without_a_namespace_is_refused(client, auth_headers):
    resp = await client.post(f"{API}/start", headers=auth_headers, json={"filter": {}})
    assert resp.status_code == 422, resp.text
    resp = await client.post(f"{API}/start", headers=auth_headers, json={})
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_responses_name_the_namespace_and_a_failure_says_why(
    client, auth_headers, fake_nats, monkeypatch,
):
    await _seed()
    service = get_replay_service()

    async def boom(self, session_id):
        session = self._sessions[session_id]
        session.status = ReplayStatus.FAILED
        session.error = "NATS publish refused"

    monkeypatch.setattr(ReplayService, "_publish_replay", boom)
    started = await client.post(f"{API}/start", headers=auth_headers, json={
        "filter": {"namespace": NS}, "throttle_ms": 0,
    })
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["namespace"] == NS
    await asyncio.wait_for(service._tasks[body["session_id"]], timeout=10.0)

    got = await client.get(f"{API}/{body['session_id']}", headers=auth_headers)
    assert got.status_code == 200, got.text
    assert got.json()["status"] == "failed"
    assert got.json()["error"] == "NATS publish refused"
    assert got.json()["namespace"] == NS


@pytest.mark.asyncio
async def test_a_failing_count_is_a_503_naming_the_cause(client, auth_headers, fake_nats, monkeypatch):
    collection = Document.get_motor_collection()

    class Failing:
        def __getattr__(self, name):
            return getattr(collection, name)

        def aggregate(self, *_a, **_k):
            raise OperationFailure("Sort exceeded memory limit")

    monkeypatch.setattr(Document, "get_motor_collection", classmethod(lambda cls: Failing()))
    resp = await client.post(f"{API}/start", headers=auth_headers, json={"filter": {"namespace": NS}})
    assert resp.status_code == 503, resp.text
    assert "Sort exceeded memory limit" in resp.text


def test_timing_stats_stay_bounded():
    stats = TimingStats()
    for i in range(PERCENTILE_WINDOW * 3):
        stats.record({"validate": float(i)})
    stage = stats.stages()["validate"]
    assert stats.runs == PERCENTILE_WINDOW * 3
    assert stage["count"] == PERCENTILE_WINDOW * 3
    assert stage["percentile_window"] == PERCENTILE_WINDOW
    assert stage["min_ms"] == 0.0 and stage["max_ms"] == float(PERCENTILE_WINDOW * 3 - 1)
    assert len(stats._stages["validate"].recent) == PERCENTILE_WINDOW
