"""Tests for the backup/restore async/sync bridge (CASE-23 Phase 3 STEP 3).

The toolkit is entirely mocked — these tests verify the bridge itself:
* events from a worker thread are marshalled onto the event loop
* each event is persisted to the BackupJob MongoDB record
* terminal phases ('complete' / 'error') transition job.status correctly
* worker-thread exceptions become FAILED + error event
* the on_event hook is called for subscribers (SSE)
* job state is cleaned up from _job_queues / _job_tasks on completion
"""

from __future__ import annotations

import asyncio
import os
import uuid

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
    # Also reset the in-process bridge state — previous test may have leaked
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
    """Return a ToolkitRunner that emits the given events in order."""
    def runner(callback):
        for ev in events:
            callback(ev)
    return runner


def _scripted_runner_raising(events_before: list[ProgressEvent], exc: Exception):
    """Emit some events then raise — simulates a mid-operation toolkit failure."""
    def runner(callback):
        for ev in events_before:
            callback(ev)
        raise exc
    return runner


class TestStartJobHappyPath:
    async def test_persists_start_through_complete(self, fresh_job: BackupJob):
        events = [
            ProgressEvent(phase="start", message="beginning backup", percent=0.0),
            ProgressEvent(phase="phase_1a_entities", message="entities", percent=25.0),
            ProgressEvent(phase="phase_1b_documents", message="docs", percent=60.0),
            ProgressEvent(phase="complete", message="done", percent=100.0),
        ]
        task = await backup_service.start_job(
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

        task = await backup_service.start_job(
            fresh_job.job_id, _scripted_runner(events), on_event=on_event
        )
        await asyncio.wait_for(task, timeout=5.0)

        assert [e.phase for e in received] == ["start", "phase_documents", "complete"]

    async def test_cleans_up_process_state(self, fresh_job: BackupJob):
        events = [
            ProgressEvent(phase="start", message="go", percent=0.0),
            ProgressEvent(phase="complete", message="ok", percent=100.0),
        ]
        task = await backup_service.start_job(
            fresh_job.job_id, _scripted_runner(events)
        )
        # Task is registered while running
        assert backup_service.get_job_task(fresh_job.job_id) is not None
        await asyncio.wait_for(task, timeout=5.0)
        # Cleaned up after terminal
        assert backup_service.get_job_task(fresh_job.job_id) is None
        assert fresh_job.job_id not in backup_service._job_queues


class TestStartJobErrorPaths:
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
        task = await backup_service.start_job(
            fresh_job.job_id, _scripted_runner(events)
        )
        await asyncio.wait_for(task, timeout=5.0)

        updated = await BackupJob.find_one(BackupJob.job_id == fresh_job.job_id)
        assert updated is not None
        assert updated.status == BackupJobStatus.FAILED
        assert updated.error == "def-store unreachable"
        assert updated.phase == "error"
        assert updated.completed_at is not None

    async def test_worker_thread_exception_becomes_failed(self, fresh_job: BackupJob):
        events_before = [
            ProgressEvent(phase="start", message="go", percent=0.0),
            ProgressEvent(phase="phase_1a_entities", message="e", percent=25.0),
        ]
        runner = _scripted_runner_raising(
            events_before, RuntimeError("httpx broke")
        )
        task = await backup_service.start_job(fresh_job.job_id, runner)
        await asyncio.wait_for(task, timeout=5.0)

        updated = await BackupJob.find_one(BackupJob.job_id == fresh_job.job_id)
        assert updated is not None
        assert updated.status == BackupJobStatus.FAILED
        assert "httpx broke" in (updated.error or "")

    async def test_on_event_hook_exception_does_not_break_job(
        self, fresh_job: BackupJob
    ):
        events = [
            ProgressEvent(phase="start", message="go", percent=0.0),
            ProgressEvent(phase="complete", message="ok", percent=100.0),
        ]

        async def explosive(_ev: ProgressEvent) -> None:
            raise RuntimeError("subscriber crashed")

        task = await backup_service.start_job(
            fresh_job.job_id, _scripted_runner(events), on_event=explosive
        )
        await asyncio.wait_for(task, timeout=5.0)

        updated = await BackupJob.find_one(BackupJob.job_id == fresh_job.job_id)
        assert updated is not None
        assert updated.status == BackupJobStatus.COMPLETE


class TestStartJobConcurrency:
    async def test_duplicate_job_rejected(self, fresh_job: BackupJob):
        # A runner that blocks on an event so we can race
        gate = asyncio.Event()
        gate_set = False

        def slow_runner(callback):
            nonlocal gate_set
            callback(ProgressEvent(phase="start", message="go", percent=0.0))
            # Busy-wait briefly; the loop flags that start event has been
            # picked up via gate being set from the consumer side.
            import time
            for _ in range(50):
                if gate_set:
                    break
                time.sleep(0.01)
            callback(ProgressEvent(phase="complete", message="ok", percent=100.0))

        task = await backup_service.start_job(fresh_job.job_id, slow_runner)

        with pytest.raises(ValueError, match="already running"):
            await backup_service.start_job(
                fresh_job.job_id, _scripted_runner([])
            )

        gate_set = True
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
        task = await backup_service.start_job(
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
        task = await backup_service.start_job(
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


# Need asyncio mode for async tests in this module
pytestmark = pytest.mark.asyncio
