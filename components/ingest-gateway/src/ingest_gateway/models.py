"""Data models for the Ingest Gateway service."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class IngestAction(str, Enum):
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


# Map NATS subjects to actions
SUBJECT_TO_ACTION: dict[str, IngestAction] = {
    "wip.ingest.terminologies.create": IngestAction.TERMINOLOGIES_CREATE,
    "wip.ingest.terms.bulk": IngestAction.TERMS_BULK,
    "wip.ingest.templates.create": IngestAction.TEMPLATES_CREATE,
    "wip.ingest.templates.bulk": IngestAction.TEMPLATES_BULK,
    "wip.ingest.documents.create": IngestAction.DOCUMENTS_CREATE,
    "wip.ingest.documents.bulk": IngestAction.DOCUMENTS_BULK,
}


class IngestMessage(BaseModel):
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
        default_factory=lambda: datetime.now(timezone.utc)
    )


class IngestResultStatus(str, Enum):
    """Status of an ingest result."""

    SUCCESS = "success"
    PARTIAL = "partial"  # For bulk operations with some failures
    FAILED = "failed"


class IngestResult(BaseModel):
    """Result of an ingest operation, published to results stream."""

    correlation_id: str
    action: IngestAction
    status: IngestResultStatus
    http_status_code: Optional[int] = None
    response: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    processed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    duration_ms: float = 0.0

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
        }


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
    uptime_seconds: float


class MetricsResponse(BaseModel):
    """Detailed metrics response."""

    total_processed: int
    total_failed: int
    total_success: int
    by_action: dict[str, dict[str, int]]
    avg_duration_ms: float
    uptime_seconds: float
