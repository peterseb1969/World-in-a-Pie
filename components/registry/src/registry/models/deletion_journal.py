"""Deletion journal model for crash-safe namespace deletion.

A DeletionJournal tracks every step of a namespace deletion, enabling
idempotent resumption after crashes and serving as an audit trail.
"""

from datetime import UTC, datetime
from typing import Any, ClassVar, Literal

from beanie import Document
from pydantic import BaseModel, Field
from pymongo import IndexModel


class DeletionStep(BaseModel):
    """A single step in the deletion journal."""

    order: int
    store: Literal["mongodb", "postgresql", "minio"]
    database: str | None = None
    collection: str | None = None
    action: str | None = None
    detail: str | None = None
    filter: dict[str, Any] | None = None
    storage_keys: list[str] | None = None
    # completed_with_errors: the step ran to completion under best-effort
    # semantics (MinIO/PostgreSQL cleanup must not block namespace deletion —
    # MongoDB is the system of record) but swallowed a failure, so data may
    # remain in that store. error carries the reason. A resumed journal
    # re-runs such steps (deletes are idempotent), unlike "completed" ones.
    status: Literal[
        "pending", "completed", "completed_with_errors", "failed"
    ] = "pending"
    deleted_count: int = 0
    error: str | None = None
    completed_at: datetime | None = None


class InboundReference(BaseModel):
    """A reference from another namespace into the one being deleted."""

    type: str
    source_namespace: str
    source_entity: str
    target_entity: str
    impact: str


class DeletionJournal(Document):
    """Persistent journal for namespace deletion.

    Created when deletion starts, updated as each step completes.
    Completed journals are never deleted — they serve as the audit trail.
    """

    namespace: str = Field(..., description="Namespace being deleted")
    # completed_with_warnings: every step executed, but at least one
    # best-effort step (MinIO/PostgreSQL) degraded — data may remain in a
    # secondary store. Terminal, like "completed"; the per-step error fields
    # say what degraded. Operators must not read bare "completed" semantics
    # into it.
    status: Literal[
        "in_progress", "completed", "completed_with_warnings", "failed"
    ] = "in_progress"
    requested_by: str | None = None
    requested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    force: bool = False
    broken_references: list[InboundReference] = Field(default_factory=list)
    steps: list[DeletionStep] = Field(default_factory=list)
    summary: dict[str, int] | None = None

    class Settings:
        name = "namespace_deletions"
        indexes: ClassVar = [
            IndexModel([("namespace", 1)], name="namespace_idx"),
            IndexModel([("status", 1)], name="status_idx"),
        ]
