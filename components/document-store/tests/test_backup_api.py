"""Tests for the backup/restore REST endpoints (CASE-23 Phase 3 STEP 5).

These tests mock ``backup_service.start_async_job`` so no engine actually
runs; the job pipeline itself is covered by ``test_backup_service.py``.
The focus here is endpoint wiring:

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
        ) as start_async_job,
    ):
        resp = await client.post(
            "/api/document-store/backup/namespaces/wip/backup",
            headers=auth_headers,
            json={"include_files": True, "include_inactive": True},
        )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["kind"] == "backup"
    assert body["namespace"] == "wip"
    assert body["status"] == "pending"
    assert body["job_id"].startswith("bkp-")
    assert body["options"]["include_files"] is True
    assert body["options"]["include_inactive"] is True

    # Runner factory received the snapshot options; start_async_job was called once.
    assert mk_runner.called
    _, kwargs = mk_runner.call_args
    assert kwargs["namespaces"] == ["wip"]
    assert kwargs["options"]["include_files"] is True
    start_async_job.assert_awaited_once()

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
    # Unreadable manifest → prefixes is empty, so namespaces falls back to the
    # scalar namespace rather than staying blank (CASE-547).
    assert body["namespaces"] == ["wip"]
    assert body["archive_size"] == len(payload)
    assert body["options"]["mode"] == "restore"

    stored = await BackupJob.find_one(BackupJob.job_id == body["job_id"])
    assert stored is not None
    assert stored.archive_path is not None
    from pathlib import Path

    archive_file = Path(stored.archive_path)
    assert archive_file.exists()
    assert archive_file.read_bytes() == payload


async def _post_fresh(client, auth_headers, data):
    """POST a fresh restore with a fake archive and patched runner."""
    fake_task = asyncio.get_running_loop().create_future()
    fake_task.set_result(None)
    payload = b"PK\x03\x04" + b"fake-archive-bytes" * 100
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
        return await client.post(
            "/api/document-store/backup/namespaces/wip/restore",
            headers=auth_headers,
            files={"archive": ("backup.zip", payload, "application/zip")},
            data={"mode": "fresh", **data},
        )


@pytest.mark.asyncio
async def test_fresh_job_records_target_namespace_not_source(
    client: AsyncClient, auth_headers: dict
):
    """A fresh restore WRITES to its targets; the job must say so, because
    the post-restore sync and validation derive their scope from the job.
    Recording the URL path param here once made a map-only restore sync and
    'validate healthy' the SOURCE while the target went unchecked."""
    resp = await _post_fresh(
        client, auth_headers, {"target_namespace": "copy-ns"}
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["namespace"] == "copy-ns"
    assert body["namespaces"] == ["copy-ns"]


@pytest.mark.asyncio
async def test_fresh_job_records_map_targets(
    client: AsyncClient, auth_headers: dict
):
    resp = await _post_fresh(
        client, auth_headers,
        {"namespace_map": '{"kb": "copy-kb", "library": "copy-lib"}'},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["namespace"] == "copy-kb"
    assert sorted(body["namespaces"]) == ["copy-kb", "copy-lib"]


@pytest.mark.asyncio
async def test_fresh_job_records_collapsed_target_once(
    client: AsyncClient, auth_headers: dict
):
    """N:1 map — two sources into one target is ONE write target."""
    resp = await _post_fresh(
        client, auth_headers,
        {"namespace_map": '{"kb": "one", "library": "one"}'},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["namespace"] == "one"
    assert body["namespaces"] == ["one"]


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


@pytest.mark.asyncio
@pytest.mark.parametrize("dead_param", ["register_synonyms", "continue_on_error"])
async def test_restore_rejects_toolkit_era_params(
    client: AsyncClient, auth_headers: dict, dead_param: str
):
    """Setting a retired toolkit import param is a loud 400, never a silent no-op."""
    resp = await client.post(
        "/api/document-store/backup/namespaces/wip/restore",
        headers=auth_headers,
        files={"archive": ("b.zip", b"x", "application/zip")},
        data={"mode": "restore", dead_param: "true"},
    )
    assert resp.status_code == 400
    assert dead_param in resp.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dead_field", "value"),
    [
        ("skip_closure", True),
        ("skip_synonyms", True),
        ("template_prefixes", ["TPL-"]),
        ("dry_run", True),
        ("latest_only", True),
    ],
)
async def test_backup_rejects_toolkit_era_fields(
    client: AsyncClient, auth_headers: dict, dead_field: str, value
):
    """Setting a retired toolkit export field is a loud 400, never a silent no-op."""
    resp = await client.post(
        "/api/document-store/backup/namespaces/wip/backup",
        headers=auth_headers,
        json={dead_field: value},
    )
    assert resp.status_code == 400
    assert dead_field in resp.json()["detail"]


@pytest.mark.asyncio
async def test_restore_dry_run_reaches_the_runner(
    client: AsyncClient, auth_headers: dict
):
    """dry_run is forwarded into the job options the runner factory consumes.

    Regression guard for the accepted-but-dropped shape this param had: the
    endpoint stored it in options while the factory never passed it on, so a
    dry-run request ran a real restore.
    """
    fake_task = asyncio.get_running_loop().create_future()
    fake_task.set_result(None)
    with (
        patch(
            "document_store.api.backup.backup_service.make_direct_restore_runner",
            return_value=AsyncMock(),
        ) as mk_runner,
        patch(
            "document_store.api.backup.backup_service.start_async_job",
            new=AsyncMock(return_value=fake_task),
        ),
    ):
        resp = await client.post(
            "/api/document-store/backup/namespaces/wip/restore",
            headers=auth_headers,
            files={"archive": ("b.zip", b"PK\x03\x04x", "application/zip")},
            data={"mode": "restore", "dry_run": "true"},
        )

    assert resp.status_code == 202, resp.text
    assert resp.json()["options"]["dry_run"] is True
    _, kwargs = mk_runner.call_args
    assert kwargs["options"]["dry_run"] is True


# ---------------------------------------------------------------------------
# Merge mode surface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_mode_forwards_its_policies_to_the_runner(
    client: AsyncClient, auth_headers: dict
):
    """The clash policies are the merge's whole contract — they must reach the
    engine, not just the job record."""
    fake_task = asyncio.get_running_loop().create_future()
    fake_task.set_result(None)
    with (
        patch(
            "document_store.api.backup.backup_service.make_direct_restore_runner",
            return_value=AsyncMock(),
        ) as mk_runner,
        patch(
            "document_store.api.backup.backup_service.start_async_job",
            new=AsyncMock(return_value=fake_task),
        ),
    ):
        resp = await client.post(
            "/api/document-store/backup/namespaces/wip/restore",
            headers=auth_headers,
            files={"archive": ("b.zip", b"PK\x03\x04x", "application/zip")},
            data={
                "mode": "merge",
                "on_clash": "overwrite",
                "add_missing": "true",
            },
        )

    assert resp.status_code == 202, resp.text
    options = mk_runner.call_args.kwargs["options"]
    assert options["mode"] == "merge"
    assert options["on_clash"] == "overwrite"
    assert options["add_missing"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [("on_clash", "clobber")],
)
async def test_merge_rejects_unknown_policies(
    client: AsyncClient, auth_headers: dict, field: str, value: str
):
    resp = await client.post(
        "/api/document-store/backup/namespaces/wip/restore",
        headers=auth_headers,
        files={"archive": ("b.zip", b"x", "application/zip")},
        data={"mode": "merge", field: value},
    )
    assert resp.status_code == 400
    assert field in resp.json()["detail"]


@pytest.mark.asyncio
async def test_extend_terminologies_reaches_the_runner(
    client: AsyncClient, auth_headers: dict
):
    """The opt-ins decide whether the target's definitions may change, so they
    must reach the engine rather than sit on the job record."""
    fake_task = asyncio.get_running_loop().create_future()
    fake_task.set_result(None)
    with (
        patch(
            "document_store.api.backup.backup_service.make_direct_restore_runner",
            return_value=AsyncMock(),
        ) as mk_runner,
        patch(
            "document_store.api.backup.backup_service.start_async_job",
            new=AsyncMock(return_value=fake_task),
        ),
    ):
        resp = await client.post(
            "/api/document-store/backup/namespaces/wip/restore",
            headers=auth_headers,
            files={"archive": ("b.zip", b"PK\x03\x04x", "application/zip")},
            data={"mode": "merge", "extend_terminologies": "true"},
        )

    assert resp.status_code == 202, resp.text
    assert mk_runner.call_args.kwargs["options"]["extend_terminologies"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("on_clash", "overwrite"),
        ("add_missing", "true"),
        ("extend_terminologies", "true"),
    ],
)
async def test_plain_restore_rejects_merge_policies(
    client: AsyncClient, auth_headers: dict, field: str, value: str
):
    """A merge policy silently ignored on a plain restore would misrepresent
    what ran: the caller asked for clash handling and got an empty-target
    insert."""
    resp = await client.post(
        "/api/document-store/backup/namespaces/wip/restore",
        headers=auth_headers,
        files={"archive": ("b.zip", b"x", "application/zip")},
        data={"mode": "restore", field: value},
    )
    assert resp.status_code == 400
    assert field in resp.json()["detail"]


@pytest.mark.asyncio
async def test_merge_rejects_drop_stale_reporting(
    client: AsyncClient, auth_headers: dict
):
    """The target is live, so its reporting schema is expected to hold tables —
    dropping it would discard the data being merged into."""
    resp = await client.post(
        "/api/document-store/backup/namespaces/wip/restore",
        headers=auth_headers,
        files={"archive": ("b.zip", b"x", "application/zip")},
        data={"mode": "merge", "drop_stale_reporting": "true"},
    )
    assert resp.status_code == 400
    assert "drop_stale_reporting" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_fresh_mode_reaches_the_runner_with_its_target(
    client: AsyncClient, auth_headers: dict
):
    """mode='fresh' re-mints every identity, so the target it writes to has to
    reach the engine."""
    fake_task = asyncio.get_running_loop().create_future()
    fake_task.set_result(None)
    with (
        patch(
            "document_store.api.backup.backup_service.make_direct_restore_runner",
            return_value=AsyncMock(),
        ) as mk_runner,
        patch(
            "document_store.api.backup.backup_service.start_async_job",
            new=AsyncMock(return_value=fake_task),
        ),
    ):
        resp = await client.post(
            "/api/document-store/backup/namespaces/wip/restore",
            headers=auth_headers,
            files={"archive": ("b.zip", b"PK\x03\x04x", "application/zip")},
            data={"mode": "fresh", "target_namespace": "wip"},
        )

    assert resp.status_code == 202, resp.text
    options = mk_runner.call_args.kwargs["options"]
    assert options["mode"] == "fresh"
    assert options["target_namespace"] == "wip"


@pytest.mark.asyncio
async def test_fresh_mode_requires_a_target(
    client: AsyncClient, auth_headers: dict
):
    """It is placing new identities somewhere, so it cannot infer where from
    the archive the way the id-preserving modes do."""
    resp = await client.post(
        "/api/document-store/backup/namespaces/wip/restore",
        headers=auth_headers,
        files={"archive": ("b.zip", b"x", "application/zip")},
        data={"mode": "fresh"},
    )

    assert resp.status_code == 400
    assert "target_namespace" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_merge_policies_are_rejected_for_fresh_mode(
    client: AsyncClient, auth_headers: dict
):
    # A fresh restore writes into an empty namespace, so there is nothing for
    # a clash policy to resolve.
    resp = await client.post(
        "/api/document-store/backup/namespaces/wip/restore",
        headers=auth_headers,
        files={"archive": ("b.zip", b"x", "application/zip")},
        data={"mode": "fresh", "target_namespace": "wip", "on_clash": "overwrite"},
    )

    assert resp.status_code == 400
    assert "on_clash" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_an_unknown_mode_names_all_three(
    client: AsyncClient, auth_headers: dict
):
    resp = await client.post(
        "/api/document-store/backup/namespaces/wip/restore",
        headers=auth_headers,
        files={"archive": ("b.zip", b"x", "application/zip")},
        data={"mode": "nuke-it"},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "restore" in detail and "merge" in detail and "fresh" in detail


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
async def test_download_serves_restore_job_archive(
    client: AsyncClient, auth_headers: dict, tmp_path
):
    """Restore jobs' retained INPUT archives are downloadable — the old
    kind guard destroyed them on delete but refused to serve them. The
    input is valid regardless of job outcome, so even FAILED works."""
    archive = tmp_path / "restore-input.zip"
    archive.write_bytes(b"PK\x03\x04RESTOREINPUT")
    job = await _make_persisted_job(
        kind=BackupJobKind.RESTORE,
        status=BackupJobStatus.FAILED,
        archive_path=str(archive),
        archive_size=archive.stat().st_size,
    )
    resp = await client.get(
        f"/api/document-store/backup/jobs/{job.job_id}/download",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.content == b"PK\x03\x04RESTOREINPUT"


@pytest.mark.asyncio
async def test_download_restore_job_archive_gone_is_410(
    client: AsyncClient, auth_headers: dict, tmp_path
):
    """Without file storage, restore inputs are deleted at job completion —
    the download then reports the non-retention contract, not a 400."""
    job = await _make_persisted_job(
        kind=BackupJobKind.RESTORE,
        status=BackupJobStatus.COMPLETE,
        archive_path=str(tmp_path / "already-deleted.zip"),
    )
    resp = await client.get(
        f"/api/document-store/backup/jobs/{job.job_id}/download",
        headers=auth_headers,
    )
    assert resp.status_code == 410
    assert "retained" in resp.json()["detail"]


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


# ---------------------------------------------------------------------------
# Namespace validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_creates_a_job_and_forwards_its_options(
    client: AsyncClient, auth_headers: dict
):
    """The check runs as a job because it scans a whole namespace; the caller
    leaves with a job id, not a blocked connection."""
    fake_task = asyncio.get_running_loop().create_future()
    fake_task.set_result(None)
    with (
        patch(
            "document_store.api.backup.backup_service.make_validation_runner",
            return_value=AsyncMock(),
        ) as mk_runner,
        patch(
            "document_store.api.backup.backup_service.start_async_job",
            new=AsyncMock(return_value=fake_task),
        ),
    ):
        resp = await client.post(
            "/api/document-store/backup/namespaces/wip/validate"
            "?check_identity=false&limit=10",
            headers=auth_headers,
        )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["kind"] == "validate"
    assert body["namespace"] == "wip"
    _job_id, ns, options = mk_runner.call_args.args
    assert ns == "wip"
    assert options["check_identity"] is False
    assert options["limit"] == 10


@pytest.mark.asyncio
async def test_validation_findings_do_not_fail_the_job(
    client: AsyncClient, auth_headers: dict
):
    """A namespace with problems is a completed job carrying findings — the
    check ran, and its answer is the deliverable."""
    job = BackupJob(
        job_id=f"val-{uuid.uuid4().hex[:16]}",
        kind=BackupJobKind.VALIDATE,
        namespace="wip",
        namespaces=["wip"],
        status=BackupJobStatus.COMPLETE,
        result={
            "status": "error",
            "summary": {"documents_checked": 3, "documents_with_issues": 1},
            "issues": [{"type": "orphaned_document_ref"}],
            "issues_truncated": 0,
        },
        created_by="test",
    )
    await job.insert()

    resp = await client.get(
        f"/api/document-store/backup/jobs/{job.job_id}", headers=auth_headers
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "complete"
    assert body["result"]["status"] == "error"
    assert body["result"]["issues"][0]["type"] == "orphaned_document_ref"
