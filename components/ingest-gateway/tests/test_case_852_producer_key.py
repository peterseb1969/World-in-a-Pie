"""CASE-852 — an ingest work item is written with the producer's key, or not at all.

ingest-gateway has no HTTP ingest route: producers publish work items to
NATS, and the worker forwards each to def-store / template-store /
document-store. It used to forward every item with the install's master key,
so anything that could publish to NATS wrote documents, templates and terms
in any namespace with admin rights, bypassing every grant.

The contract: the producer puts its WIP API key in the message's X-API-Key
header; the worker forwards with exactly that key, so the target service
authorizes the write against the producer's grants. An item without a key
is refused — published to the results stream as a failed, unauthenticated
outcome and acknowledged — and nothing is written.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from ingest_gateway.models import (
    INGEST_API_KEY_HEADER,
    IngestAction,
    IngestResult,
    IngestResultStatus,
)


def _msg(headers: dict | None) -> MagicMock:
    msg = MagicMock()
    msg.subject = "wip.ingest.documents.create"
    msg.data = json.dumps({"correlation_id": "c-852", "payload": {"x": 1}}).encode()
    msg.headers = headers
    msg.metadata = MagicMock()
    msg.metadata.num_delivered = 1
    msg.ack = AsyncMock()
    msg.nak = AsyncMock()
    msg.term = AsyncMock()
    return msg


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [None, {}, {INGEST_API_KEY_HEADER: ""}, {INGEST_API_KEY_HEADER: "   "}])
async def test_an_item_without_a_key_is_refused_and_not_written(
    worker, mock_http_client, mock_result_publisher, headers
):
    msg = _msg(headers)
    await worker._process_message(msg)

    mock_http_client.forward_request.assert_not_awaited()
    result: IngestResult = mock_result_publisher.publish.call_args.args[0]
    assert result.status == IngestResultStatus.FAILED
    assert result.http_status_code == 401
    assert result.correlation_id == "c-852"
    assert INGEST_API_KEY_HEADER in (result.error or "")
    msg.ack.assert_awaited_once()  # definitive: redelivery cannot add a key
    assert worker.messages_failed == 1


@pytest.mark.asyncio
async def test_the_producers_key_is_what_gets_forwarded(worker, mock_http_client, mock_result_publisher):
    mock_http_client.forward_request = AsyncMock(return_value=IngestResult(
        correlation_id="c-852", action=IngestAction.DOCUMENTS_CREATE,
        status=IngestResultStatus.SUCCESS, http_status_code=200,
    ))
    await worker._process_message(_msg({INGEST_API_KEY_HEADER: "producer-secret"}))

    args = mock_http_client.forward_request.await_args.args
    assert args[3] == "producer-secret"


@pytest.mark.asyncio
async def test_http_client_sends_the_given_key(http_client):
    response = MagicMock(status_code=200)
    response.json.return_value = {"results": [], "total": 1, "succeeded": 1, "failed": 0}
    http_client._client.request = AsyncMock(return_value=response)

    await http_client.forward_request(
        IngestAction.DOCUMENTS_CREATE, {"x": 1}, "c", "producer-secret"
    )

    headers = http_client._client.request.call_args.kwargs["headers"]
    assert headers["X-API-Key"] == "producer-secret"


@pytest.mark.asyncio
async def test_a_refusal_by_the_target_is_definitive(worker, mock_http_client, mock_result_publisher):
    """The producer's key lacks the grant: the target's 403 is the outcome,
    published and acked — never retried, never escalated to another key."""
    mock_http_client.forward_request = AsyncMock(return_value=IngestResult(
        correlation_id="c-852", action=IngestAction.DOCUMENTS_CREATE,
        status=IngestResultStatus.FAILED, http_status_code=403, error="HTTP 403: forbidden",
    ))
    msg = _msg({INGEST_API_KEY_HEADER: "narrow-key"})
    await worker._process_message(msg)

    msg.ack.assert_awaited_once()
    msg.nak.assert_not_awaited()
    assert mock_http_client.forward_request.await_count == 1
    assert mock_result_publisher.publish.call_args.args[0].http_status_code == 403
