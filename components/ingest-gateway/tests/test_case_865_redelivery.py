"""A transient forward failure is redelivered, not acknowledged (CASE-865).

The worker used to ack every message after forwarding it, including when
the target was unreachable, timed out or answered 5xx — so a message that
arrived while document-store (or def-store / template-store) was starting
or restarting was dropped. Transient failures now nak with a growing delay;
the last allowed delivery publishes a final FAILED result and terms the
message. Definitive outcomes (success, a 200 with per-item failures, a 4xx)
are acknowledged as before.
"""

import asyncio
import contextlib
import json
import os
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from ingest_gateway.config import settings
from ingest_gateway.models import IngestAction, IngestResult, IngestResultStatus
from ingest_gateway.worker import IngestWorker
from tests.conftest import make_bulk_response, make_mock_response

NATS_TEST_URL = os.environ.get("NATS_TEST_URL")


def _msg(delivery: int) -> MagicMock:
    msg = MagicMock()
    msg.subject = "wip.ingest.documents.create"
    msg.data = json.dumps({"correlation_id": "c-865", "payload": {"x": 1}}).encode()
    msg.metadata = MagicMock()
    msg.metadata.sequence.stream = 1
    msg.metadata.num_delivered = delivery
    msg.ack = AsyncMock()
    msg.nak = AsyncMock()
    msg.term = AsyncMock()
    return msg


def _result(retryable: bool, status_code: int | None = None) -> IngestResult:
    return IngestResult(
        correlation_id="c-865",
        action=IngestAction.DOCUMENTS_CREATE,
        status=IngestResultStatus.FAILED,
        http_status_code=status_code,
        error="target down" if retryable else "HTTP 422: invalid",
        retryable=retryable,
    )


# ── classification in the HTTP client ─────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("status,retryable", [
    (500, True), (502, True), (503, True), (408, True), (429, True),
    (400, False), (401, False), (403, False), (404, False), (409, False), (422, False),
])
async def test_http_status_classification(http_client, status, retryable):
    http_client._client.request = AsyncMock(
        return_value=make_mock_response(status, {"detail": "x"})
    )
    result = await http_client.forward_request(IngestAction.DOCUMENTS_CREATE, {"x": 1}, "c")
    assert result.status == IngestResultStatus.FAILED
    assert result.retryable is retryable


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [
    httpx.ConnectError("refused"),
    httpx.ReadTimeout("slow"),
    httpx.RemoteProtocolError("reset"),
])
async def test_transport_failures_are_retryable(http_client, exc):
    http_client._client.request = AsyncMock(side_effect=exc)
    result = await http_client.forward_request(IngestAction.DOCUMENTS_CREATE, {"x": 1}, "c")
    assert result.retryable is True


@pytest.mark.asyncio
async def test_per_item_failures_in_a_200_are_definitive(http_client):
    http_client._client.request = AsyncMock(return_value=make_mock_response(
        200, make_bulk_response([{"index": 0, "status": "error", "error": "bad"}], failed=1)
    ))
    result = await http_client.forward_request(IngestAction.DOCUMENTS_CREATE, {"x": 1}, "c")
    assert result.status == IngestResultStatus.FAILED
    assert result.retryable is False


# ── worker behaviour ──────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery,delay", [(1, 5), (2, 15), (3, 30), (4, 60), (5, 120), (9, 120)])
async def test_transient_failure_naks_with_growing_delay(
    worker, mock_http_client, mock_result_publisher, delivery, delay
):
    mock_http_client.forward_request = AsyncMock(return_value=_result(retryable=True))
    msg = _msg(delivery)
    await worker._process_message(msg)

    msg.nak.assert_awaited_once_with(delay=delay)
    msg.ack.assert_not_awaited()
    msg.term.assert_not_awaited()
    mock_result_publisher.publish.assert_not_awaited()  # only final outcomes are published
    assert worker.messages_retried == 1
    assert worker.messages_processed == 0


@pytest.mark.asyncio
async def test_last_delivery_publishes_failed_and_terms(worker, mock_http_client, mock_result_publisher):
    mock_http_client.forward_request = AsyncMock(return_value=_result(retryable=True))
    msg = _msg(settings.retry_attempts)
    await worker._process_message(msg)

    msg.term.assert_awaited_once()
    msg.nak.assert_not_awaited()
    msg.ack.assert_not_awaited()
    published = mock_result_publisher.publish.await_args.args[0]
    assert published.status == IngestResultStatus.FAILED
    assert f"gave up after {settings.retry_attempts} deliveries" in published.error
    assert (worker.messages_gave_up, worker.messages_failed, worker.messages_processed) == (1, 1, 1)


