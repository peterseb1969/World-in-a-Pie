"""Tests for the BackupJob Beanie document model.

Pin the field contract and default behavior so the async/sync bridge and REST
endpoints (CASE-23 STEPS 3 & 5) can rely on a stable shape. We call init_beanie
once at module scope because Beanie Document.__init__ touches
get_motor_collection() and would otherwise raise CollectionWasNotInitialized.
"""

import os
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import ValidationError

from document_store.models.backup_job import (
    BackupJob,
    BackupJobKind,
    BackupJobSnapshot,
    BackupJobStatus,
)


@pytest_asyncio.fixture(scope="module", autouse=True)
async def _init_backup_job_beanie():
    """Initialize Beanie once for this test module so Document.__init__ works."""
    mongo = AsyncIOMotorClient(os.environ["MONGO_URI"])
    db = mongo[os.environ["DATABASE_NAME"] + "_backup_job_model"]
    await init_beanie(database=db, document_models=[BackupJob])
    yield
    mongo.close()


class TestBackupJobDefaults:
    """New BackupJob instances should have sensible defaults."""

    def test_minimal_fields_construct(self):
        job = BackupJob(
            job_id="job-123",
            kind=BackupJobKind.BACKUP,
            namespace="wip",
            created_by="wip-admin",
        )
        assert job.job_id == "job-123"
        assert job.kind == BackupJobKind.BACKUP
        assert job.namespace == "wip"
        assert job.status == BackupJobStatus.PENDING
        assert job.phase is None
        assert job.percent is None
        assert job.message is None
        assert job.error is None
        assert job.started_at is None
        assert job.completed_at is None
        assert job.archive_path is None
        assert job.archive_size is None
        assert job.options == {}
        assert job.created_by == "wip-admin"

    def test_created_at_is_timezone_aware(self):
        job = BackupJob(
            job_id="job-123",
            kind=BackupJobKind.BACKUP,
            namespace="wip",
            created_by="wip-admin",
        )
        assert isinstance(job.created_at, datetime)
        assert job.created_at.tzinfo is not None

    def test_restore_kind_accepted(self):
        job = BackupJob(
            job_id="job-456",
            kind=BackupJobKind.RESTORE,
            namespace="wip",
            created_by="wip-admin",
        )
        assert job.kind == BackupJobKind.RESTORE


class TestBackupJobValidation:
    """Field-level validation."""

    def test_percent_bounds(self):
        # In-range values allowed
        BackupJob(
            job_id="j",
            kind=BackupJobKind.BACKUP,
            namespace="wip",
            created_by="u",
            percent=0.0,
        )
        BackupJob(
            job_id="j",
            kind=BackupJobKind.BACKUP,
            namespace="wip",
            created_by="u",
            percent=100.0,
        )
        # Out-of-range rejected
        with pytest.raises(ValidationError):
            BackupJob(
                job_id="j",
                kind=BackupJobKind.BACKUP,
                namespace="wip",
                created_by="u",
                percent=-1.0,
            )
        with pytest.raises(ValidationError):
            BackupJob(
                job_id="j",
                kind=BackupJobKind.BACKUP,
                namespace="wip",
                created_by="u",
                percent=101.0,
            )

    def test_archive_size_non_negative(self):
        with pytest.raises(ValidationError):
            BackupJob(
                job_id="j",
                kind=BackupJobKind.BACKUP,
                namespace="wip",
                created_by="u",
                archive_size=-1,
            )

    def test_required_fields_missing(self):
        with pytest.raises(ValidationError):
            BackupJob()  # type: ignore[call-arg]

    def test_kind_must_be_valid_enum(self):
        with pytest.raises(ValidationError):
            BackupJob(
                job_id="j",
                kind="invalid",  # type: ignore[arg-type]
                namespace="wip",
                created_by="u",
            )


class TestBackupJobProgressUpdate:
    """Simulate the path the async bridge uses to push progress in-place."""

    def test_mutate_progress_fields(self):
        job = BackupJob(
            job_id="j",
            kind=BackupJobKind.BACKUP,
            namespace="wip",
            created_by="u",
        )
        # The bridge sets these as ProgressEvent fields arrive
        job.status = BackupJobStatus.RUNNING
        job.phase = "phase_documents"
        job.percent = 75.0
        job.message = "writing documents to archive"
        job.started_at = datetime.now(UTC)

        assert job.status == BackupJobStatus.RUNNING
        assert job.phase == "phase_documents"
        assert job.percent == 75.0
        assert job.started_at is not None

    def test_terminal_complete(self):
        job = BackupJob(
            job_id="j",
            kind=BackupJobKind.BACKUP,
            namespace="wip",
            created_by="u",
        )
        job.status = BackupJobStatus.COMPLETE
        job.percent = 100.0
        job.completed_at = datetime.now(UTC)
        job.archive_path = "/var/lib/wip/backups/j.zip"
        job.archive_size = 10_485_760
        assert job.status == BackupJobStatus.COMPLETE
        assert job.archive_size == 10_485_760

    def test_terminal_failed_with_error(self):
        job = BackupJob(
            job_id="j",
            kind=BackupJobKind.BACKUP,
            namespace="wip",
            created_by="u",
        )
        job.status = BackupJobStatus.FAILED
        job.error = "def-store unreachable"
        job.completed_at = datetime.now(UTC)
        assert job.status == BackupJobStatus.FAILED
        assert job.error == "def-store unreachable"


class TestBackupJobSnapshot:
    """The snapshot should faithfully reproduce all public fields."""

    def test_roundtrip(self):
        job = BackupJob(
            job_id="j-42",
            kind=BackupJobKind.RESTORE,
            namespace="wip",
            created_by="admin",
            options={"skip_documents": True, "batch_size": 100},
        )
        job.status = BackupJobStatus.RUNNING
        job.phase = "phase_terms"
        job.percent = 20.5
        job.message = "loading terms"

        snap = BackupJobSnapshot.from_job(job)
        assert snap.job_id == "j-42"
        assert snap.kind == BackupJobKind.RESTORE
        assert snap.namespace == "wip"
        assert snap.status == BackupJobStatus.RUNNING
        assert snap.phase == "phase_terms"
        assert snap.percent == 20.5
        assert snap.message == "loading terms"
        assert snap.options == {"skip_documents": True, "batch_size": 100}
        assert snap.created_by == "admin"
        assert snap.error is None

    def test_snapshot_excludes_nothing_public(self):
        """Every user-facing field on BackupJob should exist on BackupJobSnapshot."""
        # Strip beanie-internal fields (id, revision_id) — these are ODM plumbing,
        # not part of the public job contract.
        beanie_internal = {"id", "revision_id"}
        job_fields = set(BackupJob.model_fields.keys()) - beanie_internal
        snap_fields = set(BackupJobSnapshot.model_fields.keys())
        # archive_path is intentionally hidden from the API snapshot
        # (it's a server-local filesystem path; downloads go via the /download endpoint)
        expected_hidden = {"archive_path"}
        assert job_fields - snap_fields == expected_hidden


class TestBackupJobSettings:
    """Beanie Settings should declare the collection and indexes we expect."""

    def test_collection_name(self):
        assert BackupJob.Settings.name == "backup_jobs"

    def test_indexes_declared(self):
        index_names = {idx.document["name"] for idx in BackupJob.Settings.indexes}
        assert "backup_job_id_unique_idx" in index_names
        assert "backup_ns_status_idx" in index_names
        assert "backup_created_at_idx" in index_names
