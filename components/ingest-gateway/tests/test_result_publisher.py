"""Tests for the ResultPublisher that publishes ingest results to NATS."""

import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from ingest_gateway.models import IngestAction, IngestResult, IngestResultStatus
from ingest_gateway.result_publisher import ResultPublisher


@pytest.fixture
def mock_jetstream():
    """Create a mock JetStream context."""
    js = MagicMock()
    ack = MagicMock()
    ack.seq = 42
    js.publish = AsyncMock(return_value=ack)
    return js


@pytest.fixture
def publisher(mock_jetstream):
    """Create a ResultPublisher with mocked JetStream."""
    return ResultPublisher(mock_jetstream)


def _make_result(
    correlation_id: str = "corr-001",
    action: IngestAction = IngestAction.TERMINOLOGIES_CREATE,
    status: IngestResultStatus = IngestResultStatus.SUCCESS,
    http_status_code: int | None = 200,
    response: dict | None = None,
    error: str | None = None,
    duration_ms: float = 15.5,
) -> IngestResult:
    return IngestResult(
        correlation_id=correlation_id,
        action=action,
        status=status,
        http_status_code=http_status_code,
        response=response,
        error=error,
        duration_ms=duration_ms,
    )


# ---- Publish success ----


class TestPublishSuccess:

    @pytest.mark.asyncio
    async def test_publish_success_result(self, publisher, mock_jetstream):
        """A successful result is published and returns True."""
        result = _make_result(
            status=IngestResultStatus.SUCCESS,
            http_status_code=200,
            response={"results": [{"index": 0, "status": "created", "id": "0190b000-0000-7000-0000-000000000001"}]},
        )

        ok = await publisher.publish(result)

        assert ok is True
        mock_jetstream.publish.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_publish_success_payload_is_json(self, publisher, mock_jetstream):
        """Published payload is valid JSON containing the result data."""
        result = _make_result(
            correlation_id="corr-json",
            status=IngestResultStatus.SUCCESS,
            http_status_code=200,
        )

        await publisher.publish(result)

        call_args = mock_jetstream.publish.call_args
        payload_bytes = call_args.args[1]
        payload = json.loads(payload_bytes)

        assert payload["correlation_id"] == "corr-json"
        assert payload["action"] == "terminologies.create"
        assert payload["status"] == "success"
        assert payload["http_status_code"] == 200

    @pytest.mark.asyncio
    async def test_publish_success_includes_response_body(self, publisher, mock_jetstream):
        """The full response dict is included in the published payload."""
        response_data = {
            "results": [
                {"index": 0, "status": "created", "id": "0190b000-0000-7000-0000-000000000100"},
                {"index": 1, "status": "created", "id": "0190b000-0000-7000-0000-000000000101"},
            ],
            "total": 2,
            "succeeded": 2,
            "failed": 0,
        }
        result = _make_result(response=response_data)

        await publisher.publish(result)

        payload = json.loads(mock_jetstream.publish.call_args.args[1])
        assert payload["response"] == response_data


# ---- Publish failed ----


class TestPublishFailed:

    @pytest.mark.asyncio
    async def test_publish_failed_result_with_error(self, publisher, mock_jetstream):
        """A failed result includes the error string in the payload."""
        result = _make_result(
            correlation_id="corr-fail",
            status=IngestResultStatus.FAILED,
            http_status_code=422,
            error="HTTP 422: Validation error - 'value' is required",
        )

        ok = await publisher.publish(result)

        assert ok is True
        payload = json.loads(mock_jetstream.publish.call_args.args[1])
        assert payload["status"] == "failed"
        assert payload["error"] == "HTTP 422: Validation error - 'value' is required"
        assert payload["http_status_code"] == 422

    @pytest.mark.asyncio
    async def test_publish_failed_result_without_http_status(self, publisher, mock_jetstream):
        """A failed result can have no HTTP status (e.g., connection error)."""
        result = _make_result(
            status=IngestResultStatus.FAILED,
            http_status_code=None,
            error="Connection refused",
        )

        await publisher.publish(result)

        payload = json.loads(mock_jetstream.publish.call_args.args[1])
        assert payload["status"] == "failed"
        assert payload["http_status_code"] is None
        assert payload["error"] == "Connection refused"


# ---- Publish partial ----


class TestPublishPartial:

    @pytest.mark.asyncio
    async def test_publish_partial_result(self, publisher, mock_jetstream):
        """A partial result (some items failed in bulk) is published correctly."""
        result = _make_result(
            correlation_id="corr-partial",
            action=IngestAction.DOCUMENTS_BULK,
            status=IngestResultStatus.PARTIAL,
            http_status_code=200,
            response={
                "results": [
                    {"index": 0, "status": "created", "id": "D-001"},
                    {"index": 1, "status": "error", "error": "duplicate"},
                ],
                "total": 2,
                "succeeded": 1,
                "failed": 1,
            },
        )

        ok = await publisher.publish(result)

        assert ok is True
        payload = json.loads(mock_jetstream.publish.call_args.args[1])
        assert payload["status"] == "partial"
        assert payload["action"] == "documents.bulk"
        assert payload["response"]["succeeded"] == 1
        assert payload["response"]["failed"] == 1


# ---- Correlation ID ----


