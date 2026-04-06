"""Tests for ingest gateway worker.

Covers message routing, format handling, error handling, and counters.
"""

import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from ingest_gateway.models import IngestAction, IngestResult, IngestResultStatus


def make_nats_msg(subject: str, data: dict) -> MagicMock:
    """Create a mock NATS message."""
    msg = MagicMock()
    msg.subject = subject
    msg.data = json.dumps(data).encode()
    msg.metadata = MagicMock()
    msg.metadata.sequence.stream = 1
    msg.ack = AsyncMock()
    msg.nak = AsyncMock()
    return msg


def _success_result(correlation_id: str, action: IngestAction) -> IngestResult:
    return IngestResult(
        correlation_id=correlation_id,
        action=action,
        status=IngestResultStatus.SUCCESS,
        http_status_code=200,
    )


def _failed_result(correlation_id: str, action: IngestAction, error: str) -> IngestResult:
    return IngestResult(
        correlation_id=correlation_id,
        action=action,
        status=IngestResultStatus.FAILED,
        error=error,
    )


# ---- Message format handling ----

class TestMessageFormats:

    @pytest.mark.asyncio
    async def test_wrapped_format(self, worker, mock_http_client, mock_result_publisher):
        """Standard {correlation_id, payload} format is unwrapped."""
        mock_http_client.forward_request = AsyncMock(
            return_value=_success_result("c-1", IngestAction.TERMINOLOGIES_CREATE)
        )

        msg = make_nats_msg("wip.ingest.terminologies.create", {
            "correlation_id": "c-1",
            "payload": {"value": "GENDER", "label": "Gender"},
        })
        await worker._process_message(msg)

        mock_http_client.forward_request.assert_awaited_once_with(
            IngestAction.TERMINOLOGIES_CREATE,
            {"value": "GENDER", "label": "Gender"},
            "c-1",
        )
        msg.ack.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_direct_payload_format(self, worker, mock_http_client, mock_result_publisher):
        """Messages without wrapper use entire data as payload with auto correlation_id."""
        mock_http_client.forward_request = AsyncMock(
            return_value=_success_result("auto-x", IngestAction.TERMINOLOGIES_CREATE)
        )

        msg = make_nats_msg("wip.ingest.terminologies.create", {
            "value": "COUNTRY",
            "label": "Country",
        })
        await worker._process_message(msg)

        call_args = mock_http_client.forward_request.call_args
        assert call_args.args[0] == IngestAction.TERMINOLOGIES_CREATE
        assert call_args.args[1] == {"value": "COUNTRY", "label": "Country"}
        # Auto-generated correlation_id
        assert call_args.args[2].startswith("auto-")

    @pytest.mark.asyncio
    async def test_direct_format_with_correlation_id(self, worker, mock_http_client, mock_result_publisher):
        """Direct payload with a correlation_id field uses it (not auto-generated)."""
        mock_http_client.forward_request = AsyncMock(
            return_value=_success_result("my-id", IngestAction.TERMINOLOGIES_CREATE)
        )

        msg = make_nats_msg("wip.ingest.terminologies.create", {
            "correlation_id": "my-id",
            "value": "STATUS",
            "label": "Status",
        })
        await worker._process_message(msg)

        call_args = mock_http_client.forward_request.call_args
        # Entire data (including correlation_id) is the payload
        assert call_args.args[1] == {
            "correlation_id": "my-id",
            "value": "STATUS",
            "label": "Status",
        }


# ---- Subject routing ----

