"""Tests for the backup/restore job pipeline (queue→consume→persist).

The engine is entirely mocked — these tests verify the pipeline itself:
* events emitted by an async runner are consumed off the job queue
* each event is persisted to the BackupJob MongoDB record
* terminal phases ('complete' / 'error') transition job.status correctly
* runner exceptions become FAILED + error event
* the on_event hook is called for subscribers (SSE)
* job state is cleaned up from _job_queues / _job_tasks on completion
"""

from __future__ import annotations

import asyncio
import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient
from wip_toolkit.models import ProgressEvent

from document_store.models.backup_job import (
    BackupJob,
    BackupJobKind,
    BackupJobStatus,
)
from document_store.services import backup_service


@pytest_asyncio.fixture(autouse=True)
async def _init_backup_service_beanie():
    """Initialize Beanie per-test — Motor binds to the active loop."""
    mongo = AsyncIOMotorClient(os.environ["MONGO_URI"])
    db = mongo[os.environ["DATABASE_NAME"] + "_backup_service"]
    await init_beanie(database=db, document_models=[BackupJob])
    await BackupJob.delete_all()
    # Also reset the in-process pipeline state — previous test may have leaked
    backup_service._job_queues.clear()
    backup_service._job_tasks.clear()
    yield
    mongo.close()


@pytest_asyncio.fixture
async def fresh_job() -> BackupJob:
    """Create a fresh PENDING BackupJob and return it."""
    job = BackupJob(
        job_id=f"job-{uuid.uuid4().hex[:8]}",
        kind=BackupJobKind.BACKUP,
        namespace="wip",
        created_by="test-admin",
    )
    await job.insert()
    return job


def _scripted_runner(events: list[ProgressEvent]):
    """Return an AsyncRunner that emits the given events in order."""
    async def runner(callback):
        for ev in events:
            callback(ev)
    return runner


def _scripted_runner_raising(events_before: list[ProgressEvent], exc: Exception):
    """Emit some events then raise — simulates a mid-operation engine failure."""
    async def runner(callback):
        for ev in events_before:
            callback(ev)
        raise exc
    return runner


class TestStartAsyncJobHappyPath:
    async def test_persists_start_through_complete(self, fresh_job: BackupJob):
        events = [
            ProgressEvent(phase="start", message="beginning backup", percent=0.0),
            ProgressEvent(phase="phase_1a_entities", message="entities", percent=25.0),
            ProgressEvent(phase="phase_1b_documents", message="docs", percent=60.0),
            ProgressEvent(phase="complete", message="done", percent=100.0),
        ]
        task = await backup_service.start_async_job(
            fresh_job.job_id, _scripted_runner(events)
        )
        await asyncio.wait_for(task, timeout=5.0)

        updated = await BackupJob.find_one(BackupJob.job_id == fresh_job.job_id)
        assert updated is not None
        assert updated.status == BackupJobStatus.COMPLETE
        assert updated.percent == 100.0
        assert updated.phase == "complete"
        assert updated.started_at is not None
        assert updated.completed_at is not None
        assert updated.error is None

    async def test_on_event_hook_receives_all_events(self, fresh_job: BackupJob):
        events = [
            ProgressEvent(phase="start", message="go", percent=0.0),
            ProgressEvent(phase="phase_documents", message="d", percent=50.0),
            ProgressEvent(phase="complete", message="ok", percent=100.0),
        ]
        received: list[ProgressEvent] = []

        async def on_event(ev: ProgressEvent) -> None:
            received.append(ev)

        task = await backup_service.start_async_job(
            fresh_job.job_id, _scripted_runner(events), on_event=on_event
        )
        await asyncio.wait_for(task, timeout=5.0)

        assert [e.phase for e in received] == ["start", "phase_documents", "complete"]

    async def test_cleans_up_process_state(self, fresh_job: BackupJob):
        events = [
            ProgressEvent(phase="start", message="go", percent=0.0),
            ProgressEvent(phase="complete", message="ok", percent=100.0),
        ]
        task = await backup_service.start_async_job(
            fresh_job.job_id, _scripted_runner(events)
        )
        # Task is registered while running
        assert backup_service.get_job_task(fresh_job.job_id) is not None
        await asyncio.wait_for(task, timeout=5.0)
        # Cleaned up after terminal
        assert backup_service.get_job_task(fresh_job.job_id) is None
        assert fresh_job.job_id not in backup_service._job_queues