class TestCorrelationId:

    @pytest.mark.asyncio
    async def test_result_includes_correlation_id_in_payload(self, publisher, mock_jetstream):
        """The published payload includes the original correlation_id."""
        result = _make_result(correlation_id="track-abc-123")

        await publisher.publish(result)

        payload = json.loads(mock_jetstream.publish.call_args.args[1])
        assert payload["correlation_id"] == "track-abc-123"

    @pytest.mark.asyncio
    async def test_correlation_id_in_subject(self, publisher, mock_jetstream):
        """The NATS subject includes the correlation_id for filtering."""
        result = _make_result(correlation_id="track-xyz-789")

        await publisher.publish(result)

        subject = mock_jetstream.publish.call_args.args[0]
        assert "track-xyz-789" in subject


# ---- Duration ----


class TestDuration:

    @pytest.mark.asyncio
    async def test_result_includes_duration_ms(self, publisher, mock_jetstream):
        """The published payload includes duration_ms."""
        result = _make_result(duration_ms=123.45)

        await publisher.publish(result)

        payload = json.loads(mock_jetstream.publish.call_args.args[1])
        assert payload["duration_ms"] == 123.45

    @pytest.mark.asyncio
    async def test_result_zero_duration(self, publisher, mock_jetstream):
        """Default duration_ms of 0.0 is included in the payload."""
        result = _make_result(duration_ms=0.0)

        await publisher.publish(result)

        payload = json.loads(mock_jetstream.publish.call_args.args[1])
        assert payload["duration_ms"] == 0.0


# ---- NATS publish failure ----


class TestNatsPublishFailure:

    @pytest.mark.asyncio
    async def test_nats_error_returns_false(self, publisher, mock_jetstream):
        """When NATS publish raises, publish returns False."""
        mock_jetstream.publish = AsyncMock(side_effect=Exception("NATS timeout"))
        result = _make_result(correlation_id="corr-nats-fail")

        ok = await publisher.publish(result)

        assert ok is False

    @pytest.mark.asyncio
    async def test_nats_error_increments_failed_count(self, publisher, mock_jetstream):
        """A NATS failure increments the failed_count counter."""
        mock_jetstream.publish = AsyncMock(side_effect=Exception("connection lost"))
        result = _make_result()

        assert publisher.failed_count == 0
        await publisher.publish(result)
        assert publisher.failed_count == 1

    @pytest.mark.asyncio
    async def test_nats_error_does_not_increment_published(self, publisher, mock_jetstream):
        """A NATS failure does NOT increment the published_count counter."""
        mock_jetstream.publish = AsyncMock(side_effect=Exception("connection lost"))
        result = _make_result()

        await publisher.publish(result)
        assert publisher.published_count == 0

    @pytest.mark.asyncio
    async def test_nats_error_does_not_propagate(self, publisher, mock_jetstream):
        """NATS exceptions are caught internally and do not propagate."""
        mock_jetstream.publish = AsyncMock(side_effect=RuntimeError("unexpected"))
        result = _make_result()

        # Should not raise
        ok = await publisher.publish(result)
        assert ok is False


# ---- Subject naming convention ----


class TestSubjectNaming:

    @pytest.mark.asyncio
    async def test_subject_format(self, publisher, mock_jetstream):
        """Subject follows the pattern wip.ingest.results.<correlation_id>."""
        result = _make_result(correlation_id="abc-def-123")

        await publisher.publish(result)

        subject = mock_jetstream.publish.call_args.args[0]
        assert subject == "wip.ingest.results.abc-def-123"

    @pytest.mark.asyncio
    async def test_subject_varies_by_correlation_id(self, publisher, mock_jetstream):
        """Different correlation IDs produce different subjects."""
        result_a = _make_result(correlation_id="id-aaa")
        result_b = _make_result(correlation_id="id-bbb")

        await publisher.publish(result_a)
        subject_a = mock_jetstream.publish.call_args.args[0]

        await publisher.publish(result_b)
        subject_b = mock_jetstream.publish.call_args.args[0]

        assert subject_a == "wip.ingest.results.id-aaa"
        assert subject_b == "wip.ingest.results.id-bbb"
        assert subject_a != subject_b

    @pytest.mark.asyncio
    async def test_subject_prefix_is_consistent(self, publisher, mock_jetstream):
        """All result subjects share the wip.ingest.results. prefix."""
        for cid in ["one", "two", "three"]:
            result = _make_result(correlation_id=cid)
            await publisher.publish(result)

            subject = mock_jetstream.publish.call_args.args[0]
            assert subject.startswith("wip.ingest.results.")


# ---- Counters ----


class TestPublisherCounters:

    @pytest.mark.asyncio
    async def test_initial_counts_are_zero(self, publisher):
        """A fresh publisher starts with zero counters."""
        assert publisher.published_count == 0
        assert publisher.failed_count == 0

    @pytest.mark.asyncio
    async def test_successful_publish_increments_published(self, publisher, mock_jetstream):
        """Each successful publish increments published_count."""
        for i in range(3):
            result = _make_result(correlation_id=f"count-{i}")
            await publisher.publish(result)

        assert publisher.published_count == 3
        assert publisher.failed_count == 0

    @pytest.mark.asyncio
    async def test_mixed_success_and_failure(self, publisher, mock_jetstream):
        """Counters track successes and failures independently."""
        # Two successes
        await publisher.publish(_make_result(correlation_id="ok-1"))
        await publisher.publish(_make_result(correlation_id="ok-2"))

        # One failure
        mock_jetstream.publish = AsyncMock(side_effect=Exception("down"))
        await publisher.publish(_make_result(correlation_id="fail-1"))

        assert publisher.published_count == 2
        assert publisher.failed_count == 1
