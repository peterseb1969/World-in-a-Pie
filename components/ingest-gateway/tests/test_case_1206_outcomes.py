"""Every ingest message ends in one outcome its producer can read (CASE-1206).

The producer's only feedback is the outcome it reads back by correlation_id
with its own key. These paths used to lose it:

- an item whose key the gateway could not identify (unknown, or the runtime
  keys not yet confirmed by the Registry after a restart) was still forwarded
  and written, and its outcome filed under an owner nobody can read as;
- an outcome whose publish failed was acknowledged anyway;
- unknown subjects, non-object bodies, unwrapped payloads and bodies that
  are not UTF-8 were acked or retried to max_deliver with no outcome;
- a missing bulk list was forwarded as [] and reported as a success;
- a path parameter went into the URL unencoded;
- a fetched batch processed serially could outlive the ack wait.
"""

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from ingest_gateway import worker as worker_module
from ingest_gateway.config import settings
from ingest_gateway.main import _providers, identify_producer
from ingest_gateway.models import (
    INGEST_API_KEY_HEADER,
    IngestAction,
    IngestResult,
    IngestResultStatus,
)
from ingest_gateway.worker import IngestWorker
from tests.conftest import make_bulk_response, make_mock_response
from wip_auth import APIKeyProvider, APIKeyRecord, hash_api_key
from wip_auth.providers.api_key import KeysUnverifiable


def _msg(
    *, delivery: int = 1, seq: int = 7, key: str = "k", body: object | None = None,
    subject: str = "wip.ingest.documents.create",
) -> MagicMock:
    msg = MagicMock()
    msg.subject = subject
    msg.data = json.dumps(
        {"correlation_id": "c-1206", "payload": {"x": 1}} if body is None else body
    ).encode()
    msg.headers = {INGEST_API_KEY_HEADER: key}
    msg.metadata = MagicMock()
    msg.metadata.num_delivered = delivery
    msg.metadata.sequence.stream = seq
    msg.ack = AsyncMock()
    msg.nak = AsyncMock()
    msg.term = AsyncMock()
    msg.in_progress = AsyncMock()
    return msg


def _success() -> IngestResult:
    return IngestResult(
        correlation_id="c-1206", action=IngestAction.DOCUMENTS_CREATE,
        status=IngestResultStatus.SUCCESS, http_status_code=200,
    )


def _worker(http, publisher, identify) -> IngestWorker:
    return IngestWorker(MagicMock(), MagicMock(), http, publisher, identify_producer=identify)


@pytest.fixture(autouse=True)
def no_publish_backoff(monkeypatch):
    monkeypatch.setattr(worker_module, "PUBLISH_RETRY_DELAYS", (0, 0))


# ── item 1: an unidentified producer's item is not forwarded ──────────────


def _unverifiable(_key: str) -> str | None:
    raise KeysUnverifiable("API keys cannot be verified: not confirmed yet")


@pytest.mark.asyncio
@pytest.mark.parametrize("identify", [_unverifiable, lambda _k: None], ids=["unverifiable", "unknown"])
async def test_an_unidentified_item_is_retried_not_forwarded(
    mock_http_client, mock_result_publisher, identify
):
    w = _worker(mock_http_client, mock_result_publisher, identify)
    msg = _msg()

    await w._process_message(msg)

    mock_http_client.forward_request.assert_not_awaited()
    mock_result_publisher.publish.assert_not_awaited()
    msg.nak.assert_awaited_once()
    msg.ack.assert_not_awaited()
    assert w.messages_processed == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "identify,status", [(_unverifiable, 503), (lambda _k: None, 401)], ids=["unverifiable", "unknown"]
)
async def test_an_unidentified_item_is_refused_on_the_last_delivery(
    mock_http_client, mock_result_publisher, identify, status
):
    w = _worker(mock_http_client, mock_result_publisher, identify)
    msg = _msg(delivery=settings.retry_attempts)

    await w._process_message(msg)

    mock_http_client.forward_request.assert_not_awaited()
    result: IngestResult = mock_result_publisher.publish.await_args.args[0]
    assert result.status == IngestResultStatus.FAILED
    assert result.http_status_code == status
    assert result.correlation_id == "c-1206"
    msg.ack.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_identified_item_is_forwarded_and_owned(mock_http_client, mock_result_publisher):
    mock_http_client.forward_request = AsyncMock(return_value=_success())
    w = _worker(mock_http_client, mock_result_publisher, lambda _k: "apikey:p")

    await w._process_message(_msg())

    mock_http_client.forward_request.assert_awaited_once()
    assert mock_result_publisher.publish.await_args.args[0].submitted_by == "apikey:p"


def test_identify_producer_tells_unverifiable_from_unknown():
    """Past the runtime-key deadline (as right after a start, before the
    first sync) a runtime key is unverifiable, not unknown; a config key
    still answers."""
    provider = next(p for p in _providers if isinstance(p, APIKeyProvider))
    record = APIKeyRecord(name="runtime-1206", key_hash=hash_api_key("rt-1206"), groups=["wip-users"])
    provider.add_key(record)
    saved = provider._runtime_deadline
    try:
        assert identify_producer("rt-1206") == "apikey:runtime-1206"
        assert identify_producer("nobody-1206") is None
        provider.expire_runtime_keys()
        with pytest.raises(KeysUnverifiable):
            identify_producer("rt-1206")
        assert identify_producer(os.environ["WIP_AUTH_LEGACY_API_KEY"]) is not None
    finally:
        provider._runtime_deadline = saved
        provider.remove_key(record.key_hash)


