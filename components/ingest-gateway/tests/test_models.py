"""Tests for ingest gateway data models."""

import pytest
from datetime import datetime, timezone

from ingest_gateway.models import (
    IngestAction,
    IngestMessage,
    IngestResult,
    IngestResultStatus,
    SUBJECT_TO_ACTION,
)


class TestIngestAction:
    def test_action_values(self):
        assert IngestAction.TERMINOLOGIES_CREATE == "terminologies.create"
        assert IngestAction.TERMS_BULK == "terms.bulk"
        assert IngestAction.TEMPLATES_CREATE == "templates.create"
        assert IngestAction.TEMPLATES_BULK == "templates.bulk"
        assert IngestAction.DOCUMENTS_CREATE == "documents.create"
        assert IngestAction.DOCUMENTS_BULK == "documents.bulk"

    def test_all_actions_have_endpoints(self):
        """Every action enum value should map to an endpoint config."""
        from ingest_gateway.http_client import ACTION_ENDPOINTS
        for action in IngestAction:
            assert action in ACTION_ENDPOINTS, f"{action} missing from ACTION_ENDPOINTS"


class TestSubjectMapping:
    def test_all_subjects_mapped(self):
        expected = {
            "wip.ingest.terminologies.create": IngestAction.TERMINOLOGIES_CREATE,
            "wip.ingest.terms.bulk": IngestAction.TERMS_BULK,
            "wip.ingest.templates.create": IngestAction.TEMPLATES_CREATE,
            "wip.ingest.templates.bulk": IngestAction.TEMPLATES_BULK,
            "wip.ingest.documents.create": IngestAction.DOCUMENTS_CREATE,
            "wip.ingest.documents.bulk": IngestAction.DOCUMENTS_BULK,
        }
        assert SUBJECT_TO_ACTION == expected

    def test_unknown_subject_returns_none(self):
        assert SUBJECT_TO_ACTION.get("wip.ingest.unknown") is None

    def test_every_action_has_a_subject(self):
        """Every action enum should be reachable from at least one subject."""
        mapped_actions = set(SUBJECT_TO_ACTION.values())
        for action in IngestAction:
            assert action in mapped_actions, f"{action} has no NATS subject mapping"


class TestIngestMessage:
    def test_valid_message(self):
        msg = IngestMessage(
            correlation_id="test-123",
            payload={"value": "TEST", "label": "Test"},
        )
        assert msg.correlation_id == "test-123"
        assert msg.payload == {"value": "TEST", "label": "Test"}
        assert msg.metadata == {}
        assert isinstance(msg.timestamp, datetime)

    def test_message_with_metadata(self):
        msg = IngestMessage(
            correlation_id="test-456",
            payload={"key": "value"},
            metadata={"source": "csv-upload"},
        )
        assert msg.metadata == {"source": "csv-upload"}

    def test_missing_correlation_id_fails(self):
        with pytest.raises(Exception):
            IngestMessage(payload={"key": "value"})

    def test_missing_payload_fails(self):
        with pytest.raises(Exception):
            IngestMessage(correlation_id="test-789")


class TestIngestResult:
    def test_success_result(self):
        result = IngestResult(
            correlation_id="test-1",
            action=IngestAction.TERMINOLOGIES_CREATE,
            status=IngestResultStatus.SUCCESS,
            http_status_code=200,
            response={"results": [{"index": 0, "status": "created", "id": "X"}]},
        )
        assert result.status == IngestResultStatus.SUCCESS
        assert result.error is None

    def test_failed_result(self):
        result = IngestResult(
            correlation_id="test-2",
            action=IngestAction.DOCUMENTS_CREATE,
            status=IngestResultStatus.FAILED,
            error="HTTP 422: validation error",
        )
        assert result.status == IngestResultStatus.FAILED
        assert result.error == "HTTP 422: validation error"

    def test_partial_result(self):
        result = IngestResult(
            correlation_id="test-3",
            action=IngestAction.TEMPLATES_BULK,
            status=IngestResultStatus.PARTIAL,
        )
        assert result.status == IngestResultStatus.PARTIAL

    def test_json_serialization(self):
        result = IngestResult(
            correlation_id="test-4",
            action=IngestAction.TEMPLATES_CREATE,
            status=IngestResultStatus.PARTIAL,
            http_status_code=200,
            duration_ms=42.5,
        )
        data = result.model_dump_json_safe()
        assert data["correlation_id"] == "test-4"
        assert data["action"] == "templates.create"
        assert data["status"] == "partial"
        assert data["http_status_code"] == 200
        assert data["duration_ms"] == 42.5
        assert "processed_at" in data

    def test_json_serialization_with_error(self):
        result = IngestResult(
            correlation_id="test-5",
            action=IngestAction.TERMINOLOGIES_CREATE,
            status=IngestResultStatus.FAILED,
            error="something broke",
        )
        data = result.model_dump_json_safe()
        assert data["error"] == "something broke"
        assert data["http_status_code"] is None
