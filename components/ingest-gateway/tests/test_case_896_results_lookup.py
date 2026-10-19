"""CASE-896 — a producer reads the outcomes of its own ingest items, and only those.

The `ingest-producer` NATS role publishes work items but cannot read the
results stream: it carries every producer's outcomes. So the gateway serves
outcomes over HTTP, scoped by the same authority the write ran under — the
API key in the item's X-API-Key header:

- the worker resolves the key to its principal and records it as
  `submitted_by`;
- the outcome is published on wip.ingest.results.<owner>.<correlation>, both
  tokens hashed — owner-scoped, and safe for any producer-chosen ID;
- GET /results/{correlation_id} and POST /results/lookup read the caller's
  own outcomes; only admins may name another principal (`submitted_by`).
"""

import contextlib
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from nats.js.errors import NotFoundError

from ingest_gateway import result_publisher as rp
from ingest_gateway.main import _providers, app, identify_producer, state
from ingest_gateway.models import (
    INGEST_API_KEY_HEADER,
    IngestAction,
    IngestResult,
    IngestResultStatus,
)
from ingest_gateway.result_publisher import (
    UNAUTHENTICATED_OWNER,
    ResultPublisher,
    owner_token,
    results_subject,
)
from ingest_gateway.worker import IngestWorker
from wip_auth import APIKeyProvider, APIKeyRecord, hash_api_key

ADMIN_KEY = os.environ["WIP_AUTH_LEGACY_API_KEY"]
PRODUCER_A = ("producer-a", "pa-secret-896")
PRODUCER_B = ("producer-b", "pb-secret-896")


def _key_provider() -> APIKeyProvider:
    return next(p for p in _providers if isinstance(p, APIKeyProvider))


@pytest.fixture(autouse=True)
def producer_keys():
    """Two non-admin producer keys, known to the gateway like synced runtime keys."""
    provider = _key_provider()
    records = [
        APIKeyRecord(name=name, key_hash=hash_api_key(secret), groups=["wip-users"])
        for name, secret in (PRODUCER_A, PRODUCER_B)
    ]
    for record in records:
        provider.add_key(record)
    yield
    for record in records:
        provider.remove_key(record.key_hash)


def _outcome(correlation_id: str, submitted_by: str | None) -> IngestResult:
    return IngestResult(
        correlation_id=correlation_id,
        action=IngestAction.DOCUMENTS_CREATE,
        status=IngestResultStatus.SUCCESS,
        http_status_code=200,
        response={"results": [{"status": "created"}]},
        submitted_by=submitted_by,
    )


class FakeJetStream:
    """In-memory stand-in for the results stream: publish stores by exact
    subject, get_last_msg reads one subject back (NotFoundError if none)."""

    def __init__(self):
        self.messages: dict[str, bytes] = {}

    async def publish(self, subject, payload):
        self.messages[subject] = payload
        return SimpleNamespace(seq=len(self.messages))

    async def get_last_msg(self, stream_name, subject):
        if subject not in self.messages:
            raise NotFoundError()
        return SimpleNamespace(subject=subject, data=self.messages[subject])


@pytest.fixture
def fake_js():
    original = state.bus
    js = FakeJetStream()
    state.bus = SimpleNamespace(connected=True, jetstream=js)
    yield js
    state.bus = original


@pytest_asyncio.fixture
async def http():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


def _as(key: str) -> dict:
    return {"X-API-Key": key}


# ---- Subject ----


def test_owner_scopes_the_subject():
    assert results_subject("apikey:producer-a", "c1") != results_subject("apikey:producer-b", "c1")
    assert results_subject("apikey:producer-a", "c1") == results_subject("apikey:producer-a", "c1")


def test_an_item_without_a_usable_key_has_the_reserved_owner():
    assert owner_token(None) == owner_token("") == UNAUTHENTICATED_OWNER
    assert owner_token("apikey:x") != UNAUTHENTICATED_OWNER


@pytest.mark.parametrize("correlation_id", ["has space", "dotted.id", "*", ">", "a.>", "tab\tid"])
def test_any_correlation_id_yields_one_literal_subject_token(correlation_id):
    subject = results_subject("apikey:producer-a", correlation_id)
    tokens = subject.split(".")
    assert len(tokens) == 5
    assert all(t.isalnum() for t in tokens[3:])


# ---- Gateway identifies the producer ----


def test_identify_producer_resolves_a_known_key():
    assert identify_producer(PRODUCER_A[1]) == "apikey:producer-a"