class TestStartAsyncJobErrorPaths:
    async def test_explicit_error_phase(self, fresh_job: BackupJob):
        events = [
            ProgressEvent(phase="start", message="go", percent=0.0),
            ProgressEvent(phase="phase_health_check", message="checking", percent=2.0),
            ProgressEvent(
                phase="error",
                message="def-store unreachable",
                details={"health": {"def-store": "down"}},
            ),
        ]
        task = await backup_service.start_async_job(
            fresh_job.job_id, _scripted_runner(events)
        )
        await asyncio.wait_for(task, timeout=5.0)

        updated = await BackupJob.find_one(BackupJob.job_id == fresh_job.job_id)
        assert updated is not None
        assert updated.status == BackupJobStatus.FAILED
        assert updated.error == "def-store unreachable"
        assert updated.phase == "error"
        assert updated.completed_at is not None

    async def test_runner_exception_becomes_failed(self, fresh_job: BackupJob):
        events_before = [
            ProgressEvent(phase="start", message="go", percent=0.0),
            ProgressEvent(phase="phase_1a_entities", message="e", percent=25.0),
        ]
        runner = _scripted_runner_raising(
            events_before, RuntimeError("engine broke")
        )
        task = await backup_service.start_async_job(fresh_job.job_id, runner)
        await asyncio.wait_for(task, timeout=5.0)

        updated = await BackupJob.find_one(BackupJob.job_id == fresh_job.job_id)
        assert updated is not None
        assert updated.status == BackupJobStatus.FAILED
        assert "engine broke" in (updated.error or "")

    async def test_on_event_hook_exception_does_not_break_job(
        self, fresh_job: BackupJob
    ):
        events = [
            ProgressEvent(phase="start", message="go", percent=0.0),
            ProgressEvent(phase="complete", message="ok", percent=100.0),
        ]

        async def explosive(_ev: ProgressEvent) -> None:
            raise RuntimeError("subscriber crashed")

        task = await backup_service.start_async_job(
            fresh_job.job_id, _scripted_runner(events), on_event=explosive
        )
        await asyncio.wait_for(task, timeout=5.0)

        updated = await BackupJob.find_one(BackupJob.job_id == fresh_job.job_id)
        assert updated is not None
        assert updated.status == BackupJobStatus.COMPLETE


class TestStartAsyncJobConcurrency:
    async def test_duplicate_job_rejected(self, fresh_job: BackupJob):
        # A runner gated on an event so the first job is still running
        # when the duplicate submission arrives.
        gate = asyncio.Event()

        async def gated_runner(callback):
            callback(ProgressEvent(phase="start", message="go", percent=0.0))
            await gate.wait()
            callback(ProgressEvent(phase="complete", message="ok", percent=100.0))

        task = await backup_service.start_async_job(fresh_job.job_id, gated_runner)

        with pytest.raises(ValueError, match="already running"):
            await backup_service.start_async_job(
                fresh_job.job_id, _scripted_runner([])
            )

        gate.set()
        await asyncio.wait_for(task, timeout=5.0)


class TestPersistEventDetails:
    async def test_percent_preserved_across_events(self, fresh_job: BackupJob):
        events = [
            ProgressEvent(phase="start", message="go", percent=0.0),
            ProgressEvent(phase="phase_a", message="a", percent=10.0),
            ProgressEvent(phase="phase_b", message="b", percent=50.0),
            ProgressEvent(
                phase="phase_c_no_percent", message="c", percent=None
            ),
            ProgressEvent(phase="complete", message="ok", percent=100.0),
        ]
        task = await backup_service.start_async_job(
            fresh_job.job_id, _scripted_runner(events)
        )
        await asyncio.wait_for(task, timeout=5.0)

        updated = await BackupJob.find_one(BackupJob.job_id == fresh_job.job_id)
        assert updated is not None
        assert updated.percent == 100.0

    async def test_pending_transitions_to_running_on_start(
        self, fresh_job: BackupJob
    ):
        assert fresh_job.status == BackupJobStatus.PENDING
        events = [
            ProgressEvent(phase="start", message="go", percent=0.0),
            ProgressEvent(phase="complete", message="ok", percent=100.0),
        ]
        task = await backup_service.start_async_job(
            fresh_job.job_id, _scripted_runner(events)
        )
        await asyncio.wait_for(task, timeout=5.0)

        updated = await BackupJob.find_one(BackupJob.job_id == fresh_job.job_id)
        assert updated is not None
        # started_at must have been set during the run
        assert updated.started_at is not None
        # and completed_at must be after it
        assert updated.completed_at is not None
        assert updated.completed_at >= updated.started_at



