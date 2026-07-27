"""Replay finds documents (CASE-762): the query layer, unmocked.

Replay was born querying `is_latest: True` — a field that has never existed
on the Document model ("latest" is computed at read time, never persisted).
Both the precondition count and the publish scan matched zero rows in every
namespace, so start_replay raised "No documents match the replay filter"
unconditionally, for the feature's whole life. Nobody noticed because the
route tests mock the entire service.

These tests are the layer that was missing: the real service against real
MongoDB, with only NATS faked. They pin latest-per-document_id semantics
(the exporter's dedup rule) for both the count and the published set, and
the born-broken regression: a plain namespace with documents must not raise.
"""

from __future__ import annotations

import asyncio
import json

import pytest
import pytest_asyncio

from document_store.models.document import Document
from document_store.services import nats_client
from document_store.services.replay_service import ReplayService

NS = "replayns"


@pytest_asyncio.fixture(autouse=True)
async def _init_beanie(session_mongo_client):
    """Bind Beanie via the shared session client, isolate per test.

    A private client + re-init here would re-bind Document and then die
    with this fixture, stranding later tests on a closed client. The
    session-wide union binding serves everyone; isolation is the
    delete_all.
    """
    from tests.conftest import _ensure_beanie
    await _ensure_beanie(session_mongo_client)
    await Document.delete_all()


class _FakeJetStream:
    """Captures publishes; stands in for NATS JetStream."""

    def __init__(self):
        self.streams: list[str] = []
        self.published: list[tuple[str, bytes]] = []

    async def add_stream(self, config):
        self.streams.append(config.name)

    async def publish(self, subject, payload):
        self.published.append((subject, payload))

    async def delete_stream(self, name):
        self.streams.remove(name)


@pytest.fixture
def fake_nats(monkeypatch):
    js = _FakeJetStream()
    monkeypatch.setattr(nats_client, "_nats_enabled", True)
    monkeypatch.setattr(nats_client, "_jetstream", js)
    return js


def _doc(doc_id: str, version: int, *, template_value: str = "PROBE",
         status: str = "active", ns: str = NS) -> Document:
    return Document(
        namespace=ns,
        document_id=doc_id,
        template_id="0190c762-0000-7000-0000-000000000001",
        template_version=1,
        template_value=template_value,
        identity_hash=f"h-{doc_id}",
        version=version,
        data={"n": version},
        status=status,
    )


async def _run_replay(service: ReplayService, **filter_kwargs) -> dict:
    session = await service.start_replay(
        {"namespace": NS, **filter_kwargs}, throttle_ms=0
    )
    await asyncio.wait_for(service._tasks[session["session_id"]], timeout=10.0)
    return service.get_session(session["session_id"])


async def test_replay_finds_documents_at_all(fake_nats):
    # The born-broken regression pin: a namespace with plain documents must
    # not raise "No documents match the replay filter".
    await _doc("d1", 1).insert()
    session = await _run_replay(ReplayService())
    assert session["total_count"] == 1
    assert session["published"] == 1


async def test_count_and_published_are_latest_per_document(fake_nats):
    # Two versions of d1, one of d2: two documents, and the published rows
    # are the highest versions — not three version rows.
    await _doc("d1", 1).insert()
    await _doc("d1", 2).insert()
    await _doc("d2", 1).insert()

    session = await _run_replay(ReplayService())
    assert session["total_count"] == 2
    assert session["published"] == 2


    events = [json.loads(p) for s, p in fake_nats.published if ".documents." in s]
    versions = {e["document"]["document_id"]: e["document"]["version"] for e in events}
    assert versions == {"d1": 2, "d2": 1}


async def test_template_value_filter_scopes(fake_nats):
    await _doc("d1", 1, template_value="PROBE").insert()
    await _doc("d2", 1, template_value="OTHER").insert()

    session = await _run_replay(ReplayService(), template_value="PROBE")
    assert session["total_count"] == 1


async def test_pagination_covers_all_documents_once(fake_nats):
    # More documents than one batch: every document published exactly once
    # (the deterministic sort in the pipeline is what makes skip/limit safe).
    for i in range(25):
        await _doc(f"d{i:03d}", 1).insert()

    service = ReplayService()
    session = await service.start_replay(
        {"namespace": NS}, throttle_ms=0, batch_size=10
    )
    await asyncio.wait_for(service._tasks[session["session_id"]], timeout=10.0)


    events = [json.loads(p) for s, p in fake_nats.published if ".documents." in s]
    ids = [e["document"]["document_id"] for e in events]
    assert len(ids) == 25
    assert len(set(ids)) == 25


async def test_no_match_error_names_the_filter(fake_nats):
    with pytest.raises(ValueError) as exc_info:
        await ReplayService().start_replay(
            {"namespace": NS, "template_value": "NOPE"}
        )
    message = str(exc_info.value)
    assert "No documents match" in message
    assert NS in message and "NOPE" in message


async def test_inactive_documents_are_not_replayed(fake_nats):
    await _doc("d1", 1, status="inactive").insert()
    with pytest.raises(ValueError, match="No documents match"):
        await ReplayService().start_replay({"namespace": NS})