def test_identify_producer_unknown_key_is_none():
    assert identify_producer("not-a-key") is None


# ---- Worker records the submitter ----


def _msg(headers: dict | None, data: bytes | None = None) -> MagicMock:
    msg = MagicMock()
    msg.subject = "wip.ingest.documents.create"
    msg.data = data or json.dumps({"correlation_id": "c-896", "payload": {"x": 1}}).encode()
    msg.headers = headers
    msg.metadata = MagicMock()
    msg.metadata.num_delivered = 1
    msg.ack = AsyncMock()
    msg.nak = AsyncMock()
    msg.term = AsyncMock()
    return msg


@pytest.fixture
def identifying_worker(mock_http_client, mock_result_publisher):
    identify = MagicMock(side_effect=lambda key: {"k-a": "apikey:producer-a"}.get(key))
    worker = IngestWorker(
        MagicMock(), MagicMock(), mock_http_client, mock_result_publisher,
        identify_producer=identify,
    )
    return worker, identify


@pytest.mark.asyncio
async def test_forwarded_outcome_records_the_submitter(
    identifying_worker, mock_http_client, mock_result_publisher
):
    worker, _ = identifying_worker
    mock_http_client.forward_request = AsyncMock(return_value=_outcome("c-896", None))

    await worker._process_message(_msg({INGEST_API_KEY_HEADER: "k-a"}))

    published: IngestResult = mock_result_publisher.publish.call_args.args[0]
    assert published.submitted_by == "apikey:producer-a"
    assert published.model_dump_json_safe()["submitted_by"] == "apikey:producer-a"


@pytest.mark.asyncio
async def test_an_unknown_key_records_no_submitter(
    identifying_worker, mock_http_client, mock_result_publisher
):
    worker, _ = identifying_worker
    mock_http_client.forward_request = AsyncMock(return_value=_outcome("c-896", None))

    await worker._process_message(_msg({INGEST_API_KEY_HEADER: "k-unknown"}))

    assert mock_result_publisher.publish.call_args.args[0].submitted_by is None


@pytest.mark.asyncio
async def test_a_keyless_item_is_not_identified(identifying_worker, mock_result_publisher):
    worker, identify = identifying_worker

    await worker._process_message(_msg({}))

    identify.assert_not_called()
    assert mock_result_publisher.publish.call_args.args[0].submitted_by is None


@pytest.mark.asyncio
async def test_a_parse_error_outcome_records_the_submitter(identifying_worker, mock_result_publisher):
    worker, _ = identifying_worker

    await worker._process_message(_msg({INGEST_API_KEY_HEADER: "k-a"}, data=b"{not json"))

    assert mock_result_publisher.publish.call_args.args[0].submitted_by == "apikey:producer-a"


# ---- Lookup endpoints ----


async def _publish(js, correlation_id: str, submitted_by: str | None) -> None:
    await ResultPublisher(js).publish(_outcome(correlation_id, submitted_by))


@pytest.mark.asyncio
async def test_a_producer_reads_its_own_outcome(fake_js, http):
    await _publish(fake_js, "c1", "apikey:producer-a")

    resp = await http.get("/api/ingest-gateway/results/c1", headers=_as(PRODUCER_A[1]))

    assert resp.status_code == 200
    body = resp.json()
    assert body["correlation_id"] == "c1"
    assert body["status"] == "success"
    assert body["submitted_by"] == "apikey:producer-a"


@pytest.mark.asyncio
async def test_a_producer_cannot_read_another_producers_outcome(fake_js, http):
    await _publish(fake_js, "c1", "apikey:producer-a")

    resp = await http.get("/api/ingest-gateway/results/c1", headers=_as(PRODUCER_B[1]))

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_two_producers_reusing_one_correlation_id_each_read_their_own(fake_js, http):
    await _publish(fake_js, "shared", "apikey:producer-a")
    await ResultPublisher(fake_js).publish(IngestResult(
        correlation_id="shared", action=IngestAction.DOCUMENTS_CREATE,
        status=IngestResultStatus.FAILED, http_status_code=403,
        submitted_by="apikey:producer-b",
    ))

    a = await http.get("/api/ingest-gateway/results/shared", headers=_as(PRODUCER_A[1]))
    b = await http.get("/api/ingest-gateway/results/shared", headers=_as(PRODUCER_B[1]))

    assert a.json()["status"] == "success"
    assert b.json()["status"] == "failed"


