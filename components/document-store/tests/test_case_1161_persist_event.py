"""Job progress persistence: the branches the suite did not reach, plus the
terminal transitions, pinned before _persist_event's refactor.

Provenance: CASE-1161. An event for a job that is gone is dropped with a
warning; a completed job's archive size is read from its archive file (and
left unset when the file is missing); a restore that wrote data gets its
reporting sync and validation scheduled after the terminal write, a dry run
and a backup get neither; a failure keeps its structured result.
"""

from __future__ import annotations

import logging
import uuid

import pytest
import pytest_asyncio
from wip_archive.models import ProgressEvent

from document_store.models.backup_job import BackupJob, BackupJobKind, BackupJobStatus
from document_store.services import backup_service


@pytest_asyncio.fixture(autouse=True)
async def _beanie(session_mongo_client):
    from tests.conftest import _ensure_beanie
    await _ensure_beanie(session_mongo_client)
    await BackupJob.delete_all()


@pytest.fixture
def follow_ups(monkeypatch):
    """Records the reporting syncs and validations a terminal event schedules."""
    seen: dict[str, list] = {"sync": [], "validate": []}

    async def sync(namespace):
        seen["sync"].append(namespace)

    async def validate(job):
        seen["validate"].append((job.job_id, job.status))

    monkeypatch.setattr(backup_service, "_trigger_reporting_batch_sync", sync)
    monkeypatch.setattr(backup_service, "trigger_validation_for", validate)
    monkeypatch.setattr(backup_service, "_track", lambda fut: fut)
    return seen


async def _job(kind=BackupJobKind.BACKUP, **kw) -> BackupJob:
    job = BackupJob(job_id=f"job-{uuid.uuid4().hex[:8]}", kind=kind, namespace="ns-a",
                    created_by="test", **kw)
    await job.insert()
    return job


async def _reload(job: BackupJob) -> BackupJob:
    found = await BackupJob.find_one(BackupJob.job_id == job.job_id)
    assert found is not None
    return found


async def _drain():
    # Let the scheduled follow-ups (ensure_future) run.
    import asyncio
    for _ in range(3):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_an_event_for_a_vanished_job_is_dropped_with_a_warning(caplog):
    with caplog.at_level(logging.WARNING, logger=backup_service.logger.name):
        await backup_service._persist_event("job-gone", ProgressEvent(phase="start", message="x"))
    assert "BackupJob job-gone disappeared while streaming progress" in caplog.text


@pytest.mark.asyncio
async def test_completion_reads_the_archive_size_from_its_file(tmp_path, follow_ups):
    archive = tmp_path / "a.zip"
    archive.write_bytes(b"x" * 1234)
    job = await _job(archive_path=str(archive))
    await backup_service._persist_event(
        job.job_id, ProgressEvent(phase="complete", message="done", details={"n": 1}),
    )
    job = await _reload(job)
    assert job.status == BackupJobStatus.COMPLETE and job.percent == 100.0
    assert job.archive_size == 1234 and job.result == {"n": 1}
    assert job.completed_at is not None
    await _drain()
    assert follow_ups == {"sync": [], "validate": []}


@pytest.mark.asyncio
async def test_a_missing_archive_file_leaves_the_size_unset(tmp_path, follow_ups):
    job = await _job(archive_path=str(tmp_path / "never-written.zip"))
    await backup_service._persist_event(job.job_id, ProgressEvent(phase="complete", message="done"))
    job = await _reload(job)
    assert job.status == BackupJobStatus.COMPLETE and job.archive_size is None and job.result is None


@pytest.mark.asyncio
async def test_a_restore_that_wrote_schedules_sync_and_validation_after_the_terminal_write(follow_ups):
    job = await _job(kind=BackupJobKind.RESTORE, namespaces=["ns-a", "ns-b", "ns-c"])
    await backup_service._persist_event(job.job_id, ProgressEvent(
        phase="complete", message="done", details={"reporting_synced": ["ns-b"]},
    ))
    await _drain()
    assert follow_ups["sync"] == ["ns-a", "ns-c"]
    assert follow_ups["validate"] == [(job.job_id, BackupJobStatus.COMPLETE)]
    assert (await _reload(job)).status == BackupJobStatus.COMPLETE


@pytest.mark.asyncio
async def test_a_dry_run_restore_schedules_nothing(follow_ups):
    job = await _job(kind=BackupJobKind.RESTORE, options={"dry_run": True})
    await backup_service._persist_event(job.job_id, ProgressEvent(phase="complete", message="done"))
    await _drain()
    assert follow_ups == {"sync": [], "validate": []}
    assert (await _reload(job)).status == BackupJobStatus.COMPLETE


@pytest.mark.asyncio
async def test_a_failure_keeps_its_structured_result_and_the_percent(follow_ups):
    job = await _job()
    await backup_service._persist_event(job.job_id, ProgressEvent(phase="start", message="go", percent=0.0))
    await backup_service._persist_event(job.job_id, ProgressEvent(phase="x", message="half", percent=50.0))
    await backup_service._persist_event(job.job_id, ProgressEvent(
        phase="error", message="broke", details={"result_kind": "refusal", "type": "e", "why": "w"},
    ))
    job = await _reload(job)
    assert job.status == BackupJobStatus.FAILED and job.error == "broke"
    assert job.percent == 50.0 and job.started_at is not None and job.completed_at is not None
    assert job.result == {"result_kind": "refusal", "why": "w"}


@pytest.mark.asyncio
async def test_warnings_accumulate_without_touching_the_phase(follow_ups):
    job = await _job()
    await backup_service._persist_event(job.job_id, ProgressEvent(phase="start", message="go"))
    for m in ("w1", "w2"):
        await backup_service._persist_event(job.job_id, ProgressEvent(phase="warning", message=m))
    job = await _reload(job)
    assert job.warnings == ["w1", "w2"] and job.phase == "start" and job.status == BackupJobStatus.RUNNING