class TestSubjectRouting:

    @pytest.mark.asyncio
    async def test_terminologies_create(self, worker, mock_http_client, mock_result_publisher):
        mock_http_client.forward_request = AsyncMock(
            return_value=_success_result("c-10", IngestAction.TERMINOLOGIES_CREATE)
        )
        msg = make_nats_msg("wip.ingest.terminologies.create", {
            "correlation_id": "c-10",
            "payload": {"value": "X", "label": "X"},
        })
        await worker._process_message(msg)
        assert mock_http_client.forward_request.call_args.args[0] == IngestAction.TERMINOLOGIES_CREATE

    @pytest.mark.asyncio
    async def test_terms_bulk(self, worker, mock_http_client, mock_result_publisher):
        mock_http_client.forward_request = AsyncMock(
            return_value=_success_result("c-11", IngestAction.TERMS_BULK)
        )
        msg = make_nats_msg("wip.ingest.terms.bulk", {
            "correlation_id": "c-11",
            "payload": {"terminology_id": "0190b000-0000-7000-0000-000000000001", "terms": []},
        })
        await worker._process_message(msg)
        assert mock_http_client.forward_request.call_args.args[0] == IngestAction.TERMS_BULK

    @pytest.mark.asyncio
    async def test_templates_create(self, worker, mock_http_client, mock_result_publisher):
        mock_http_client.forward_request = AsyncMock(
            return_value=_success_result("c-12", IngestAction.TEMPLATES_CREATE)
        )
        msg = make_nats_msg("wip.ingest.templates.create", {
            "correlation_id": "c-12",
            "payload": {"value": "TPL", "label": "T", "fields": []},
        })
        await worker._process_message(msg)
        assert mock_http_client.forward_request.call_args.args[0] == IngestAction.TEMPLATES_CREATE

    @pytest.mark.asyncio
    async def test_templates_bulk(self, worker, mock_http_client, mock_result_publisher):
        mock_http_client.forward_request = AsyncMock(
            return_value=_success_result("c-13", IngestAction.TEMPLATES_BULK)
        )
        msg = make_nats_msg("wip.ingest.templates.bulk", {
            "correlation_id": "c-13",
            "payload": {"templates": []},
        })
        await worker._process_message(msg)
        assert mock_http_client.forward_request.call_args.args[0] == IngestAction.TEMPLATES_BULK

    @pytest.mark.asyncio
    async def test_documents_create(self, worker, mock_http_client, mock_result_publisher):
        mock_http_client.forward_request = AsyncMock(
            return_value=_success_result("c-14", IngestAction.DOCUMENTS_CREATE)
        )
        msg = make_nats_msg("wip.ingest.documents.create", {
            "correlation_id": "c-14",
            "payload": {"template_id": "T", "data": {}},
        })
        await worker._process_message(msg)
        assert mock_http_client.forward_request.call_args.args[0] == IngestAction.DOCUMENTS_CREATE

    @pytest.mark.asyncio
    async def test_documents_bulk(self, worker, mock_http_client, mock_result_publisher):
        mock_http_client.forward_request = AsyncMock(
            return_value=_success_result("c-15", IngestAction.DOCUMENTS_BULK)
        )
        msg = make_nats_msg("wip.ingest.documents.bulk", {
            "correlation_id": "c-15",
            "payload": {"documents": []},
        })
        await worker._process_message(msg)
        assert mock_http_client.forward_request.call_args.args[0] == IngestAction.DOCUMENTS_BULK


# ---- Error handling ----

class TestErrorHandling:

    @pytest.mark.asyncio
    async def test_unknown_subject_acks_without_forwarding(self, worker, mock_http_client):
        """Unknown subjects are acked immediately, never forwarded."""
        msg = make_nats_msg("wip.ingest.unknown.action", {
            "correlation_id": "c-20",
            "payload": {},
        })
        await worker._process_message(msg)

        mock_http_client.forward_request.assert_not_called()
        msg.ack.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invalid_json_acks_and_publishes_error(self, worker, mock_http_client, mock_result_publisher):
        """Invalid JSON is acked (no retry) and error result published."""
        msg = MagicMock()
        msg.subject = "wip.ingest.terminologies.create"
        msg.data = b"not valid json{{"
        msg.ack = AsyncMock()
        msg.nak = AsyncMock()

        await worker._process_message(msg)

        msg.ack.assert_awaited_once()
        msg.nak.assert_not_awaited()
        mock_http_client.forward_request.assert_not_called()

        # Error result published
        mock_result_publisher.publish.assert_awaited_once()
        published = mock_result_publisher.publish.call_args.args[0]
        assert published.status == IngestResultStatus.FAILED
        assert "Invalid JSON" in published.error

    @pytest.mark.asyncio
    async def test_unexpected_exception_naks_for_retry(self, worker, mock_http_client, mock_result_publisher):
        """Unexpected exceptions NAK the message for retry."""
        mock_http_client.forward_request = AsyncMock(
            side_effect=RuntimeError("boom")
        )

        msg = make_nats_msg("wip.ingest.terminologies.create", {
            "correlation_id": "c-21",
            "payload": {"value": "X", "label": "X"},
        })
        await worker._process_message(msg)

        msg.nak.assert_awaited_once()
        msg.ack.assert_not_awaited()


