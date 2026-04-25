"""BackupJob model for the Document Store service.

Tracks long-running backup/restore jobs that wrap wip-toolkit in-process.
Created when a caller POSTs to /namespaces/{ns}/backup or /namespaces/{ns}/restore;
updated by the async/sync bridge as the toolkit emits progress events; read by
the SSE and status endpoints.

The MongoDB-persisted record is the durable source of truth for progress so a
service restart or a second uvicorn worker can still report a sensible last-known
state. The in-process asyncio.Queue used by SSE is a latency optimization that
sits on top of this record.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar

from beanie import Document as BeanieDocument
from pydantic import BaseModel, Field
from pymongo import DESCENDING, IndexModel


class BackupJobKind(StrEnum):
    """Whether this job is exporting (backup) or importing (restore)."""

    BACKUP = "backup"
    RESTORE = "restore"


class BackupJobStatus(StrEnum):
    """Lifecycle status for a backup/restore job."""

    PENDING = "pending"      # Created, worker not yet started
    RUNNING = "running"      # Worker running, toolkit in progress
    COMPLETE = "complete"    # Finished successfully
    FAILED = "failed"        # Aborted with an error


class BackupJob(BeanieDocument):
    """
    A long-running backup or restore job.

    Jobs are created immediately on request and the actual toolkit run happens
    in a worker thread bridged to the event loop. Progress events from the
    toolkit are persisted here as they arrive; the SSE endpoint can replay the
    latest known state to reconnecting clients from this document.

    Not a namespaced entity: backup jobs can span namespaces in the future
    (e.g. multi-namespace backups), and the job_id is globally unique.
    """

    # Identity
    job_id: str = Field(
        ...,
        description="Globally unique job ID (UUID4)"
    )
    kind: BackupJobKind = Field(
        ...,
        description="backup (export) or restore (import)"
    )
    namespace: str = Field(
        ...,
        description="Source namespace for backup, target namespace for restore"
    )

    # Lifecycle
    status: BackupJobStatus = Field(
        default=BackupJobStatus.PENDING,
        description="Current lifecycle status"
    )
    phase: str | None = Field(
        None,
        description="Current phase from the toolkit's ProgressEvent (e.g. 'phase_documents')"
    )
    percent: float | None = Field(
        None,
        ge=0.0,
        le=100.0,
        description="Progress percentage (0-100) from the latest event"
    )
    message: str | None = Field(
        None,
        description="Human-readable message from the latest event"
    )
    error: str | None = Field(
        None,
        description="Error message if status is 'failed'"
    )

    # Timestamps
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the job was created"
    )
    started_at: datetime | None = Field(
        None,
        description="When the worker started running the toolkit"
    )
    completed_at: datetime | None = Field(
        None,
        description="When the job reached a terminal status (complete or failed)"
    )

    # Archive tracking
    archive_path: str | None = Field(
        None,
        description="Local filesystem path of the produced (backup) or uploaded (restore) archive"
    )
    archive_size: int | None = Field(
        None,
        ge=0,
        description="Archive size in bytes"
    )

    # Caller-supplied options (request body) for reproducibility and audit
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="The request body / options that initiated the job"
    )

    # Provenance
    created_by: str = Field(
        ...,
        description="identity_string of the caller who created the job"
    )

    class Settings:
        name = "backup_jobs"
        indexes: ClassVar[list[IndexModel]] = [
            # Unique job lookup
            IndexModel([("job_id", 1)], unique=True, name="backup_job_id_unique_idx"),
            # Namespace + status filter (for dashboards / list endpoints)
            IndexModel([("namespace", 1), ("status", 1)], name="backup_ns_status_idx"),
            # Cleanup cron scans by created_at
            IndexModel([("created_at", DESCENDING)], name="backup_created_at_idx"),
            # Audit: who created it
            IndexModel([("created_by", 1)], name="backup_created_by_idx", sparse=True),
        ]


class BackupProgressMessage(BaseModel):
    """SSE wire envelope for backup/restore progress events.

    **Guardrail 2 (CASE-23 Phase 3)** — this type is the public contract for
    the SSE endpoint. It is deliberately **not** ``wip_toolkit.models.ProgressEvent``:
    the toolkit's event type is an implementation detail that must not leak
    to clients, so a future v1.1 rewrite that replaces the toolkit can still
    emit the same wire format without breaking clients or the @wip/client
    TypeScript types.

    The fields below are the stable subset of information that SSE
    subscribers need. ``phase`` is intentionally a free-form string (Guardrail 3).
    """

    job_id: str = Field(..., description="The BackupJob.job_id this event belongs to")
    status: BackupJobStatus = Field(..., description="Current lifecycle status of the job")
    phase: str | None = Field(
        None,
        description=(
            "Current phase name — a free-form runtime convention shared "
            "between producer and consumer, not a schema contract. Phase "
            "names may change in a future implementation."
        ),
    )
    percent: float | None = Field(
        None,
        ge=0.0,
        le=100.0,
        description="Progress percentage (0-100), if known",
    )
    message: str | None = Field(None, description="Human-readable status message")
    current: int | None = Field(
        None,
        ge=0,
        description="Items processed so far in the current phase (if applicable)",
    )
    total: int | None = Field(
        None,
        ge=0,
        description="Total items to process in the current phase (if applicable)",
    )
    details: dict[str, Any] | None = Field(
        None,
        description="Opaque per-phase details (counts, sizes, skipped entities)",
    )


class BackupRequest(BaseModel):
    """Request body for POST /backup/namespaces/{namespace}/backup.

    All fields map to keyword arguments of the underlying toolkit
    :func:`run_export` call; the factory in ``backup_service`` forwards this
    dict as ``**options``.
    """

    include_files: bool = Field(
        False, description="Include file blobs in the archive"
    )
    include_inactive: bool = Field(
        False, description="Include inactive (soft-deleted) entities"
    )
    skip_documents: bool = Field(
        False, description="Skip the documents phase entirely"
    )
    skip_closure: bool = Field(
        False, description="Skip the closure-table (term-relations) phase"
    )
    skip_synonyms: bool = Field(
        False, description="Skip the synonyms phase"
    )
    latest_only: bool = Field(
        False, description="Export only the latest version of each entity"
    )
    template_prefixes: list[str] | None = Field(
        None,
        description="Optional list of template_id prefixes to filter documents",
    )
    dry_run: bool = Field(
        False, description="Walk the export without writing the archive"
    )


class RestoreRequest(BaseModel):
    """Parameters accompanying a multipart restore upload.

    These fields are expected as form fields alongside the ``archive`` file.
    They map to keyword arguments of :func:`run_import`.
    """

    mode: str = Field(
        "restore",
        description="'restore' (preserve IDs) or 'fresh' (generate new IDs)",
    )
    target_namespace: str | None = Field(
        None,
        description="Override target namespace (defaults to the archive's source namespace)",
    )
    register_synonyms: bool = Field(
        False,
        description="Register original IDs as synonyms of the new IDs (fresh mode)",
    )
    skip_documents: bool = Field(
        False, description="Skip restoring documents (definitions only)"
    )
    skip_files: bool = Field(
        False, description="Skip restoring file blobs"
    )
    batch_size: int = Field(
        50, ge=1, le=500, description="Restore batch size"
    )
    continue_on_error: bool = Field(
        False, description="Continue past per-item errors"
    )
    dry_run: bool = Field(
        False, description="Walk the import without applying changes"
    )


class BackupJobSnapshot(BaseModel):
    """API response shape for a BackupJob — hides mongo _id and trims internals."""

    job_id: str
    kind: BackupJobKind
    namespace: str
    status: BackupJobStatus
    phase: str | None = None
    percent: float | None = None
    message: str | None = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    archive_size: int | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    created_by: str

    @classmethod
    def from_job(cls, job: BackupJob) -> "BackupJobSnapshot":
        """Build a snapshot from a BackupJob document."""
        return cls(
            job_id=job.job_id,
            kind=job.kind,
            namespace=job.namespace,
            status=job.status,
            phase=job.phase,
            percent=job.percent,
            message=job.message,
            error=job.error,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            archive_size=job.archive_size,
            options=job.options,
            created_by=job.created_by,
        )
