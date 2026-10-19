"""Data models for the Ingest Gateway service."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Base for caller-input models — rejects unknown fields."""
    model_config = ConfigDict(extra='forbid')


class IngestAction(StrEnum):
    """Types of ingest actions mapped from NATS subjects."""

    # Terminology operations
    TERMINOLOGIES_CREATE = "terminologies.create"

    # Term operations
    TERMS_BULK = "terms.bulk"

    # Template operations
    TEMPLATES_CREATE = "templates.create"
    TEMPLATES_BULK = "templates.bulk"

    # Document operations
    DOCUMENTS_CREATE = "documents.create"
    DOCUMENTS_BULK = "documents.bulk"


# NATS message header carrying the producer's WIP API key. The gateway
# forwards each work item with THIS key, so the target service applies the
# producer's own grants; a message without it is refused, never written with
# a key of the gateway's.
INGEST_API_KEY_HEADER = "X-API-Key"


# Map NATS subjects to actions
SUBJECT_TO_ACTION: dict[str, IngestAction] = {
    "wip.ingest.terminologies.create": IngestAction.TERMINOLOGIES_CREATE,
    "wip.ingest.terms.bulk": IngestAction.TERMS_BULK,
    "wip.ingest.templates.create": IngestAction.TEMPLATES_CREATE,
    "wip.ingest.templates.bulk": IngestAction.TEMPLATES_BULK,
    "wip.ingest.documents.create": IngestAction.DOCUMENTS_CREATE,
    "wip.ingest.documents.bulk": IngestAction.DOCUMENTS_BULK,
}


class IngestMessage(StrictModel):
    """Incoming ingest message structure."""

    correlation_id: str = Field(
        ...,
        description="Unique ID for tracking request/response"
    )
    payload: dict[str, Any] = Field(
        ...,
        description="Payload matching REST API request body"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional metadata"
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )


class IngestResultStatus(StrEnum):
    """Status of an ingest result."""

    SUCCESS = "success"
    PARTIAL = "partial"  # For bulk operations with some failures
    FAILED = "failed"


class IngestResult(BaseModel):
    """Result of an ingest operation, published to results stream."""

    correlation_id: str
    action: IngestAction
    status: IngestResultStatus
    http_status_code: int | None = None
    response: dict[str, Any] | None = None
    error: str | None = None
    processed_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    duration_ms: float = 0.0
    # The principal whose API key the item carried ("apikey:<name>"), or None
    # when it carried none or one the gateway does not know. Outcomes are
    # readable only by that principal (and admins), so it decides the
    # outcome's subject — see result_publisher.results_subject.
    submitted_by: str | None = None
    # True when the failure is transient (target unreachable, timeout, 5xx,
    # 429), so redelivering the message may succeed. Internal: not published.
    retryable: bool = False

    def model_dump_json_safe(self) -> dict[str, Any]:
        """Dump to JSON-serializable dict."""
        return {
            "correlation_id": self.correlation_id,
            "action": self.action.value,
            "status": self.status.value,
            "http_status_code": self.http_status_code,
            "response": self.response,
            "error": self.error,
            "processed_at": self.processed_at.isoformat(),
            "duration_ms": self.duration_ms,
            "submitted_by": self.submitted_by,
        }


class PublishedIngestResult(BaseModel):
    """An ingest outcome as published to the results stream and served by
    the results lookup."""

    correlation_id: str
    action: str
    status: str
    http_status_code: int | None = None
    response: dict[str, Any] | None = None
    error: str | None = None
    processed_at: str
    duration_ms: float
    submitted_by: str | None = None


class ResultLookupRequest(StrictModel):
    """Correlation IDs whose outcomes to look up."""

    correlation_ids: list[str] = Field(..., min_length=1, max_length=1000)


class ResultLookupItem(BaseModel):
    """One correlation ID's lookup: its outcome, or not_found."""

    correlation_id: str
    status: str = Field(..., description="'found' or 'not_found'")
    result: PublishedIngestResult | None = None


class ResultLookupResponse(BaseModel):
    """Per-ID lookups, in request order."""

    results: list[ResultLookupItem]
    total: int
    found: int


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    service: str
    version: str
    nats_connected: bool
    worker_running: bool
    details: dict[str, Any] = Field(default_factory=dict)


class StatusResponse(BaseModel):
    """Status response with processing statistics."""

    running: bool
    nats_connected: bool
    messages_processed: int
    messages_failed: int
    messages_retried: int = 0
    messages_gave_up: int = 0
    uptime_seconds: float


class MetricsResponse(BaseModel):
    """Detailed metrics response."""

    total_processed: int
    total_failed: int
    total_success: int
    by_action: dict[str, dict[str, int]]
    avg_duration_ms: float
    uptime_seconds: float