# ---- Counters ----

class TestCounters:

    @pytest.mark.asyncio
    async def test_success_increments_processed(self, worker, mock_http_client, mock_result_publisher):
        mock_http_client.forward_request = AsyncMock(
            return_value=_success_result("c-30", IngestAction.TERMINOLOGIES_CREATE)
        )

        msg = make_nats_msg("wip.ingest.terminologies.create", {
            "correlation_id": "c-30",
            "payload": {"value": "X", "label": "X"},
        })

        assert worker.messages_processed == 0
        assert worker.messages_failed == 0

        await worker._process_message(msg)

        assert worker.messages_processed == 1
        assert worker.messages_failed == 0

    @pytest.mark.asyncio
    async def test_failure_increments_both(self, worker, mock_http_client, mock_result_publisher):
        mock_http_client.forward_request = AsyncMock(
            return_value=_failed_result("c-31", IngestAction.TERMINOLOGIES_CREATE, "bad")
        )

        msg = make_nats_msg("wip.ingest.terminologies.create", {
            "correlation_id": "c-31",
            "payload": {"value": "X", "label": "X"},
        })
        await worker._process_message(msg)

        assert worker.messages_processed == 1
        assert worker.messages_failed == 1

    @pytest.mark.asyncio
    async def test_json_error_increments_both(self, worker, mock_http_client, mock_result_publisher):
        msg = MagicMock()
        msg.subject = "wip.ingest.terminologies.create"
        msg.data = b"bad json"
        msg.ack = AsyncMock()
        msg.nak = AsyncMock()

        await worker._process_message(msg)

        assert worker.messages_processed == 1
        assert worker.messages_failed == 1

    @pytest.mark.asyncio
    async def test_multiple_messages_accumulate(self, worker, mock_http_client, mock_result_publisher):
        """Counters accumulate across multiple messages."""
        mock_http_client.forward_request = AsyncMock(
            return_value=_success_result("c-32", IngestAction.TERMINOLOGIES_CREATE)
        )

        for i in range(5):
            msg = make_nats_msg("wip.ingest.terminologies.create", {
                "correlation_id": f"c-32-{i}",
                "payload": {"value": f"T{i}", "label": f"T{i}"},
            })
            await worker._process_message(msg)

        assert worker.messages_processed == 5
        assert worker.messages_failed == 0


# ---- Result publishing ----

class TestResultPublishing:

    @pytest.mark.asyncio
    async def test_result_published_with_duration(self, worker, mock_http_client, mock_result_publisher):
        mock_http_client.forward_request = AsyncMock(
            return_value=_success_result("c-40", IngestAction.DOCUMENTS_CREATE)
        )

        msg = make_nats_msg("wip.ingest.documents.create", {
            "correlation_id": "c-40",
            "payload": {"template_id": "T", "data": {}},
        })
        await worker._process_message(msg)

        mock_result_publisher.publish.assert_awaited_once()
        published = mock_result_publisher.publish.call_args.args[0]
        assert published.correlation_id == "c-40"
        assert published.duration_ms > 0

    @pytest.mark.asyncio
    async def test_result_preserves_status(self, worker, mock_http_client, mock_result_publisher):
        """The http_client result status flows through to the published result."""
        mock_http_client.forward_request = AsyncMock(return_value=IngestResult(
            correlation_id="c-41",
            action=IngestAction.TERMINOLOGIES_CREATE,
            status=IngestResultStatus.PARTIAL,
            http_status_code=200,
        ))

        msg = make_nats_msg("wip.ingest.terminologies.create", {
            "correlation_id": "c-41",
            "payload": {"value": "X", "label": "X"},
        })
        await worker._process_message(msg)

        published = mock_result_publisher.publish.call_args.args[0]
        assert published.status == IngestResultStatus.PARTIAL