@pytest.mark.asyncio
async def test_definitive_failure_is_published_and_acked(worker, mock_http_client, mock_result_publisher):
    mock_http_client.forward_request = AsyncMock(return_value=_result(retryable=False, status_code=422))
    msg = _msg(1)
    await worker._process_message(msg)

    msg.ack.assert_awaited_once()
    msg.nak.assert_not_awaited()
    mock_result_publisher.publish.assert_awaited_once()
    assert worker.messages_retried == 0


def test_default_policy_outlasts_a_restart():
    """The default schedule must span well beyond a pod restart (minutes)."""
    total = sum(IngestWorker._retry_delay(d) for d in range(1, settings.retry_attempts))
    assert total >= 10 * 60


# ── startup: the consumer's retry policy reaches existing installs ────────


@pytest.mark.asyncio
async def test_start_applies_consumer_config_before_subscribing():
    js = MagicMock()
    calls: list[str] = []
    js.add_consumer = AsyncMock(side_effect=lambda *a, **k: calls.append("add_consumer"))

    sub = MagicMock()
    sub.fetch = AsyncMock(side_effect=asyncio.CancelledError)

    async def pull_subscribe(*_a, **_k):
        calls.append("pull_subscribe")
        return sub

    js.pull_subscribe = pull_subscribe
    worker = IngestWorker(MagicMock(), js, MagicMock(), MagicMock())
    await worker.start()

    assert calls == ["add_consumer", "pull_subscribe"]
    config = js.add_consumer.await_args.kwargs["config"]
    assert config.max_deliver == settings.retry_attempts
    assert config.filter_subject == "wip.ingest.>"


@pytest.mark.asyncio
async def test_start_continues_when_the_consumer_update_is_refused():
    js = MagicMock()
    js.add_consumer = AsyncMock(side_effect=RuntimeError("not editable"))
    sub = MagicMock()
    sub.fetch = AsyncMock(side_effect=asyncio.CancelledError)
    js.pull_subscribe = AsyncMock(return_value=sub)

    worker = IngestWorker(MagicMock(), js, MagicMock(), MagicMock())
    await worker.start()
    js.pull_subscribe.assert_awaited_once()


@pytest.mark.skipif(not NATS_TEST_URL, reason="NATS not available (set NATS_TEST_URL)")
@pytest.mark.asyncio
async def test_existing_consumer_is_converged_in_place(monkeypatch):
    """Against a real NATS server: an existing durable created with the old
    max_deliver is updated on worker start, and its ack state is kept."""
    import nats
    from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy, StreamConfig

    stream, durable = "C865_TEST_INGEST", "c865-test-durable"
    monkeypatch.setattr(settings, "nats_ingest_stream_name", stream)
    monkeypatch.setattr(settings, "nats_ingest_durable_name", durable)

    nc = await nats.connect(NATS_TEST_URL)
    js = nc.jetstream()
    try:
        with contextlib.suppress(Exception):
            await js.delete_stream(stream)
        await js.add_stream(StreamConfig(name=stream, subjects=["wip.ingest.>"]))
        await js.add_consumer(stream, ConsumerConfig(
            durable_name=durable, deliver_policy=DeliverPolicy.ALL,
            ack_policy=AckPolicy.EXPLICIT, ack_wait=60, max_deliver=3,
            filter_subject="wip.ingest.>",
        ))
        await js.publish("wip.ingest.documents.create", b"{}")
        await js.publish("wip.ingest.documents.create", b"{}")
        old = await js.pull_subscribe("wip.ingest.>", durable=durable, stream=stream)
        (first,) = await old.fetch(1, timeout=2)
        await first.ack()
        await asyncio.sleep(0.2)

        worker = IngestWorker(nc, js, MagicMock(), MagicMock())
        task = asyncio.create_task(worker.start())
        await asyncio.sleep(1.0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        info = await js.consumer_info(stream, durable)
        assert info.config.max_deliver == settings.retry_attempts
        assert info.ack_floor.stream_seq == 1  # the acked message stays acked
    finally:
        try:
            await js.delete_stream(stream)
        finally:
            await nc.close()
