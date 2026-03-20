"""Replay session model."""

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


class ReplayStatus(str, Enum):
    """Replay session status."""
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ReplayFilter(BaseModel):
    """Filter for replay — which documents to replay."""
    template_id: str | None = None
    template_value: str | None = None
    namespace: str = "wip"
    status: str = "active"


class ReplayRequest(BaseModel):
    """Request to start a replay session."""
    filter: ReplayFilter = Field(default_factory=ReplayFilter)
    throttle_ms: int = Field(default=10, ge=0, le=5000, description="Delay between events in milliseconds")
    batch_size: int = Field(default=100, ge=10, le=1000, description="Documents per batch")


class ReplaySession(BaseModel):
    """A replay session tracking state."""
    session_id: str
    filter: ReplayFilter
    stream_name: str
    subject_prefix: str
    total_count: int = 0
    published: int = 0
    throttle_ms: int = 10
    batch_size: int = 100
    status: ReplayStatus = ReplayStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None


class ReplaySessionResponse(BaseModel):
    """API response for replay session."""
    session_id: str
    status: ReplayStatus
    total_count: int = 0
    published: int = 0
    throttle_ms: int = 10
    message: str = ""
