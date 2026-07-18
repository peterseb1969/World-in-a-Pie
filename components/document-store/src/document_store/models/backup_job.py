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
        description="Globally unique job ID: 'bkp-' (backup) or 'rst-' (restore) prefix + 16 hex chars (UUID4-derived)"
    )
    kind: BackupJobKind = Field(
        ...,
        description="backup (export) or restore (import)"
    )
    namespace: str = Field(
        ...,
        description="Primary namespace (the URL anchor). For a multi-namespace "
                    "backup this is the first of `namespaces`; for a "
                    "single-namespace restore it is the target, but for a "
                    "multi-namespace restore it is the URL anchor only (each "
                    "namespace restores into itself) — use `namespaces` for the "
                    "actual restored set."
    )
    namespaces: list[str] = Field(
        default_factory=list,
        description="All namespaces this job spans. For a backup: the exported "
                    "set (1 for a single-namespace backup). For a restore: the "
                    "namespaces the archive writes into, read from its manifest "
                    "(the scalar namespace when the manifest was unreadable — "
                    "never blank on new records; legacy records may be empty).",
    )

    # Lifecycle
    status: BackupJobStatus = Field(
        default=BackupJobStatus.PENDING,
        description="Current lifecycle status"
    )
    phase: str | None = Field(
        default=None,
        description="Current phase from the toolkit's ProgressEvent (e.g. 'phase_documents')"
    )
    percent: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Progress percentage (0-100) from the latest event"
    )
    message: str | None = Field(
        default=None,
        description="Human-readable message from the latest event"
    )
    error: str | None = Field(
        default=None,
        description="Error message if status is 'failed'"
    )

    # Timestamps
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the job was created"
    )
    started_at: datetime | None = Field(
        default=None,
        description="When the worker started running the toolkit"
    )
    completed_at: datetime | None = Field(
        default=None,
        description="When the job reached a terminal status (complete or failed)"
    )

    # Archive tracking
    archive_path: str | None = Field(
        default=None,
        description="Archive locator: a local filesystem path when "
                    "archive_backend is 'local', an object key in the "
                    "backup bucket when it is 'minio'"
    )
    archive_backend: str = Field(
        default="local",
        description="Where the archive lives: 'local' (scratch filesystem, "
                    "no durability promise) or 'minio' (dedicated bucket). "
                    "Records predating the field are local by construction, "
                    "which is exactly what the default yields."
    )
    archive_size: int | None = Field(
        default=None,
        ge=0,
        description="Archive size in bytes"
    )

    # Caller-supplied options (request body) for reproducibility and audit
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="The request body / options that initiated the job"
    )

    # Non-fatal findings surfaced during the job (e.g. reporting count-parity
    # incomplete after its bounded wait). A completed job with warnings
    # succeeded — the warnings say what to double-check.
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal warnings accumulated while the job ran"
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
        default=None,
        description=(
            "Current phase name — a free-form runtime convention shared "
            "between producer and consumer, not a schema contract. Phase "
            "names may change in a future implementation."
        ),
    )
    percent: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Progress percentage (0-100), if known",
    )
    message: str | None = Field(default=None, description="Human-readable status message")
    current: int | None = Field(
        default=None,
        ge=0,
        description="Items processed so far in the current phase (if applicable)",
    )
    total: int | None = Field(
        default=None,
        ge=0,
        description="Total items to process in the current phase (if applicable)",
    )
    details: dict[str, Any] | None = Field(
        default=None,
        description="Opaque per-phase details (counts, sizes, skipped entities)",
    )


class BackupRequest(BaseModel):
    """Request body for POST /backup/namespaces/{namespace}/backup.

    Most fields map to keyword arguments of the underlying backup engine; the
    factory in ``backup_service`` forwards this dict as ``options``. The
    multi-namespace selectors (`namespaces`, `all_namespaces`) are resolved in
    the endpoint, not forwarded to the engine.
    """

    namespaces: list[str] | None = Field(
        default=None,
        description=(
            "Also back up these namespaces into the same archive "
            "(joined with the URL's {namespace}). Omit for single-namespace."
        ),
    )
    all_namespaces: bool = Field(
        False,
        description=(
            "Back up EVERY namespace the registry lists (incl. 'wip') "
            "into one archive. Overrides `namespaces`/{namespace}."
        ),
    )
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
        default=None,
        description="Optional list of template_id prefixes to filter documents",
    )
    dry_run: bool = Field(
        False, description="Walk the export without writing the archive"
    )


# NOTE: restore's multipart form fields are bound directly as Form(...)
# parameters on `start_restore` (api/backup.py) — deliberately no request
# model here. A previous `RestoreRequest` model existed but was never wired
# to the route, so its declared bounds were unenforced prose (CASE-564); the
# one meaningful bound (batch_size 1..500) lives on the Form declaration.


class RestoreFromJobRequest(BaseModel):
    """Request body for POST /backup/jobs/{job_id}/restore.

    Unlike the upload restore (multipart form), this endpoint takes JSON —
    the archive is already retained server-side, so there is no file part.
    This model IS wired to the route, so its bounds are enforced.
    """

    skip_documents: bool = Field(
        False, description="Skip the documents phase entirely"
    )
    skip_files: bool = Field(
        False, description="Skip restoring file blobs"
    )
    batch_size: int = Field(
        50, ge=1, le=500, description="Document write batch size"
    )


class BackupJobSnapshot(BaseModel):
    """API response shape for a BackupJob — hides mongo _id and trims internals."""

    job_id: str
    kind: BackupJobKind
    namespace: str
    namespaces: list[str] = Field(default_factory=list)
    status: BackupJobStatus
    phase: str | None = None
    percent: float | None = None
    message: str | None = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    archive_size: int | None = None
    archive_backend: str = "local"
    options: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    created_by: str

    @classmethod
    def from_job(cls, job: BackupJob) -> "BackupJobSnapshot":
        """Build a snapshot from a BackupJob document."""
        return cls(
            job_id=job.job_id,
            kind=job.kind,
            namespace=job.namespace,
            namespaces=job.namespaces,
            status=job.status,
            phase=job.phase,
            percent=job.percent,
            message=job.message,
            error=job.error,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            archive_size=job.archive_size,
            archive_backend=job.archive_backend,
            options=job.options,
            warnings=job.warnings,
            created_by=job.created_by,
        )
