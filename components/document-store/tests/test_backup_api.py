"""Tests for the backup/restore REST endpoints (CASE-23 Phase 3 STEP 5).

These tests mock ``backup_service.start_job`` so no worker thread actually
runs the toolkit; the async/sync bridge itself is covered by
``test_backup_service.py`` and the loopback factories by
``test_backup_loopback.py``. The focus here is endpoint wiring:

* request parsing and validation
* BackupJob record creation + archive path bookkeeping
* permission checks on the job's namespace
* SSE stream media type + envelope shape
* download endpoint's status/kind guards
* delete endpoint's running-job guard and file cleanup
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from document_store.models.backup_job import (
    BackupJob,
    BackupJobKind,
    BackupJobStatus,
    BackupProgressMessage,
)


@pytest.fixture(autouse=True)
def _isolate_backup_dir(tmp_path, monkeypatch):
    """Redirect WIP_BACKUP_DIR to a per-test tmp dir so we never write to /tmp."""
    monkeypatch.setenv("WIP_BACKUP_DIR", str(tmp_path / "backups"))
    yield


@pytest.fixture(autouse=True)
async def _clean_backup_jobs():
    """Wipe BackupJob between tests so job_id uniqueness queries stay honest."""
    with contextlib.suppress(Exception):
        await BackupJob.delete_all()
    yield
    with contextlib.suppress(Exception):
        await BackupJob.delete_all()


async def _make_persisted_job(
    *,
    job_id: str | None = None,
    kind: BackupJobKind = BackupJobKind.BACKUP,
    namespace: str = "wip",
    status: BackupJobStatus = BackupJobStatus.PENDING,
    archive_path: str | None = None,
    archive_size: int | None = None,
    phase: str | None = None,
    percent: float | None = None,
    message: str | None = None,
) -> BackupJob:
    job = BackupJob(
        job_id=job_id or f"bkp-{uuid.uuid4().hex[:12]}",
        kind=kind,
        namespace=namespace,
        status=status,
        phase=phase,
        percent=percent,
        message=message,
        archive_path=archive_path,
        archive_size=archive_size,
        options={},
        created_by="test",
        created_at=datetime.now(UTC),
    )
    await job.insert()
    return job


# ---------------------------------------------------------------------------
# POST /backup/namespaces/{ns}/backup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_backup_creates_job_and_returns_snapshot(
    client: AsyncClient, auth_headers: dict
):
    fake_task = asyncio.get_running_loop().create_future()
    fake_task.set_result(None)
    with (
        patch(
            "document_store.api.backup.backup_service.make_direct_backup_runner",
            return_value=AsyncMock(),
        ) as mk_runner,
        patch(
            "document_store.api.backup.backup_service.start_async_job",
            new=AsyncMock(return_value=fake_task),
        ) as start_job,
    ):
        resp = await client.post(
            "/api/document-store/backup/namespaces/wip/backup",
            headers=auth_headers,
            json={"include_files": True, "latest_only": True},
        )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["kind"] == "backup"
    assert body["namespace"] == "wip"
    assert body["status"] == "pending"
    assert body["job_id"].startswith("bkp-")
    assert body["options"]["include_files"] is True
    assert body["options"]["latest_only"] is True

    # Runner factory received the snapshot options; start_async_job was called once.
    assert mk_runner.called
    _, kwargs = mk_runner.call_args
    assert kwargs["namespace"] == "wip"
    assert kwargs["options"]["include_files"] is True
    start_job.assert_awaited_once()

    # The BackupJob was persisted.
    stored = await BackupJob.find_one(BackupJob.job_id == body["job_id"])
    assert stored is not None
    assert stored.kind == BackupJobKind.BACKUP
    assert stored.archive_path and stored.archive_path.endswith(f"{body['job_id']}.zip")


# ---------------------------------------------------------------------------
# POST /backup/namespaces/{ns}/restore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_restore_streams_upload_and_creates_job(
    client: AsyncClient, auth_headers: dict, tmp_path
):
    fake_task = asyncio.get_running_loop().create_future()
    fake_task.set_result(None)

    payload = b"PK\x03\x04" + b"fake-archive-bytes" * 100  # ~1800 bytes

    with (
        patch(
            "document_store.api.backup.backup_service.make_direct_restore_runner",
            return_value=AsyncMock(),
        ),
        patch(
            "document_store.api.backup.backup_service.start_async_job",
            new=AsyncMock(return_value=fake_task),
        ),
    ):
        resp = await client.post(
            "/api/document-store/backup/namespaces/wip/restore",
            headers=auth_headers,
            files={"archive": ("backup.zip", payload, "application/zip")},
            data={"mode": "restore", "register_synonyms": "false"},
        )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["kind"] == "restore"
    assert body["namespace"] == "wip"
    assert body["archive_size"] == len(payload)
    assert body["options"]["mode"] == "restore"

    stored = await BackupJob.find_one(BackupJob.job_id == body["job_id"])
    assert stored is not None
    assert stored.archive_path is not None
    from pathlib import Path

    archive_file = Path(stored.archive_path)
    assert archive_file.exists()
    assert archive_file.read_bytes() == payload


@pytest.mark.asyncio
async def test_restore_rejects_invalid_mode(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        "/api/document-store/backup/namespaces/wip/restore",
        headers=auth_headers,
        files={"archive": ("b.zip", b"x", "application/zip")},
        data={"mode": "nuke"},
    )
    assert resp.status_code == 400
    assert "restore" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# GET /backup/jobs/{job_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_job_returns_snapshot(client: AsyncClient, auth_headers: dict):
    job = await _make_persisted_job(phase="phase_documents", percent=42.0)
    resp = await client.get(
        f"/api/document-store/backup/jobs/{job.job_id}", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == job.job_id
    assert body["phase"] == "phase_documents"
    assert body["percent"] == 42.0


@pytest.mark.asyncio
async def test_get_job_404(client: AsyncClient, auth_headers: dict):
    resp = await client.get(
        "/api/document-store/backup/jobs/does-not-exist", headers=auth_headers
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /backup/jobs — list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_jobs_filters_by_status(client: AsyncClient, auth_headers: dict):
    await _make_persisted_job(status=BackupJobStatus.COMPLETE)
    await _make_persisted_job(status=BackupJobStatus.COMPLETE)
    await _make_persisted_job(status=BackupJobStatus.FAILED)

    resp = await client.get(
        "/api/document-store/backup/jobs?status=complete&limit=50",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert all(j["status"] == "complete" for j in body)


# ---------------------------------------------------------------------------
# GET /backup/jobs/{job_id}/events — SSE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_stream_emits_progress_messages(
    client: AsyncClient, auth_headers: dict
):
    job = await _make_persisted_job(
        status=BackupJobStatus.COMPLETE,
        phase="complete",
        percent=100.0,
        message="all done",
    )
    async with client.stream(
        "GET",
        f"/api/document-store/backup/jobs/{job.job_id}/events",
        headers=auth_headers,
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        buf = b""
        async for chunk in resp.aiter_bytes():
            buf += chunk
            if b"progress" in buf:
                break

    text = buf.decode()
    assert ": connected" in text
    assert "event: progress" in text
    data_line = next(
        line for line in text.splitlines() if line.startswith("data: ")
    )
    payload = json.loads(data_line[len("data: "):])
    # Envelope MUST be a BackupProgressMessage — Guardrail 2.
    envelope = BackupProgressMessage.model_validate(payload)
    assert envelope.job_id == job.job_id
    assert envelope.status == BackupJobStatus.COMPLETE
    assert envelope.phase == "complete"
    assert envelope.percent == 100.0


@pytest.mark.asyncio
async def test_sse_stream_404_for_unknown_job(
    client: AsyncClient, auth_headers: dict
):
    resp = await client.get(
        "/api/document-store/backup/jobs/nope/events", headers=auth_headers
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /backup/jobs/{job_id}/download
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_complete_backup(
    client: AsyncClient, auth_headers: dict, tmp_path
):
    archive = tmp_path / "archive.zip"
    archive.write_bytes(b"PK\x03\x04REALZIP")
    job = await _make_persisted_job(
        status=BackupJobStatus.COMPLETE,
        archive_path=str(archive),
        archive_size=len(b"PK\x03\x04REALZIP"),
    )
    resp = await client.get(
        f"/api/document-store/backup/jobs/{job.job_id}/download",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.content == b"PK\x03\x04REALZIP"


@pytest.mark.asyncio
async def test_download_rejects_non_backup_job(
    client: AsyncClient, auth_headers: dict
):
    job = await _make_persisted_job(
        kind=BackupJobKind.RESTORE,
        status=BackupJobStatus.COMPLETE,
    )
    resp = await client.get(
        f"/api/document-store/backup/jobs/{job.job_id}/download",
        headers=auth_headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_download_rejects_incomplete_job(
    client: AsyncClient, auth_headers: dict
):
    job = await _make_persisted_job(status=BackupJobStatus.RUNNING)
    resp = await client.get(
        f"/api/document-store/backup/jobs/{job.job_id}/download",
        headers=auth_headers,
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# DELETE /backup/jobs/{job_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_removes_job_and_archive(
    client: AsyncClient, auth_headers: dict, tmp_path
):
    archive = tmp_path / "to-delete.zip"
    archive.write_bytes(b"bye")
    job = await _make_persisted_job(
        status=BackupJobStatus.COMPLETE, archive_path=str(archive)
    )
    resp = await client.delete(
        f"/api/document-store/backup/jobs/{job.job_id}", headers=auth_headers
    )
    assert resp.status_code == 204
    assert not archive.exists()
    assert await BackupJob.find_one(BackupJob.job_id == job.job_id) is None


@pytest.mark.asyncio
async def test_delete_rejects_running_job(client: AsyncClient, auth_headers: dict):
    job = await _make_persisted_job(status=BackupJobStatus.RUNNING)
    resp = await client.delete(
        f"/api/document-store/backup/jobs/{job.job_id}", headers=auth_headers
    )
    assert resp.status_code == 409