class TestRestoreRunnerModeRouting:
    """``mode`` picks the engine entry point.

    The two modes differ in preconditions and in what they do on a collision,
    so routing to the wrong one is not a degraded run — it is a different
    operation. Regression guard for the class of bug where an option reached
    the job record but never the engine.
    """

    async def test_merge_mode_runs_the_merge_with_its_policies(self, tmp_path):
        engine = MagicMock()
        engine.run_merge = AsyncMock()
        engine.run_restore = AsyncMock()

        runner = backup_service.make_direct_restore_runner(
            tmp_path / "a.zip",
            {
                "mode": "merge",
                "target_namespace": "kb",
                "on_clash": "overwrite",
                "add_missing": True,
                "dry_run": True,
            },
        )
        with (
            patch(
                "document_store.services.backup_engine.DirectRestoreEngine",
                return_value=engine,
            ),
            patch(
                "document_store.services.file_storage_client.is_file_storage_enabled",
                return_value=False,
            ),
        ):
            await runner(lambda _event: None)

        engine.run_restore.assert_not_called()
        kwargs = engine.run_merge.call_args.kwargs
        assert kwargs["target_namespace"] == "kb"
        assert kwargs["on_clash"] == "overwrite"
        assert kwargs["add_missing"] is True
        assert kwargs["dry_run"] is True

    async def test_default_mode_runs_the_plain_restore(self, tmp_path):
        engine = MagicMock()
        engine.run_merge = AsyncMock()
        engine.run_restore = AsyncMock()

        runner = backup_service.make_direct_restore_runner(
            tmp_path / "a.zip", {"target_namespace": "kb"}
        )
        with (
            patch(
                "document_store.services.backup_engine.DirectRestoreEngine",
                return_value=engine,
            ),
            patch(
                "document_store.services.file_storage_client.is_file_storage_enabled",
                return_value=False,
            ),
        ):
            await runner(lambda _event: None)

        engine.run_merge.assert_not_called()
        engine.run_restore.assert_called_once()



class TestValidationAfterRestore:
    """A completed restore verifies what it wrote.

    Restore validates nothing while writing, so this follow-up is the only
    thing that would notice a dangling reference or a drifted identity hash.
    It runs as its own job and the restore does not wait for it — the data is
    committed either way.
    """

    async def test_one_validation_job_per_restored_namespace(self, fresh_job):
        fresh_job.kind = BackupJobKind.RESTORE
        fresh_job.namespaces = ["kb", "library"]
        await fresh_job.save()

        with patch.object(
            backup_service, "start_async_job", new=AsyncMock()
        ) as start:
            started = await backup_service.trigger_validation_for(fresh_job)

        assert len(started) == 2
        assert start.await_count == 2
        jobs = [
            await BackupJob.find_one(BackupJob.job_id == job_id)
            for job_id in started
        ]
        assert sorted(j.namespace for j in jobs) == ["kb", "library"]
        assert {j.kind for j in jobs} == {BackupJobKind.VALIDATE}

    async def test_the_restore_records_the_jobs_it_started(self, fresh_job):
        fresh_job.kind = BackupJobKind.RESTORE
        fresh_job.namespaces = ["kb"]
        await fresh_job.save()

        with patch.object(backup_service, "start_async_job", new=AsyncMock()):
            started = await backup_service.trigger_validation_for(fresh_job)

        updated = await BackupJob.find_one(BackupJob.job_id == fresh_job.job_id)
        assert updated.validation_job_ids == started

    async def test_a_failure_to_start_never_fails_the_restore(self, fresh_job):
        # The restore succeeded. Reporting it as failed because a follow-up
        # check could not start would be a lie about the data.
        fresh_job.kind = BackupJobKind.RESTORE
        fresh_job.namespaces = ["kb"]
        await fresh_job.save()

        with patch.object(
            backup_service, "start_async_job",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            started = await backup_service.trigger_validation_for(fresh_job)

        assert started == []

    async def test_a_validation_job_does_not_trigger_another(self, fresh_job):
        # Only restores trigger validation; otherwise each check would spawn
        # the next one forever.
        fresh_job.kind = BackupJobKind.VALIDATE
        fresh_job.status = BackupJobStatus.RUNNING
        await fresh_job.save()

        with patch.object(
            backup_service, "trigger_validation_for", new=AsyncMock()
        ) as trigger:
            await backup_service._persist_event(
                fresh_job.job_id,
                ProgressEvent(phase="complete", message="done", percent=100.0),
            )

        trigger.assert_not_awaited()

# Need asyncio mode for async tests in this module
pytestmark = pytest.mark.asyncio
