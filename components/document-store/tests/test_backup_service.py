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
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from wip_archive.models import ProgressEvent

from document_store.models.backup_job import (
    BackupJob,
    BackupJobKind,
    BackupJobStatus,
)
from document_store.services import backup_service
from document_store.services.backup_engine import RestoreRequester

_REQUESTER = RestoreRequester(credential={"X-API-Key": "requester-key"}, platform_admin=False)


def _make_runner(*args, **kwargs):
    return backup_service.make_direct_restore_runner(*args, requester=_REQUESTER, **kwargs)


@pytest_asyncio.fixture(autouse=True)
async def _init_backup_service_beanie(session_mongo_client):
    """Bind Beanie via the shared session client, isolate per test.

    Re-running init_beanie on a private client here (the old per-test
    pattern from function-scoped-loop days) would re-bind BackupJob to a
    client this fixture then closes — leaving every later test in the
    session holding a model bound to a dead client. Under the
    session-scoped loop the one union binding serves everyone; per-test
    isolation is the delete_all, not a private database.
    """
    from tests.conftest import _ensure_beanie
    await _ensure_beanie(session_mongo_client)
    await BackupJob.delete_all()
    # Also reset the in-process pipeline state — previous test may have leaked
    backup_service._job_queues.clear()
    backup_service._job_tasks.clear()


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

    @pytest.mark.parametrize("terminal", ["complete", "error"])
    async def test_a_terminal_event_is_settled_before_the_record_turns_terminal(
        self, fresh_job: BackupJob, terminal: str,
    ):
        # The archive hook uploads a finished backup, which can take minutes.
        # It must run while the record still reads RUNNING, which DELETE
        # refuses: a DELETE that landed on a COMPLETE record mid-upload
        # removed the record and left the uploaded object in the bucket with
        # nothing pointing at it. A progress event reaches the hook after it
        # is persisted, as before.
        events = [
            ProgressEvent(phase="start", message="go", percent=0.0),
            ProgressEvent(phase="phase_documents", message="d", percent=50.0),
            ProgressEvent(phase=terminal, message="end", percent=100.0),
        ]
        seen: list[tuple[str, BackupJobStatus, str | None]] = []

        async def on_event(ev: ProgressEvent) -> None:
            job = await BackupJob.find_one(BackupJob.job_id == fresh_job.job_id)
            assert job is not None
            seen.append((ev.phase, job.status, job.phase))

        task = await backup_service.start_async_job(
            fresh_job.job_id, _scripted_runner(events), on_event=on_event
        )
        await asyncio.wait_for(task, timeout=5.0)

        assert seen == [
            ("start", BackupJobStatus.RUNNING, "start"),
            ("phase_documents", BackupJobStatus.RUNNING, "phase_documents"),
            (terminal, BackupJobStatus.RUNNING, "phase_documents"),
        ]
        final = await BackupJob.find_one(BackupJob.job_id == fresh_job.job_id)
        assert final is not None and final.phase == terminal

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

        runner = _make_runner(
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

    async def test_the_requester_reaches_the_engine(self, tmp_path):
        """A namespace the restore creates is created as the requester, so the
        runner must hand the engine who that is."""
        engine = MagicMock()
        engine.run_restore = AsyncMock()
        runner = _make_runner(tmp_path / "a.zip", {"mode": "restore"})
        with (
            patch(
                "document_store.services.backup_engine.DirectRestoreEngine",
                return_value=engine,
            ) as engine_cls,
            patch(
                "document_store.services.file_storage_client.is_file_storage_enabled",
                return_value=False,
            ),
        ):
            await runner(lambda _event: None)
        assert engine_cls.call_args.kwargs["requester"] == _REQUESTER

    async def test_fresh_mode_runs_the_remap(self, tmp_path):
        engine = MagicMock()
        engine.run_remap = AsyncMock()
        engine.run_merge = AsyncMock()
        engine.run_restore = AsyncMock()

        runner = _make_runner(
            tmp_path / "a.zip",
            {"mode": "fresh", "target_namespace": "kb-copy", "dry_run": True,
             "drop_stale_reporting": True},
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
        engine.run_merge.assert_not_called()
        kwargs = engine.run_remap.call_args.kwargs
        assert kwargs["target_namespace"] == "kb-copy"
        assert kwargs["dry_run"] is True
        # A fresh restore honours the flag its refusal advises (CASE-1194, 6-R6).
        assert kwargs["drop_stale_reporting"] is True

    async def test_default_mode_runs_the_plain_restore(self, tmp_path):
        engine = MagicMock()
        engine.run_merge = AsyncMock()
        engine.run_remap = AsyncMock()
        engine.run_restore = AsyncMock()

        runner = _make_runner(
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
        engine.run_remap.assert_not_called()
        engine.run_restore.assert_called_once()


    @pytest.mark.parametrize(
        ("mode", "entry"),
        [("restore", "run_restore"), ("merge", "run_merge"), ("fresh", "run_remap")],
    )
    async def test_allow_failed_verify_reaches_every_entry_point(
        self, tmp_path, mode, entry
    ):
        engine = MagicMock()
        engine.run_merge = AsyncMock()
        engine.run_remap = AsyncMock()
        engine.run_restore = AsyncMock()

        runner = _make_runner(
            tmp_path / "a.zip",
            {"mode": mode, "target_namespace": "kb", "allow_failed_verify": True},
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

        assert getattr(engine, entry).call_args.kwargs["allow_failed_verify"] is True


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


class TestDryRunSideEffects:
    """A preview must not do anything a real run would.

    Observed live: a dry run into a namespace that did not exist spawned a
    validation job which reported "healthy, 0 documents", and tried a
    reporting batch sync. Both key off kind==RESTORE; neither checked
    dry_run.
    """

    async def test_a_dry_run_triggers_neither_sync_nor_validation(self, fresh_job):
        fresh_job.kind = BackupJobKind.RESTORE
        fresh_job.status = BackupJobStatus.RUNNING
        fresh_job.options = {"dry_run": True}
        await fresh_job.save()

        with (
            patch.object(
                backup_service, "_trigger_reporting_batch_sync", new=AsyncMock()
            ) as sync,
            patch.object(
                backup_service, "trigger_validation_for", new=AsyncMock()
            ) as validate,
        ):
            await backup_service._persist_event(
                fresh_job.job_id,
                ProgressEvent(phase="complete", message="dry run done", percent=100.0),
            )

        sync.assert_not_called()
        validate.assert_not_called()

    async def test_a_real_restore_still_triggers_both(self, fresh_job):
        fresh_job.kind = BackupJobKind.RESTORE
        fresh_job.status = BackupJobStatus.RUNNING
        fresh_job.options = {"dry_run": False}
        await fresh_job.save()

        with (
            patch.object(
                backup_service, "_trigger_reporting_batch_sync", new=AsyncMock()
            ) as sync,
            patch.object(
                backup_service, "trigger_validation_for", new=AsyncMock()
            ) as validate,
        ):
            await backup_service._persist_event(
                fresh_job.job_id,
                ProgressEvent(phase="complete", message="done", percent=100.0),
            )

        sync.assert_called_once()
        validate.assert_called_once()

    async def test_multi_target_restore_syncs_every_target(self, fresh_job):
        """A fresh restore with several write targets (namespace_map) syncs
        EACH of them — job.namespace alone would cover only the first, and
        the remaining targets would silently never reach PostgreSQL."""
        fresh_job.kind = BackupJobKind.RESTORE
        fresh_job.status = BackupJobStatus.RUNNING
        fresh_job.options = {"dry_run": False}
        fresh_job.namespace = "target-a"
        fresh_job.namespaces = ["target-a", "target-b"]
        await fresh_job.save()

        with (
            patch.object(
                backup_service, "_trigger_reporting_batch_sync", new=AsyncMock()
            ) as sync,
            patch.object(
                backup_service, "trigger_validation_for", new=AsyncMock()
            ),
        ):
            await backup_service._persist_event(
                fresh_job.job_id,
                ProgressEvent(phase="complete", message="done", percent=100.0),
            )

        assert sorted(c.args[0] for c in sync.call_args_list) == [
            "target-a", "target-b",
        ]

    async def test_namespaces_already_synced_by_the_count_phase_are_not_resynced(
        self, fresh_job
    ):
        """CASE-967: a plain or merge restore triggers each namespace's batch
        sync in its count phase and waits on it. Triggering again on
        completion re-synced every template from scratch — the duplicate
        second run observed after every large restore."""
        fresh_job.kind = BackupJobKind.RESTORE
        fresh_job.status = BackupJobStatus.RUNNING
        fresh_job.options = {"dry_run": False}
        fresh_job.namespace = "target-a"
        fresh_job.namespaces = ["target-a", "target-b"]
        await fresh_job.save()

        with (
            patch.object(
                backup_service, "_trigger_reporting_batch_sync", new=AsyncMock()
            ) as sync,
            patch.object(
                backup_service, "trigger_validation_for", new=AsyncMock()
            ) as validate,
        ):
            await backup_service._persist_event(
                fresh_job.job_id,
                ProgressEvent(phase="complete", message="done", percent=100.0,
                              details={"reporting_synced": ["target-a"]}),
            )

        # target-b's count phase did not trigger (reporting-sync unreachable,
        # say): the completion still syncs it.
        assert [c.args[0] for c in sync.call_args_list] == ["target-b"]
        validate.assert_called_once()

    async def test_archive_finalize_preserves_concurrent_field_writes(
        self, fresh_job, tmp_path
    ):
        """The archive lifecycle hook settles the input archive AFTER the
        terminal event, concurrent with the detached follow-up tasks. A
        writer landing during its slow bucket upload (here: the validation
        back-link) must survive the hook's own write — the hook once did a
        full-document save from its pre-upload copy and erased the link
        ~50ms after it was written."""
        from document_store.services.archive_store import ArchiveStore

        scratch = tmp_path / "input.zip"
        scratch.write_bytes(b"PK\x03\x04fake")
        fresh_job.kind = BackupJobKind.RESTORE
        fresh_job.archive_path = str(scratch)
        fresh_job.archive_backend = "local"
        await fresh_job.save()

        job_id = fresh_job.job_id

        async def upload_with_concurrent_writer(key, path):
            # Deterministic interleave: the back-link lands mid-upload.
            other = await backup_service.BackupJob.find_one(
                backup_service.BackupJob.job_id == job_id
            )
            await other.set(
                {backup_service.BackupJob.validation_job_ids: ["val-x"]}
            )

        storage = MagicMock()
        storage.exists = AsyncMock(return_value=False)
        storage.upload_file = AsyncMock(side_effect=upload_with_concurrent_writer)
        store = ArchiveStore(storage_client=storage)

        with (
            patch.object(type(store), "backend", property(lambda self: "minio")),
            patch.object(store, "_ensure_bucket", new=AsyncMock()),
        ):
            await store.finalize_restore_input(fresh_job)

        stored = await backup_service.BackupJob.find_one(
            backup_service.BackupJob.job_id == job_id
        )
        assert stored.validation_job_ids == ["val-x"]
        assert stored.archive_backend == "minio"
        assert stored.archive_path.endswith(f"{job_id}.zip") or "zip" in stored.archive_path


class TestPlanSurvivesOnTheJob:
    """The counts a dry run produces have to outlive the run.

    Progress events overwrite job.message, so the per-type plan was visible
    only to whoever happened to be streaming SSE at the time — and the plan is
    the entire reason to ask for a dry run.

    The result rides on the TERMINAL EVENT rather than being written in a
    second pass. The second pass raced this one and lost: the event consumer
    had already loaded the record, so its save put the result back to null.
    Observed on a live dry run, which is the only place the race showed.
    """

    async def test_the_terminal_events_details_land_on_the_record(self, fresh_job):
        fresh_job.status = BackupJobStatus.RUNNING
        await fresh_job.save()
        plan = {"mode": "fresh", "dry_run": True, "planned": {"documents": 7}}

        await backup_service._persist_event(
            fresh_job.job_id,
            ProgressEvent(
                phase="complete", message="done", percent=100.0, details=plan
            ),
        )

        updated = await BackupJob.find_one(BackupJob.job_id == fresh_job.job_id)
        assert updated.result == plan
        assert updated.status == BackupJobStatus.COMPLETE

    async def test_a_terminal_event_without_details_leaves_result_alone(
        self, fresh_job
    ):
        # Backups and plain restores carry no structured outcome; they must
        # not blank one that something else recorded.
        fresh_job.status = BackupJobStatus.RUNNING
        fresh_job.result = {"kept": True}
        await fresh_job.save()

        await backup_service._persist_event(
            fresh_job.job_id,
            ProgressEvent(phase="complete", message="done", percent=100.0),
        )

        updated = await BackupJob.find_one(BackupJob.job_id == fresh_job.job_id)
        assert updated.result == {"kept": True}



# Need asyncio mode for async tests in this module
pytestmark = pytest.mark.asyncio


class TestValidationResultSurvives:
    """CASE-750 — the integrity result rides the terminal event.

    The runner used to write job.result in a separate second pass after the
    work; that save raced the consumer's per-event load-modify-save and an
    in-flight progress event's full save put the result back to null. Small
    namespaces lost it almost always (tail events still draining at save
    time); the 252k-doc job survived because its queue had drained. Measured
    live: 3 of 4 fresh validate jobs on a quiet stack had result=null while
    message said "validation complete — healthy".
    """

    def _stub_integrity(self, *, status: str = "healthy", issues=None):
        from document_store.services.integrity_service import (
            IntegrityCheckResult,
            IntegrityIssue,
            IntegritySummary,
        )
        issues = issues or []
        return IntegrityCheckResult(
            status=status,
            summary=IntegritySummary(
                total_documents=40,
                documents_checked=40,
                documents_with_issues=len(issues),
            ),
            issues=[IntegrityIssue(**i) for i in issues],
        )

    async def test_result_survives_a_busy_event_queue(self, fresh_job: BackupJob):
        # Flood the queue with progress events right up to the end — the
        # exact condition under which the second-pass write lost the race.
        async def fake_check(namespace, progress=None, **_kw):
            if progress:
                for i in range(50):
                    progress(i + 1, 50)
            return self._stub_integrity()

        runner = backup_service.make_validation_runner(
            fresh_job.job_id, "probe-ns", {}
        )
        with patch(
            "document_store.services.integrity_service.check_all_documents",
            new=AsyncMock(side_effect=fake_check),
        ):
            task = await backup_service.start_async_job(fresh_job.job_id, runner)
            await asyncio.wait_for(task, timeout=10.0)

        updated = await BackupJob.find_one(BackupJob.job_id == fresh_job.job_id)
        assert updated is not None
        assert updated.status == BackupJobStatus.COMPLETE
        assert updated.result is not None, (
            "integrity result was lost — the terminal event must carry it"
        )
        assert updated.result["result_kind"] == "namespace_integrity"
        assert updated.result["status"] == "healthy"
        assert updated.result["summary"]["documents_checked"] == 40
        assert updated.result["issues"] == []
        assert updated.result["issues_truncated"] == 0

    async def test_unhealthy_result_lands_with_warning_and_findings(
        self, fresh_job: BackupJob
    ):
        issues = [{
            "type": "orphaned_term_ref",
            "severity": "warning",
            "document_id": "d-1",
            "template_id": "t-1",
            "version": 1,
            "reference": "TERM-GONE",
            "message": "term gone",
        }]

        async def fake_check(namespace, progress=None, **_kw):
            return self._stub_integrity(status="warning", issues=issues)

        runner = backup_service.make_validation_runner(
            fresh_job.job_id, "probe-ns", {}
        )
        with patch(
            "document_store.services.integrity_service.check_all_documents",
            new=AsyncMock(side_effect=fake_check),
        ):
            task = await backup_service.start_async_job(fresh_job.job_id, runner)
            await asyncio.wait_for(task, timeout=10.0)

        updated = await BackupJob.find_one(BackupJob.job_id == fresh_job.job_id)
        assert updated is not None
        assert updated.result is not None
        assert updated.result["status"] == "warning"
        assert len(updated.result["issues"]) == 1
        assert updated.result["issues"][0]["type"] == "orphaned_term_ref"
        # The warning event accumulated alongside, not instead of, the result.
        assert any("integrity warning" in w for w in updated.warnings)


class TestFieldScopedJobWrites:
    """No BackupJob.save() anywhere in the pipeline (CASE-749).

    The lost-update class: a writer holds a full document copy across an
    await while another writer lands, then save() full-replaces the record
    and silently erases the other's fields. It bit twice (the result
    null-out, the validation back-link clobber) before the remaining
    full-save sites were converted to field-scoped writes. These tests pin
    the conversion with the CASE-747 deterministic-interleave shape: a
    concurrent writer lands INSIDE the site's read-to-write window — after
    its find_one, before its write — and must survive. Under the old
    save() code every one of these fails.
    """

    def _set_with_interleaved_writer(self, job_id: str):
        """Wrap BackupJob.set so a concurrent field write lands exactly once,
        after the production read (its in-memory copy is already stale) and
        immediately before the production write — the lost-update window.

        The seam is the WRITE, not find_one: Beanie's own Document.set
        routes through find_one(...).update() internally, so patching
        find_one poisons the machinery under test. The concurrent write
        goes through the raw motor collection for the same reason. A
        regression back to full-document save() never enters this seam, so
        the concurrent write never lands and the survival assert fails —
        which is the point.
        """
        orig_set = BackupJob.set
        fired = {"done": False}

        async def wrapped(doc_self, expression, *args, **kwargs):
            if not fired["done"]:
                fired["done"] = True
                await BackupJob.get_motor_collection().update_one(
                    {"job_id": job_id},
                    {"$set": {"validation_job_ids": ["val-concurrent"]}},
                )
            return await orig_set(doc_self, expression, *args, **kwargs)

        return wrapped

    async def test_progress_event_preserves_concurrent_field_write(
        self, fresh_job: BackupJob
    ):
        job_id = fresh_job.job_id
        with patch.object(
            backup_service.BackupJob, "set",
            new=self._set_with_interleaved_writer(job_id),
        ):
            await backup_service._persist_event(
                job_id,
                ProgressEvent(phase="phase_documents", message="docs", percent=50.0),
            )

        stored = await BackupJob.find_one(BackupJob.job_id == job_id)
        assert stored.validation_job_ids == ["val-concurrent"]  # survived
        assert stored.phase == "phase_documents"  # and the event landed too
        assert stored.percent == 50.0

    async def test_terminal_event_preserves_concurrent_field_write(
        self, fresh_job: BackupJob
    ):
        job_id = fresh_job.job_id
        with patch.object(
            backup_service.BackupJob, "set",
            new=self._set_with_interleaved_writer(job_id),
        ):
            await backup_service._persist_event(
                job_id,
                ProgressEvent(phase="complete", message="done", percent=100.0),
            )

        stored = await BackupJob.find_one(BackupJob.job_id == job_id)
        assert stored.validation_job_ids == ["val-concurrent"]
        assert stored.status == BackupJobStatus.COMPLETE
        assert stored.percent == 100.0

    async def test_mark_failed_preserves_concurrent_field_write(
        self, fresh_job: BackupJob
    ):
        job_id = fresh_job.job_id
        with patch.object(
            backup_service.BackupJob, "set",
            new=self._set_with_interleaved_writer(job_id),
        ):
            await backup_service._mark_failed(job_id, "worker died")

        stored = await BackupJob.find_one(BackupJob.job_id == job_id)
        assert stored.validation_job_ids == ["val-concurrent"]
        assert stored.status == BackupJobStatus.FAILED
        assert stored.error == "worker died"
        assert stored.phase == "error"

    async def test_two_warning_events_both_survive(self, fresh_job: BackupJob):
        # The warning branch is an atomic $push — concurrent appends
        # compose instead of last-writer-wins.
        await asyncio.gather(
            backup_service._persist_event(
                fresh_job.job_id,
                ProgressEvent(phase="warning", message="first warning"),
            ),
            backup_service._persist_event(
                fresh_job.job_id,
                ProgressEvent(phase="warning", message="second warning"),
            ),
        )

        stored = await BackupJob.find_one(BackupJob.job_id == fresh_job.job_id)
        assert sorted(stored.warnings) == ["first warning", "second warning"]

    async def test_warning_event_touches_nothing_else(self, fresh_job: BackupJob):
        # A warning must not drag the rolling phase/message (or anything
        # else it holds in memory) along with it.
        await fresh_job.set({
            BackupJob.phase: "phase_documents",
            BackupJob.message: "in flight",
            BackupJob.percent: 40.0,
        })

        await backup_service._persist_event(
            fresh_job.job_id,
            ProgressEvent(phase="warning", message="heads up"),
        )

        stored = await BackupJob.find_one(BackupJob.job_id == fresh_job.job_id)
        assert stored.warnings == ["heads up"]
        assert stored.phase == "phase_documents"
        assert stored.message == "in flight"
        assert stored.percent == 40.0

    async def test_error_event_preserves_last_known_percent(
        self, fresh_job: BackupJob
    ):
        # The preserve-percent-on-error behavior survives the conversion:
        # percent is simply absent from the error event's field set.
        await fresh_job.set({BackupJob.percent: 70.0})

        await backup_service._persist_event(
            fresh_job.job_id,
            ProgressEvent(phase="error", message="boom"),
        )

        stored = await BackupJob.find_one(BackupJob.job_id == fresh_job.job_id)
        assert stored.percent == 70.0
        assert stored.status == BackupJobStatus.FAILED
        assert stored.error == "boom"

    async def test_no_backupjob_save_remains_in_backup_service(self):
        # The class-level guard: the module must not regrow a full-document
        # save. Source-level, deliberately blunt — any new `.save()` on a
        # BackupJob in this module is a regression of the whole class.
        import inspect

        source = inspect.getsource(backup_service)
        assert ".save()" not in source, (
            "backup_service must use field-scoped writes (set/Push), "
            "never BackupJob.save() — see the lost-update class this "
            "module's docstrings describe"
        )


class TestValidationMirrorsToParentRestore:
    """A validation spawned by a restore runs as its own job AFTER the
    restore completed — a caller who polled the restore to completion and
    read warnings=[] never learns the restored data is broken. A non-healthy
    result must land on the triggering restore job's warnings too."""

    def _stub_integrity(self, *, status: str = "healthy", issues=None):
        from document_store.services.integrity_service import (
            IntegrityCheckResult,
            IntegrityIssue,
            IntegritySummary,
        )
        issues = issues or []
        return IntegrityCheckResult(
            status=status,
            summary=IntegritySummary(
                total_documents=2,
                documents_checked=2,
                documents_with_issues=len(issues),
            ),
            issues=[IntegrityIssue(**i) for i in issues],
        )

    def _issue(self):
        return {
            "type": "orphaned_document_ref",
            "severity": "error",
            "document_id": "d-1",
            "template_id": "t-1",
            "version": 1,
            "reference": "d-gone",
            "message": "referenced document missing",
        }

    async def _run_validation(self, fresh_job, parent, status, issues):
        async def fake_check(namespace, progress=None, **_kw):
            return self._stub_integrity(status=status, issues=issues)

        runner = backup_service.make_validation_runner(
            fresh_job.job_id, "probe-ns",
            {"triggered_by": parent.job_id},
        )
        with patch(
            "document_store.services.integrity_service.check_all_documents",
            new=AsyncMock(side_effect=fake_check),
        ):
            task = await backup_service.start_async_job(fresh_job.job_id, runner)
            await asyncio.wait_for(task, timeout=10.0)

    async def test_unhealthy_validation_lands_on_parent(self, fresh_job: BackupJob):
        parent = BackupJob(
            job_id=f"rst-{uuid.uuid4().hex[:8]}",
            kind=BackupJobKind.RESTORE,
            namespace="probe-ns",
            created_by="test-admin",
        )
        await parent.insert()

        await self._run_validation(
            fresh_job, parent, "error", [self._issue()]
        )

        updated_parent = await BackupJob.find_one(
            BackupJob.job_id == parent.job_id
        )
        assert updated_parent is not None
        assert any(
            "post-restore validation" in w and fresh_job.job_id in w
            for w in updated_parent.warnings
        ), f"parent restore job must carry the pointer: {updated_parent.warnings}"

    async def test_healthy_validation_leaves_parent_untouched(
        self, fresh_job: BackupJob
    ):
        parent = BackupJob(
            job_id=f"rst-{uuid.uuid4().hex[:8]}",
            kind=BackupJobKind.RESTORE,
            namespace="probe-ns",
            created_by="test-admin",
        )
        await parent.insert()

        await self._run_validation(fresh_job, parent, "healthy", [])

        updated_parent = await BackupJob.find_one(
            BackupJob.job_id == parent.job_id
        )
        assert updated_parent is not None
        assert updated_parent.warnings == []


class TestTerminalEventPersistence:
    """A terminal event the record cannot take at once is retried, and a
    run that finished is never recorded as one that died without a word
    (CASE-1194, 6-R5)."""

    @staticmethod
    def _failing_terminal_writes(failures: int):
        real = backup_service._persist_event
        left = {"n": failures}

        async def persist(job_id, event):
            if event.phase == "complete" and left["n"] > 0:
                left["n"] -= 1
                raise RuntimeError("mongo blip")
            await real(job_id, event)
        return persist

    async def test_a_transient_failure_is_retried(self, fresh_job: BackupJob, monkeypatch):
        monkeypatch.setattr(backup_service, "_TERMINAL_PERSIST_DELAYS", (0.0, 0.0))
        monkeypatch.setattr(backup_service, "_persist_event", self._failing_terminal_writes(1))
        events = [ProgressEvent(phase="start", message="go", percent=0.0),
                  ProgressEvent(phase="complete", message="done", percent=100.0)]
        task = await backup_service.start_async_job(fresh_job.job_id, _scripted_runner(events))
        await asyncio.wait_for(task, timeout=5.0)

        updated = await BackupJob.find_one(BackupJob.job_id == fresh_job.job_id)
        assert updated.status == BackupJobStatus.COMPLETE

    async def test_a_lost_completion_says_the_run_completed(self, fresh_job: BackupJob, monkeypatch):
        monkeypatch.setattr(backup_service, "_TERMINAL_PERSIST_DELAYS", (0.0, 0.0))
        monkeypatch.setattr(backup_service, "_persist_event", self._failing_terminal_writes(99))
        events = [ProgressEvent(phase="start", message="go", percent=0.0),
                  ProgressEvent(phase="complete", message="restored 3 namespaces", percent=100.0)]
        task = await backup_service.start_async_job(fresh_job.job_id, _scripted_runner(events))
        await asyncio.wait_for(task, timeout=5.0)

        updated = await BackupJob.find_one(BackupJob.job_id == fresh_job.job_id)
        assert updated.status == BackupJobStatus.FAILED
        assert "the run completed" in updated.error
        assert "restored 3 namespaces" in updated.error
        assert "without terminal event" not in updated.error
        # A backup owes no reporting sync or validation: none is claimed.
        assert "follow-ups" not in updated.error

    async def test_a_restore_whose_completion_is_lost_still_gets_its_follow_ups(
        self, fresh_job: BackupJob, monkeypatch,
    ):
        """The restore's data is committed whatever the job record says, so
        the namespaces it wrote are still synced and verified."""
        fresh_job.kind = BackupJobKind.RESTORE
        fresh_job.namespaces = ["ns-a", "ns-b"]
        fresh_job.options = {"dry_run": False}
        await fresh_job.save()
        monkeypatch.setattr(backup_service, "_TERMINAL_PERSIST_DELAYS", (0.0, 0.0))
        monkeypatch.setattr(backup_service, "_persist_event", self._failing_terminal_writes(99))
        sync = AsyncMock(return_value=True)
        validate = AsyncMock(return_value=["val-1", "val-2"])
        monkeypatch.setattr(backup_service, "_trigger_reporting_batch_sync", sync)
        monkeypatch.setattr(backup_service, "trigger_validation_for", validate)
        events = [ProgressEvent(phase="start", message="go", percent=0.0),
                  ProgressEvent(phase="complete", message="done", percent=100.0,
                                details={"reporting_synced": ["ns-a"]})]
        task = await backup_service.start_async_job(fresh_job.job_id, _scripted_runner(events))
        await asyncio.wait_for(task, timeout=5.0)

        assert [c.args[0] for c in sync.call_args_list] == ["ns-b"]
        validate.assert_awaited_once()
        updated = await BackupJob.find_one(BackupJob.job_id == fresh_job.job_id)
        assert updated.status == BackupJobStatus.FAILED
        assert "reporting sync started" in updated.error
        assert "validation started (val-1, val-2)" in updated.error

    async def test_follow_ups_that_could_not_start_are_named_with_the_way_to_run_them(
        self, fresh_job: BackupJob, monkeypatch,
    ):
        """The outage that lost the completion usually stops the validation
        job's own insert too: the record must not claim it ran."""
        fresh_job.kind = BackupJobKind.RESTORE
        fresh_job.namespaces = ["ns-a"]
        fresh_job.options = {"dry_run": False}
        await fresh_job.save()
        monkeypatch.setattr(backup_service, "_TERMINAL_PERSIST_DELAYS", (0.0, 0.0))
        monkeypatch.setattr(backup_service, "_persist_event", self._failing_terminal_writes(99))
        monkeypatch.setattr(backup_service, "_trigger_reporting_batch_sync", AsyncMock(return_value=False))
        monkeypatch.setattr(backup_service, "trigger_validation_for", AsyncMock(return_value=[]))
        events = [ProgressEvent(phase="start", message="go", percent=0.0),
                  ProgressEvent(phase="complete", message="done", percent=100.0)]
        task = await backup_service.start_async_job(fresh_job.job_id, _scripted_runner(events))
        await asyncio.wait_for(task, timeout=5.0)

        updated = await BackupJob.find_one(BackupJob.job_id == fresh_job.job_id)
        assert "reporting sync could not be started for ns-a" in updated.error
        assert "validation could not be started" in updated.error
        assert "/validate" in updated.error
        assert "sync started" not in updated.error and "validation started" not in updated.error


    async def test_the_fallback_failed_write_is_retried(self, fresh_job: BackupJob, monkeypatch):
        monkeypatch.setattr(backup_service, "_TERMINAL_PERSIST_DELAYS", (0.0, 0.0))
        monkeypatch.setattr(backup_service, "_persist_event", self._failing_terminal_writes(99))
        real_mark = backup_service._mark_failed
        left = {"n": 1}

        async def flaky_mark(job_id, error):
            if left["n"] > 0:
                left["n"] -= 1
                raise RuntimeError("mongo blip")
            await real_mark(job_id, error)
        monkeypatch.setattr(backup_service, "_mark_failed", flaky_mark)
        events = [ProgressEvent(phase="start", message="go", percent=0.0),
                  ProgressEvent(phase="complete", message="done", percent=100.0)]
        task = await backup_service.start_async_job(fresh_job.job_id, _scripted_runner(events))
        await asyncio.wait_for(task, timeout=5.0)

        updated = await BackupJob.find_one(BackupJob.job_id == fresh_job.job_id)
        assert updated.status == BackupJobStatus.FAILED
        assert fresh_job.job_id not in backup_service._job_queues
        assert fresh_job.job_id not in backup_service._job_tasks

    async def test_the_job_entries_go_even_when_the_record_cannot_be_settled(
        self, fresh_job: BackupJob, monkeypatch,
    ):
        monkeypatch.setattr(backup_service, "_TERMINAL_PERSIST_DELAYS", (0.0, 0.0))
        monkeypatch.setattr(backup_service, "_persist_event", self._failing_terminal_writes(99))

        async def broken_mark(job_id, error):
            raise RuntimeError("mongo down")
        monkeypatch.setattr(backup_service, "_mark_failed", broken_mark)
        events = [ProgressEvent(phase="start", message="go", percent=0.0),
                  ProgressEvent(phase="complete", message="done", percent=100.0)]
        task = await backup_service.start_async_job(fresh_job.job_id, _scripted_runner(events))
        await asyncio.wait_for(task, timeout=5.0)

        assert fresh_job.job_id not in backup_service._job_queues
        assert fresh_job.job_id not in backup_service._job_tasks

    async def test_a_job_left_pending_is_marked_failed(self, fresh_job: BackupJob, monkeypatch):
        """Its 'start' never reached the record: with the run over, nothing
        else would ever move it out of PENDING."""
        monkeypatch.setattr(backup_service, "_TERMINAL_PERSIST_DELAYS", (0.0, 0.0))

        async def nothing_lands(job_id, event):
            raise RuntimeError("mongo blip")
        monkeypatch.setattr(backup_service, "_persist_event", nothing_lands)
        events = [ProgressEvent(phase="start", message="go", percent=0.0),
                  ProgressEvent(phase="error", message="boom")]
        task = await backup_service.start_async_job(fresh_job.job_id, _scripted_runner(events))
        await asyncio.wait_for(task, timeout=5.0)

        updated = await BackupJob.find_one(BackupJob.job_id == fresh_job.job_id)
        assert updated.status == BackupJobStatus.FAILED
        assert "boom" in updated.error


class TestReconcileOrphanedJobs:
    """A job a previous process left unfinished is marked failed at startup;
    a restore that was writing gets the follow-ups it owes."""

    async def _job(self, kind, status, **kw) -> BackupJob:
        job = BackupJob(job_id=f"job-{uuid.uuid4().hex[:8]}", kind=kind, status=status,
                        namespace="ns-a", namespaces=["ns-a"], created_by="test", **kw)
        await job.insert()
        return job

    async def test_a_running_restore_gets_its_follow_ups(self, fresh_job, monkeypatch):
        sync = AsyncMock(return_value=True)
        validate = AsyncMock(return_value=["val-9"])
        monkeypatch.setattr(backup_service, "_trigger_reporting_batch_sync", sync)
        monkeypatch.setattr(backup_service, "trigger_validation_for", validate)
        restore = await self._job(BackupJobKind.RESTORE, BackupJobStatus.RUNNING, options={"dry_run": False})
        dry = await self._job(BackupJobKind.RESTORE, BackupJobStatus.RUNNING, options={"dry_run": True})
        pending = await self._job(BackupJobKind.RESTORE, BackupJobStatus.PENDING, options={"dry_run": False})
        backup = await self._job(BackupJobKind.BACKUP, BackupJobStatus.RUNNING)

        assert await backup_service.reconcile_orphaned_jobs() >= 4

        def error(job):
            return BackupJob.find_one(BackupJob.job_id == job.job_id)
        settled = {j.job_id: await error(j) for j in (restore, dry, pending, backup)}
        assert all(j.status == BackupJobStatus.FAILED for j in settled.values())
        assert "validation started (val-9)" in settled[restore.job_id].error
        for other in (dry, pending, backup):
            assert settled[other.job_id].error == "Process terminated during execution (pod restart)"
        assert [c.args[0] for c in sync.call_args_list] == ["ns-a"]
        validate.assert_awaited_once()
