"""One restore or merge runs at a time on an instance.

Provenance: CASE-1283 (review of its first fix).

Every restore asks the object store whether each file key it would write is
free, then writes it. Two restores running at once could both find a key free
and both write it, the later write replacing the earlier one's bytes. A
restore that writes now claims the instance from its job's insert until the
job is terminal; the claim is a partial unique index, so a second restore's
insert is refused atomically (409, naming the running job). A dry run writes
nothing and is not held back; neither is a backup or a validation. A restore
the process left running is failed at startup, which frees the claim.
"""

from __future__ import annotations

import asyncio
import io
import json
import uuid
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from wip_archive.models import ProgressEvent

from document_store.models.backup_job import BackupJob, BackupJobKind, BackupJobStatus
from document_store.services import backup_service

RESTORE_URL = "/api/document-store/backup/namespaces/wip/restore"


def _archive() -> bytes:
    manifest = {
        "format_version": "3.0", "namespace": "wip",
        "namespaces": [{"prefix": "wip"}], "counts": {},
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _backup_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WIP_BACKUP_DIR", str(tmp_path / "backups"))


@pytest.fixture(autouse=True)
async def _no_jobs(client):
    await BackupJob.delete_all()
    yield
    await BackupJob.delete_all()


@pytest.fixture
async def no_runs():
    """The engines do not run: a job stays as its insert left it."""
    done = asyncio.get_running_loop().create_future()
    done.set_result(None)
    with (
        patch.object(backup_service, "make_direct_restore_runner", return_value=AsyncMock()),
        patch.object(backup_service, "make_direct_backup_runner", return_value=AsyncMock()),
        patch.object(backup_service, "start_async_job", new=AsyncMock(return_value=done)),
    ):
        yield


async def _restore(client: AsyncClient, headers: dict, **data: str):
    return await client.post(
        RESTORE_URL, headers=headers,
        files={"archive": ("b.zip", _archive(), "application/zip")},
        data={"mode": "restore", **data},
    )


@pytest.mark.asyncio
async def test_a_second_restore_is_refused_while_one_runs(
    client: AsyncClient, auth_headers: dict, no_runs, tmp_path,
):
    first = await _restore(client, auth_headers)
    assert first.status_code == 202, first.text
    running = first.json()["job_id"]

    for mode in ("restore", "merge"):
        second = await _restore(client, auth_headers, mode=mode)
        assert second.status_code == 409, second.text
        assert running in second.json()["detail"]

    # Nothing of the refused uploads is kept: one job, one archive.
    assert [j.job_id for j in await BackupJob.find_all().to_list()] == [running]
    assert [p.name for p in (tmp_path / "backups").iterdir()] == [f"{running}.zip"]


@pytest.mark.asyncio
async def test_a_dry_run_and_a_backup_are_not_held_back(
    client: AsyncClient, auth_headers: dict, no_runs,
):
    assert (await _restore(client, auth_headers)).status_code == 202

    dry = await _restore(client, auth_headers, dry_run="true")
    assert dry.status_code == 202, dry.text
    backup = await client.post(
        "/api/document-store/backup/namespaces/wip/backup", json={}, headers=auth_headers,
    )
    assert backup.status_code == 202, backup.text
    # A dry run claims nothing, so a real restore still meets only the first.
    assert (await _restore(client, auth_headers)).status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["complete", "error"])
async def test_a_finished_restore_frees_the_instance(
    client: AsyncClient, auth_headers: dict, no_runs, phase,
):
    first = await _restore(client, auth_headers, dry_run="false")
    job_id = first.json()["job_id"]

    await backup_service._persist_event(job_id, ProgressEvent(phase=phase, message="done"))

    job = await BackupJob.find_one(BackupJob.job_id == job_id)
    assert job is not None and job.running_restore is None
    with patch.object(backup_service, "_schedule_restore_follow_ups"):
        assert (await _restore(client, auth_headers)).status_code == 202


@pytest.mark.asyncio
async def test_a_restore_a_crash_left_running_does_not_block_the_next(
    client: AsyncClient, auth_headers: dict, no_runs,
):
    first = await _restore(client, auth_headers)
    job_id = first.json()["job_id"]
    await BackupJob.find_one(BackupJob.job_id == job_id).set({BackupJob.status: BackupJobStatus.RUNNING})

    with patch.object(backup_service, "_start_follow_ups_now", new=AsyncMock(return_value="")):
        assert await backup_service.reconcile_orphaned_jobs() == 1

    assert (await _restore(client, auth_headers)).status_code == 202


@pytest.mark.asyncio
async def test_a_restore_from_a_job_is_refused_while_one_runs(
    client: AsyncClient, auth_headers: dict, no_runs, tmp_path,
):
    running = (await _restore(client, auth_headers)).json()["job_id"]
    archive = tmp_path / "backups" / "bkp-source.zip"
    archive.write_bytes(_archive())
    source = BackupJob(
        job_id=f"bkp-{uuid.uuid4().hex[:16]}", kind=BackupJobKind.BACKUP, namespace="wip",
        namespaces=["wip"], status=BackupJobStatus.COMPLETE, archive_path=str(archive),
        options={}, created_by="test",
    )
    await source.insert()

    resp = await client.post(
        f"/api/document-store/backup/jobs/{source.job_id}/restore",
        json={"mode": "restore"}, headers=auth_headers,
    )

    assert resp.status_code == 409, resp.text
    assert running in resp.json()["detail"]
    assert sorted(p.name for p in Path(tmp_path / "backups").iterdir()) == [
        "bkp-source.zip", f"{running}.zip",
    ]