# ── item 2: acknowledged only once the outcome is stored ──────────────────


@pytest.mark.asyncio
async def test_an_unstored_outcome_is_not_acked_and_the_item_not_rewritten(mock_http_client):
    mock_http_client.forward_request = AsyncMock(return_value=_success())
    publisher = MagicMock(spec=["publish"])
    publisher.publish = AsyncMock(return_value=False)
    w = _worker(mock_http_client, publisher, lambda _k: "apikey:p")

    first = _msg(delivery=1)
    await w._process_message(first)

    first.ack.assert_not_awaited()
    first.nak.assert_awaited_once()
    assert publisher.publish.await_count == worker_module.PUBLISH_ATTEMPTS

    # The redelivery publishes the kept outcome; the item is not written again.
    publisher.publish = AsyncMock(return_value=True)
    second = _msg(delivery=2)
    await w._process_message(second)

    assert mock_http_client.forward_request.await_count == 1
    second.ack.assert_awaited_once()
    assert publisher.publish.await_args.args[0].correlation_id == "c-1206"
    assert w.messages_processed == 1


@pytest.mark.asyncio
async def test_an_outcome_unstored_on_the_last_delivery_is_counted(mock_http_client):
    mock_http_client.forward_request = AsyncMock(return_value=_success())
    publisher = MagicMock(spec=["publish"])
    publisher.publish = AsyncMock(return_value=False)
    w = _worker(mock_http_client, publisher, lambda _k: "apikey:p")
    msg = _msg(delivery=settings.retry_attempts)

    await w._process_message(msg)

    msg.term.assert_awaited_once()
    msg.ack.assert_not_awaited()
    assert w.messages_without_outcome == 1


# ── item 3: every terminal path publishes an outcome ──────────────────────


@pytest.mark.asyncio
async def test_an_unexpected_error_on_the_last_delivery_publishes_an_outcome(
    mock_http_client, mock_result_publisher
):
    mock_http_client.forward_request = AsyncMock(side_effect=RuntimeError("boom"))
    w = _worker(mock_http_client, mock_result_publisher, lambda _k: "apikey:p")

    early = _msg(delivery=1)
    await w._process_message(early)
    early.nak.assert_awaited_once()
    assert w.messages_processed == 0  # a retry is not a finished message

    last = _msg(delivery=settings.retry_attempts)
    await w._process_message(last)
    result: IngestResult = mock_result_publisher.publish.await_args.args[0]
    assert result.status == IngestResultStatus.FAILED
    assert result.http_status_code == 500
    assert result.submitted_by == "apikey:p"
    last.ack.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"correlation_id": "c-1206", "payload": "a string"},
    {"correlation_id": "c-1206", "payload": {"x": 1}, "extra": True},
    {"correlation_id": "c-1206"},
], ids=["payload-not-object", "unknown-envelope-key", "no-payload"])
async def test_an_invalid_envelope_is_refused_under_its_correlation_id(
    mock_http_client, mock_result_publisher, body
):
    w = _worker(mock_http_client, mock_result_publisher, lambda _k: "apikey:p")
    msg = _msg(body=body)

    await w._process_message(msg)

    mock_http_client.forward_request.assert_not_awaited()
    result: IngestResult = mock_result_publisher.publish.await_args.args[0]
    assert (result.correlation_id, result.http_status_code) == ("c-1206", 400)
    assert "invalid envelope" in result.error
    msg.ack.assert_awaited_once()
    msg.nak.assert_not_awaited()


# ── item 5: a path parameter is one encoded segment ───────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["a/b", "x?namespace=other", "y#frag", "../templates"])
async def test_a_path_parameter_cannot_re_point_the_request(http_client, value):
    http_client._client.request = AsyncMock(
        return_value=make_mock_response(200, make_bulk_response([{"status": "created"}]))
    )

    await http_client.forward_request(
        IngestAction.TERMS_BULK, {"terminology_id": value, "terms": [{"value": "v"}]},
        "c", "producer-key",
    )

    url = http_client._client.request.call_args.kwargs["url"]
    segment = url.split("/terminologies/", 1)[1]
    assert segment.endswith("/terms")
    encoded = segment[: -len("/terms")]
    assert "/" not in encoded and "?" not in encoded and "#" not in encoded


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, 42, "", "  "])
async def test_a_path_parameter_must_be_a_non_empty_string(http_client, value):
    http_client._client.request = AsyncMock()

    result = await http_client.forward_request(
        IngestAction.TERMS_BULK, {"terminology_id": value, "terms": []}, "c", "producer-key"
    )

    http_client._client.request.assert_not_called()
    assert result.status == IngestResultStatus.FAILED
    assert result.http_status_code == 400


# ── item 7: queued messages outlive a slow one ────────────────────────────


@pytest.mark.asyncio
async def test_queued_messages_are_kept_alive_while_one_is_slow(
    mock_http_client, mock_result_publisher, monkeypatch
):
    monkeypatch.setattr(worker_module, "ACK_WAIT_SECONDS", 0.03)

    async def slow_forward(*_args):
        await asyncio.sleep(0.1)
        return _success()

    mock_http_client.forward_request = AsyncMock(side_effect=slow_forward)
    w = _worker(mock_http_client, mock_result_publisher, lambda _k: "apikey:p")
    batch = [_msg(seq=i) for i in range(3)]

    await w._process_batch(batch)

    assert all(m.ack.await_count == 1 for m in batch)
    assert batch[2].in_progress.await_count >= 2  # waited through two slow ones