@pytest.mark.asyncio
async def test_a_producer_may_not_name_another_principal(fake_js, http):
    await _publish(fake_js, "c1", "apikey:producer-a")

    resp = await http.get(
        "/api/ingest-gateway/results/c1",
        params={"submitted_by": "apikey:producer-a"},
        headers=_as(PRODUCER_B[1]),
    )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_an_admin_reads_a_named_principals_outcome(fake_js, http):
    await _publish(fake_js, "c1", "apikey:producer-a")

    resp = await http.get(
        "/api/ingest-gateway/results/c1",
        params={"submitted_by": "apikey:producer-a"},
        headers=_as(ADMIN_KEY),
    )

    assert resp.status_code == 200
    assert resp.json()["submitted_by"] == "apikey:producer-a"


@pytest.mark.asyncio
async def test_an_admin_reads_unauthenticated_outcomes_with_an_empty_submitted_by(fake_js, http):
    await _publish(fake_js, "keyless", None)

    resp = await http.get(
        "/api/ingest-gateway/results/keyless",
        params={"submitted_by": ""},
        headers=_as(ADMIN_KEY),
    )

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_lookup_answers_per_id_in_request_order(fake_js, http):
    await _publish(fake_js, "c1", "apikey:producer-a")
    await _publish(fake_js, "c3", "apikey:producer-a")
    await _publish(fake_js, "c2", "apikey:producer-b")

    resp = await http.post(
        "/api/ingest-gateway/results/lookup",
        json={"correlation_ids": ["c3", "c2", "c1"]},
        headers=_as(PRODUCER_A[1]),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert [(i["correlation_id"], i["status"]) for i in body["results"]] == [
        ("c3", "found"), ("c2", "not_found"), ("c1", "found"),
    ]
    assert body["total"] == 3 and body["found"] == 2


@pytest.mark.asyncio
async def test_lookup_rejects_an_empty_list(fake_js, http):
    resp = await http.post(
        "/api/ingest-gateway/results/lookup",
        json={"correlation_ids": []},
        headers=_as(PRODUCER_A[1]),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_lookup_requires_a_key(fake_js, http):
    resp = await http.get("/api/ingest-gateway/results/c1")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_lookup_without_nats_is_503(http):
    original = state.bus
    state.bus = SimpleNamespace(connected=False, jetstream=None)
    try:
        resp = await http.get("/api/ingest-gateway/results/c1", headers=_as(PRODUCER_A[1]))
    finally:
        state.bus = original
    assert resp.status_code == 503


# ---- Real NATS: publish and last-message-by-subject read ----

NATS_TEST_URL = os.environ.get("NATS_TEST_URL")


@pytest.mark.skipif(not NATS_TEST_URL, reason="NATS not available (set NATS_TEST_URL)")
@pytest.mark.asyncio
async def test_round_trip_on_a_real_stream(http, monkeypatch):
    """The publisher's subjects and the lookup's last-message reads agree on
    a real JetStream, for IDs NATS could not take verbatim — whitespace —
    and for IDs that would be wildcards in a last-message read."""
    import nats

    from ingest_gateway.config import settings

    stream = "TEST896_RESULTS"
    monkeypatch.setattr(rp, "RESULTS_SUBJECT_PREFIX", "test896.results")
    monkeypatch.setattr(settings, "nats_results_stream_name", stream)

    nc = await nats.connect(NATS_TEST_URL)
    js = nc.jetstream()
    with contextlib.suppress(Exception):
        await js.delete_stream(stream)
    await js.add_stream(name=stream, subjects=["test896.results.>"])
    original = state.bus
    state.bus = SimpleNamespace(connected=True, jetstream=js)
    try:
        assert await ResultPublisher(js).publish(_outcome("has space", "apikey:producer-a"))
        assert await ResultPublisher(js).publish(_outcome("other", "apikey:producer-a"))

        own = await http.get("/api/ingest-gateway/results/has space", headers=_as(PRODUCER_A[1]))
        assert own.status_code == 200 and own.json()["correlation_id"] == "has space"

        other_producer = await http.get(
            "/api/ingest-gateway/results/has space", headers=_as(PRODUCER_B[1])
        )
        assert other_producer.status_code == 404

        # "*" and ">" are looked up as literal IDs, never as wildcards that
        # would return another item's outcome.
        for wildcard in ("*", ">"):
            resp = await http.post(
                "/api/ingest-gateway/results/lookup",
                json={"correlation_ids": [wildcard]},
                headers=_as(PRODUCER_A[1]),
            )
            assert resp.json()["results"][0]["status"] == "not_found"
    finally:
        state.bus = original
        await js.delete_stream(stream)
        await nc.close()
