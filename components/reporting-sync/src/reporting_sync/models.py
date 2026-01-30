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
