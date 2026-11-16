"""Every outcome the gateway publishes fits one NATS message.

- A target's 2xx answer is kept whole up to OUTCOME_BODY_MAX of JSON. A bulk
  answer over it keeps its counts exact and its items reduced to index,
  status, id, error_code, document_id and version, listed in order while they
  fit; results_truncated says whether any were left out. A 200 whose items all
  failed (classed FAILED) is bounded the same way.
- An outcome still over the connection's max_payload is published with its
  answer cut to counts, never handed back to be retried: the items were
  written, and only a published outcome tells the producer so.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from nats.errors import MaxPayloadError

from ingest_gateway import worker as worker_module
from ingest_gateway.http_client import IngestHTTPClient
from ingest_gateway.models import IngestAction, IngestResult, IngestResultStatus
from ingest_gateway.result_publisher import ResultPublisher
from tests.conftest import make_mock_response
from tests.test_case_1206_outcomes import _msg, _worker

ONE_MB = 1024 * 1024
# The 2xx answer bound: half of NATS's default max_payload.
BUDGET = 512 * 1024


@pytest.fixture(autouse=True)
def no_publish_backoff(monkeypatch):
    monkeypatch.setattr(worker_module, "PUBLISH_RETRY_DELAYS", (0, 0))


class _Bus:
    """A JetStream context that refuses a payload over its connection's
    max_payload, as nats-py does on the client side."""

    def __init__(self, max_payload: int = ONE_MB):
        self._nc = MagicMock()
        self._nc.max_payload = max_payload
        self.published: list[bytes] = []
        self.attempts = 0

    async def publish(self, subject: str, payload: bytes) -> Any:
        self.attempts += 1
        if len(payload) > self._nc.max_payload:
            raise MaxPayloadError
        self.published.append(payload)
        ack = MagicMock()
        ack.seq = len(self.published)
        return ack


def _terms_answer(n: int) -> dict[str, Any]:
    results = [
        {
            "index": i, "status": "created",
            "id": f"0190b000-0000-7000-0000-{i:012d}",
            "value": f"term-{i}", "label": f"Term number {i}",
        }
        for i in range(n)
    ]
    return {"results": results, "total": n, "succeeded": n, "failed": 0}


def _all_failed_documents_answer(n: int) -> dict[str, Any]:
    results = [
        {
            "index": i, "status": "error", "error_code": "validation_failed",
            "error": "Document failed validation: " + "x" * 400,
            "details": {"errors": [{"field": f"f{j}", "message": "y" * 100} for j in range(5)]},
        }
        for i in range(n)
    ]
    return {"results": results, "total": n, "succeeded": 0, "failed": n}


def _outcome(status_code: int, body: dict[str, Any]) -> IngestResult:
    return IngestHTTPClient._outcome(
        IngestAction.TERMS_BULK, "c-1399", make_mock_response(status_code, json_data=body),
    )


def _size(response: dict[str, Any] | None) -> int:
    return len(json.dumps(response))


def test_a_large_success_answer_is_bounded_with_exact_counts():
    body = _terms_answer(8000)
    assert _size(body) > ONE_MB

    result = _outcome(200, body)

    assert result.status == IngestResultStatus.SUCCESS
    response = result.response
    assert _size(response) <= BUDGET
    assert (response["total"], response["succeeded"], response["failed"]) == (8000, 8000, 0)
    assert response["results_truncated"] is True
    listed = response["results"]
    assert 0 < len(listed) < 8000
    assert [item["index"] for item in listed] == list(range(len(listed)))
    assert listed[0] == {
        "index": 0, "status": "created", "id": "0190b000-0000-7000-0000-000000000000",
    }


@pytest.mark.asyncio
async def test_a_large_success_outcome_is_published():
    bus = _Bus()
    result = _outcome(200, _terms_answer(8000))

    assert await ResultPublisher(bus).publish(result)

    published = json.loads(bus.published[0])
    assert published["status"] == "success"
    assert published["response"]["total"] == 8000


def test_an_answer_whose_reduced_items_all_fit_lists_every_one():
    body = _terms_answer(3000)
    for item in body["results"]:
        item["label"] = "z" * 200
    assert _size(body) > BUDGET

    response = _outcome(200, body).response

    assert response["results_truncated"] is False
    assert len(response["results"]) == 3000
    assert set(response["results"][0]) == {"index", "status", "id"}


def test_an_all_failed_200_is_bounded():
    body = _all_failed_documents_answer(1000)
    assert _size(body) > ONE_MB

    result = _outcome(200, body)

    assert result.status == IngestResultStatus.FAILED
    response = result.response
    assert _size(response) <= BUDGET
    assert (response["total"], response["succeeded"], response["failed"]) == (1000, 0, 1000)
    assert response["results"][0] == {"index": 0, "status": "error", "error_code": "validation_failed"}


def test_a_success_answer_within_the_bound_is_kept_whole():
    body = _terms_answer(10)
    assert _outcome(200, body).response == body


def test_a_large_answer_that_is_not_a_bulk_answer_keeps_its_start():
    body = {"payload": "p" * ONE_MB}
    response = _outcome(200, body).response
    assert response["truncated"] is True
    assert len(response["raw"]) == BUDGET


@pytest.mark.asyncio
async def test_an_outcome_over_max_payload_is_published_with_counts_only():
    bus = _Bus(max_payload=4096)
    result = _outcome(200, _terms_answer(100))

    assert await ResultPublisher(bus).publish(result)

    assert bus.attempts == 1
    published = json.loads(bus.published[0])
    assert published["correlation_id"] == "c-1399"
    assert published["response"] == {
        "total": 100, "succeeded": 100, "failed": 0,
        "results_truncated": True, "results": [],
    }


@pytest.mark.asyncio
async def test_an_oversize_outcome_is_settled_without_retry(mock_http_client):
    bus = _Bus(max_payload=4096)
    mock_http_client.forward_request = AsyncMock(return_value=_outcome(200, _terms_answer(100)))
    w = _worker(mock_http_client, ResultPublisher(bus), lambda _k: "apikey:p")

    msg = _msg()
    await w._process_message(msg)

    msg.ack.assert_awaited_once()
    msg.nak.assert_not_awaited()
    msg.term.assert_not_awaited()
    assert bus.attempts == 1
    assert (w.messages_processed, w.messages_failed) == (1, 0)
