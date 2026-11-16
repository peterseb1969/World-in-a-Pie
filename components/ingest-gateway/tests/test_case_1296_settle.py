"""A stored outcome is settled once, and an outcome always fits one message.

- An ack that fails after the outcome was stored leaves the message to the
  server's redelivery, which publishes the kept outcome again: the item is
  not forwarded twice, the message is counted once, and the last delivery
  does not publish a FAILED outcome over the stored one.
- A given-up message is counted as given up whether or not its outcome could
  be stored, and a kept given-up outcome is still termed on its redelivery.
- A target's error answer is kept in the outcome only up to a bound, so an
  outcome is never too large to publish.
- A keep-alive that fails is logged, not swallowed.
"""

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from ingest_gateway import http_client as http_client_module
from ingest_gateway import worker as worker_module
from ingest_gateway.config import settings
from ingest_gateway.http_client import IngestHTTPClient
from ingest_gateway.models import IngestAction, IngestResult, IngestResultStatus
from tests.conftest import make_mock_response
from tests.test_case_1206_outcomes import _msg, _success, _worker


@pytest.fixture(autouse=True)
def no_publish_backoff(monkeypatch):
    monkeypatch.setattr(worker_module, "PUBLISH_RETRY_DELAYS", (0, 0))


def _gave_up() -> IngestResult:
    return IngestResult(
        correlation_id="c-1206", action=IngestAction.DOCUMENTS_CREATE,
        status=IngestResultStatus.FAILED, http_status_code=503,
        error="HTTP 503: down", retryable=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery", [1, settings.retry_attempts])
async def test_a_failed_ack_is_redelivered_with_the_stored_outcome(
    mock_http_client, mock_result_publisher, delivery,
):
    mock_http_client.forward_request = AsyncMock(return_value=_success())
    w = _worker(mock_http_client, mock_result_publisher, lambda _k: "apikey:p")

    first = _msg(delivery=delivery)
    first.ack = AsyncMock(side_effect=RuntimeError("connection closed"))
    await w._process_message(first)

    # Not handed back, not refused: no FAILED outcome over the stored one.
    first.nak.assert_not_awaited()
    first.term.assert_not_awaited()
    published = [c.args[0].status for c in mock_result_publisher.publish.await_args_list]
    assert published == [IngestResultStatus.SUCCESS]
    assert w.messages_processed == 0

    second = _msg(delivery=delivery + 1)
    await w._process_message(second)

    assert mock_http_client.forward_request.await_count == 1
    second.ack.assert_awaited_once()
    published = [c.args[0].status for c in mock_result_publisher.publish.await_args_list]
    assert published == [IngestResultStatus.SUCCESS, IngestResultStatus.SUCCESS]
    assert (w.messages_processed, w.messages_failed) == (1, 0)


@pytest.mark.asyncio
async def test_a_given_up_message_whose_outcome_is_unstorable_is_counted_as_given_up(
    mock_http_client,
):
    mock_http_client.forward_request = AsyncMock(return_value=_gave_up())
    publisher = MagicMock(spec=["publish"])
    publisher.publish = AsyncMock(return_value=False)
    w = _worker(mock_http_client, publisher, lambda _k: "apikey:p")

    msg = _msg(delivery=settings.retry_attempts)
    await w._process_message(msg)

    msg.term.assert_awaited_once()
    assert (w.messages_gave_up, w.messages_without_outcome) == (1, 1)


@pytest.mark.asyncio
async def test_a_kept_given_up_outcome_is_termed_on_its_redelivery(mock_http_client):
    mock_http_client.forward_request = AsyncMock(return_value=_gave_up())
    publisher = MagicMock(spec=["publish"])
    publisher.publish = AsyncMock(return_value=True)
    w = _worker(mock_http_client, publisher, lambda _k: "apikey:p")

    first = _msg(delivery=settings.retry_attempts)
    first.term = AsyncMock(side_effect=RuntimeError("connection closed"))
    await w._process_message(first)
    assert w.messages_gave_up == 0

    second = _msg(delivery=settings.retry_attempts + 1)
    await w._process_message(second)

    second.term.assert_awaited_once()
    second.ack.assert_not_awaited()
    assert mock_http_client.forward_request.await_count == 1
    assert (w.messages_gave_up, w.messages_processed) == (1, 1)


def test_a_large_error_answer_is_bounded():
    detail = [{"loc": ["body", i], "msg": "x" * 200} for i in range(5000)]
    body = {"detail": detail}
    assert len(json.dumps(body)) > 1_000_000
    result = IngestHTTPClient._outcome(
        IngestAction.DOCUMENTS_CREATE, "c-1296", make_mock_response(422, json_data=body),
    )
    assert result.status == IngestResultStatus.FAILED and not result.retryable
    assert len(result.error) <= len("HTTP 422: ") + http_client_module.ERROR_DETAIL_MAX
    assert result.response["truncated"] is True
    assert len(result.response["raw"]) == http_client_module.ERROR_BODY_MAX
    assert len(result.model_dump_json()) < 200_000


def test_a_small_error_answer_is_kept_whole():
    body = {"detail": "not found"}
    result = IngestHTTPClient._outcome(
        IngestAction.DOCUMENTS_CREATE, "c-1296", make_mock_response(404, json_data=body),
    )
    assert (result.response, result.error) == (body, "HTTP 404: not found")


def test_an_error_answer_that_is_not_an_object_is_an_outcome():
    result = IngestHTTPClient._outcome(
        IngestAction.DOCUMENTS_CREATE, "c-1296",
        make_mock_response(400, json_data=["bad"], text='["bad"]'),
    )
    assert result.status == IngestResultStatus.FAILED
    assert (result.response, result.error) == ({"body": ["bad"]}, 'HTTP 400: ["bad"]')


@pytest.mark.asyncio
async def test_a_failed_keep_alive_is_logged(
    mock_http_client, mock_result_publisher, monkeypatch, caplog,
):
    monkeypatch.setattr(worker_module, "ACK_WAIT_SECONDS", 0.03)

    async def slow_forward(*_args):
        await asyncio.sleep(0.05)
        return _success()

    mock_http_client.forward_request = AsyncMock(side_effect=slow_forward)
    w = _worker(mock_http_client, mock_result_publisher, lambda _k: "apikey:p")
    batch = [_msg(seq=i) for i in range(2)]
    batch[1].in_progress = AsyncMock(side_effect=RuntimeError("no connection"))

    with caplog.at_level(logging.WARNING, logger=worker_module.logger.name):
        await w._process_batch(batch)

    assert "Could not keep a queued message alive: no connection" in caplog.text
    assert all(m.ack.await_count == 1 for m in batch)
