"""
Data models for the Reporting Sync service.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """Types of events published to NATS."""

    # Document events
    DOCUMENT_CREATED = "document.created"
    DOCUMENT_UPDATED = "document.updated"
    DOCUMENT_DELETED = "document.deleted"

    # Template events
    TEMPLATE_CREATED = "template.created"
    TEMPLATE_UPDATED = "template.updated"
    TEMPLATE_DELETED = "template.deleted"


class SyncStrategy(str, Enum):
    """Sync strategy for a template."""

    LATEST_ONLY = "latest_only"  # UPSERT - one row per document_id
    ALL_VERSIONS = "all_versions"  # INSERT all versions
    DISABLED = "disabled"  # Don't sync to PostgreSQL


class DocumentEvent(BaseModel):
    """Event payload for document changes."""

    event_id: str
    event_type: EventType
    timestamp: datetime
    document: dict[str, Any] = Field(
        ..., description="Full document including data, term_references, metadata"
    )


class TemplateEvent(BaseModel):
    """Event payload for template changes."""

    event_id: str
    event_type: EventType
    timestamp: datetime
    template: dict[str, Any] = Field(..., description="Full template definition")


class ReportingConfig(BaseModel):
    """Reporting configuration for a template."""

    sync_enabled: bool = True
    sync_strategy: SyncStrategy = SyncStrategy.LATEST_ONLY
    table_name: str | None = None  # Auto-generated from template code if not set
    include_metadata: bool = True
    flatten_arrays: bool = True
    max_array_elements: int = 10


class FieldType(str, Enum):
    """Field types from template definitions."""

    STRING = "string"
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    TERM = "term"
    OBJECT = "object"
    ARRAY = "array"


class TemplateField(BaseModel):
    """Field definition from a template."""

    name: str
    label: str | None = None
    type: FieldType
    mandatory: bool = False
    terminology_ref: str | None = None
    template_ref: str | None = None
    array_item_type: FieldType | None = None
    array_terminology_ref: str | None = None
    array_template_ref: str | None = None


class SyncStatus(BaseModel):
    """Status of the sync worker."""

    running: bool
    connected_to_nats: bool
    connected_to_postgres: bool
    last_event_processed: datetime | None = None
    events_processed: int = 0
    events_failed: int = 0
    tables_managed: int = 0


class HealthResponse(BaseModel):
    """Health check response."""

    status: str  # "healthy", "degraded", "unhealthy"
    service: str
    version: str
    nats_connected: bool
    postgres_connected: bool
    details: dict[str, Any] = Field(default_factory=dict)


class BatchSyncStatus(str, Enum):
    """Status of a batch sync job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BatchSyncJob(BaseModel):
    """Batch sync job status."""

    job_id: str
    template_code: str
    status: BatchSyncStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    total_documents: int = 0
    documents_synced: int = 0
    documents_failed: int = 0
    current_page: int = 0
    error_message: str | None = None


class BatchSyncRequest(BaseModel):
    """Request to start a batch sync."""

    template_code: str | None = None  # None = all templates
    force: bool = False  # Force re-sync even if table has data
    page_size: int = Field(default=100, ge=10, le=1000)


class BatchSyncResponse(BaseModel):
    """Response from starting a batch sync."""

    job_id: str
    template_code: str
    status: BatchSyncStatus
    message: str


# =============================================================================
# MONITORING & METRICS MODELS
# =============================================================================


class PerTemplateStats(BaseModel):
    """Statistics for a specific template."""

    template_code: str
    table_name: str
    documents_synced: int = 0
    documents_failed: int = 0
    last_sync_at: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None


class ConsumerInfo(BaseModel):
    """NATS consumer information."""

    stream_name: str
    consumer_name: str
    pending_messages: int = 0
    pending_bytes: int = 0
    delivered_messages: int = 0
    ack_pending: int = 0
    redelivered: int = 0
    last_delivered: datetime | None = None


class LatencyStats(BaseModel):
    """Latency statistics."""

    sample_count: int = 0
    min_ms: float = 0.0
    max_ms: float = 0.0
    avg_ms: float = 0.0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0


class MetricsResponse(BaseModel):
    """Comprehensive metrics response."""

    # Uptime
    started_at: datetime
    uptime_seconds: float

    # Connection status
    nats_connected: bool
    postgres_connected: bool

    # Event processing
    events_processed: int = 0
    events_failed: int = 0
    events_per_second: float = 0.0

    # Queue info
    consumer_info: ConsumerInfo | None = None

    # Latency
    processing_latency: LatencyStats = Field(default_factory=LatencyStats)

    # Per-template stats
    template_stats: list[PerTemplateStats] = Field(default_factory=list)

    # Error breakdown
    errors_by_type: dict[str, int] = Field(default_factory=dict)


class AlertSeverity(str, Enum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertType(str, Enum):
    """Types of alerts."""

    QUEUE_LAG = "queue_lag"
    ERROR_RATE = "error_rate"
    PROCESSING_STALLED = "processing_stalled"
    CONNECTION_LOST = "connection_lost"


class Alert(BaseModel):
    """An active alert."""

    alert_id: str
    alert_type: AlertType
    severity: AlertSeverity
    message: str
    triggered_at: datetime
    resolved_at: datetime | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class AlertThresholds(BaseModel):
    """Configurable alert thresholds."""

    # Queue lag alert
    queue_lag_warning: int = Field(default=100, description="Pending messages for warning")
    queue_lag_critical: int = Field(default=1000, description="Pending messages for critical")

    # Error rate alert (errors per minute)
    error_rate_warning: float = Field(default=5.0, description="Errors/min for warning")
    error_rate_critical: float = Field(default=20.0, description="Errors/min for critical")

    # Processing stalled (seconds since last event)
    stall_warning_seconds: int = Field(default=300, description="Seconds for stall warning")
    stall_critical_seconds: int = Field(default=600, description="Seconds for stall critical")


class AlertConfig(BaseModel):
    """Alert configuration."""

    enabled: bool = True
    check_interval_seconds: int = Field(default=30, description="How often to check alerts")
    thresholds: AlertThresholds = Field(default_factory=AlertThresholds)
    webhook_url: str | None = Field(default=None, description="Webhook URL for notifications")
    webhook_headers: dict[str, str] = Field(default_factory=dict)


class AlertsResponse(BaseModel):
    """Response containing alerts and config."""

    config: AlertConfig
    active_alerts: list[Alert] = Field(default_factory=list)
    resolved_alerts: list[Alert] = Field(default_factory=list)
