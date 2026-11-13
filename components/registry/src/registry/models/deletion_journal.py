"""Deletion journal model for crash-safe namespace deletion.

A DeletionJournal tracks every step of a namespace deletion, enabling
idempotent resumption after crashes and serving as an audit trail.
"""

from datetime import UTC, datetime
from typing import Any, ClassVar, Literal

from beanie import Document
from pydantic import BaseModel, Field, field_validator
from pymongo import IndexModel


class DeletionStep(BaseModel):
    """A single step in the deletion journal."""

    order: int
    # "file_storage": stored file objects, deleted through document-store.
    # "minio" is the same step as written by older journals.
    store: Literal["mongodb", "postgresql", "file_storage", "minio"]
    database: str | None = None
    collection: str | None = None
    action: str | None = None
    detail: str | None = None
    filter: dict[str, Any] | None = None
    # The stored-object keys this step deletes. Journals write them to a
    # separate document per batch (storage_keys_batch, the _id of a
    # namespace_deletion_keys document): a journal is one MongoDB document,
    # and the keys of a namespace with a few hundred thousand files do not
    # fit in it however they are split across steps. storage_keys inline is
    # what older journals wrote, and is still read.
    storage_keys: list[str] | None = None
    storage_keys_batch: str | None = None
    # completed_with_errors: the step ran to completion under best-effort
    # semantics (file-storage/PostgreSQL cleanup must not block namespace deletion —
    # MongoDB is the system of record) but swallowed a failure, so data may
    # remain in that store. error carries the reason. A resumed journal
    # re-runs such steps (deletes are idempotent), unlike "completed" ones.
    status: Literal[
        "pending", "completed", "completed_with_errors", "failed"
    ] = "pending"
    deleted_count: int = 0
    error: str | None = None
    completed_at: datetime | None = None

    @field_validator("deleted_count", mode="before")
    @classmethod
    def _null_count_reads_as_zero(cls, v: Any) -> Any:
        """Journals persisted while reporting-sync returned a null row count
        carry ``deleted_count: null`` in MongoDB. The journal is the audit
        trail and completed journals are never deleted, so those documents
        must stay readable — hydrating them to 0 beats a 500 on every
        deletion-status read. The write path never produces None anymore
        (the executor maps a null upstream count to 0 + step.error)."""
        return 0 if v is None else v


class InboundReference(BaseModel):
    """A reference from another namespace into the one being deleted."""

    type: str
    source_namespace: str
    source_entity: str
    target_entity: str
    impact: str


class InboundReferenceCount(BaseModel):
    """How many inbound references of one kind came from one namespace."""

    type: str
    source_namespace: str
    count: int


class DeletionJournal(Document):
    """Persistent journal for namespace deletion.

    Created when deletion starts, updated as each step completes.
    Completed journals are never deleted — they serve as the audit trail.
    """

    namespace: str = Field(..., description="Namespace being deleted")
    namespace_id: str | None = Field(
        default=None,
        description=(
            "The namespace_id of the namespace being deleted: a namespace that "
            "holds the prefix later with another id is a new one, whose data "
            "the deletion's later steps leave alone"
        ),
    )
    # completed_with_warnings: every step executed, but at least one
    # best-effort step (file storage/PostgreSQL) degraded — data may remain in a
    # secondary store. Terminal, like "completed"; the per-step error fields
    # say what degraded. Operators must not read bare "completed" semantics
    # into it.
    status: Literal[
        "in_progress", "completed", "completed_with_warnings", "failed"
    ] = "in_progress"
    # The authenticated caller who asked for the deletion (its identity
    # string); the caller's own free-text note, if it sent one, beside it.
    requested_by: str | None = None
    requested_note: str | None = None
    requested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    force: bool = False
    # The references from other namespaces the deletion breaks. The journal
    # is one MongoDB document, so it keeps the first ones only (a heavily
    # referenced namespace has more than 16 MiB of them); the counts per
    # kind and source namespace cover every one found.
    broken_references: list[InboundReference] = Field(default_factory=list)
    broken_references_counts: list[InboundReferenceCount] = Field(default_factory=list)
    broken_references_truncated: bool = False
    steps: list[DeletionStep] = Field(default_factory=list)
    summary: dict[str, int] | None = None
    # Who runs the journal now. A run claims the journal with one
    # conditional update (no token, or a heartbeat older than the lease) and
    # every write it makes is conditional on still holding the token, so two
    # runs of one deletion, in one process or several, never both act on it.
    # A run refreshes the heartbeat with each write; a run that died leaves
    # a heartbeat that ages out, and the next run takes the journal over.
    run_token: str | None = None
    run_heartbeat: datetime | None = None

    @field_validator("summary", mode="before")
    @classmethod
    def _null_summary_counts_read_as_zero(cls, v: Any) -> Any:
        """Same tolerance as DeletionStep.deleted_count, for the summary dict:
        a stored ``postgres_rows: null`` must hydrate (as 0) rather than make
        the journal unreadable. New writes never contain None values."""
        if isinstance(v, dict):
            return {k: (0 if val is None else val) for k, val in v.items()}
        return v

    class Settings:
        name = "namespace_deletions"
        indexes: ClassVar = [
            IndexModel([("namespace", 1)], name="namespace_idx"),
            IndexModel([("status", 1)], name="status_idx"),
        ]
