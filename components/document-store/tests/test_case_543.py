"""Archive storage + retained-archive reuse (CASE-543).

Backup/restore archives now live in MinIO (dedicated bucket) when file
storage is enabled, with the local WIP_BACKUP_DIR demoted to scratch; without
MinIO there is no durable-retention promise (restore inputs deleted at
terminal state, backup outputs TTL-swept). On top of that storage model the
API gained: download of restore-job input archives, and restore-from-job
without re-uploading. These tests cover the ArchiveStore backends (MinIO via
a fake storage client), the terminal-state lifecycle hook, and the new
endpoint's wiring incl. the manifest-driven authorization and the
own-archive-copy rule.
"""

from __future__ import annotations

import contextlib
import uuid
import zipfile
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from document_store.models.backup_job import (
    BackupJob,
    BackupJobKind,
    BackupJobStatus,
)
from document_store.services.archive_store import (
    ArchiveStore,
    archive_lifecycle_hook,
    configure_archive_store,
    scratch_path_for,
)


@pytest.fixture(autouse=True)
def _isolate_backup_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WIP_BACKUP_DIR", str(tmp_path / "backups"))
    yield


@pytest.fixture(autouse=True)
async def _clean_backup_jobs():
    with contextlib.suppress(Exception):
        await BackupJob.delete_all()
    yield
    with contextlib.suppress(Exception):
        await BackupJob.delete_all()


@pytest.fixture(autouse=True)
def _reset_store_singleton():
    configure_archive_store(None)
    yield
    configure_archive_store(None)


class FakeStorage:
    """In-memory stand-in for FileStorageClient's archive-relevant surface."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.bucket_ensured = False

    async def ensure_bucket_exists(self):
        self.bucket_ensured = True
        return True

    async def upload_file(self, key, file_path, content_type="application/zip"):
        with open(file_path, "rb") as fh:
            self.objects[key] = fh.read()

    async def download_to_file(self, key, dest_path, chunk_size=1024 * 1024):
        data = self.objects[key]
        with open(dest_path, "wb") as fh:
            fh.write(data)
        return len(data)

    async def download_stream(self, key, chunk_size=64 * 1024):
        data = self.objects[key]
        for i in range(0, len(data), chunk_size):
            yield data[i:i + chunk_size]

    async def copy_object(self, source_key, dest_key):
        self.objects[dest_key] = self.objects[source_key]

    async def exists(self, key):
        return key in self.objects

    async def delete(self, key):
        self.objects.pop(key, None)


def _minio_store(monkeypatch) -> tuple[ArchiveStore, FakeStorage]:
    monkeypatch.setenv("WIP_FILE_STORAGE_ENABLED", "true")
    fake = FakeStorage()
    store = ArchiveStore(storage_client=fake)
    configure_archive_store(store)
    return store, fake


async def _job(
    *,
    kind=BackupJobKind.BACKUP,
    status=BackupJobStatus.COMPLETE,
    archive_path=None,
    archive_backend="local",
    namespace="wip",
    namespaces=None,
    job_prefix="bkp",
) -> BackupJob:
    job = BackupJob(
        job_id=f"{job_prefix}-{uuid.uuid4().hex[:12]}",
        kind=kind,
        namespace=namespace,
        namespaces=namespaces or [],
        status=status,
        archive_path=archive_path,
        archive_backend=archive_backend,
        options={},
        created_by="test",
        created_at=datetime.now(UTC),
    )
    await job.insert()
    return job


def _make_v3_archive(path, prefixes):
    """A minimal real v3 archive: just a parseable manifest."""
    from wip_archive.models import Manifest, NamespaceEntry

    manifest = Manifest(
        namespaces=[NamespaceEntry(prefix=p) for p in prefixes],
        namespace=prefixes[0] if len(prefixes) == 1 else "",
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", manifest.model_dump_json())


# ---------------------------------------------------------------------------
# ArchiveStore — backend behavior
# ---------------------------------------------------------------------------


class TestFinalizeBackup:
    @pytest.mark.asyncio
    async def test_local_mode_leaves_archive_in_scratch(self, client, monkeypatch):
        monkeypatch.delenv("WIP_FILE_STORAGE_ENABLED", raising=False)
        store = ArchiveStore()
        job = await _job(archive_path=None)
        scratch = scratch_path_for(job.job_id)
        scratch.write_bytes(b"ZIPDATA")
        job.archive_path = str(scratch)
        await job.save()

        await store.finalize_backup(job)

        assert scratch.exists()
        assert job.archive_backend == "local"

    @pytest.mark.asyncio
    async def test_minio_mode_uploads_and_unlinks_scratch(self, client, monkeypatch):
        store, fake = _minio_store(monkeypatch)
        job = await _job()
        scratch = scratch_path_for(job.job_id)
        scratch.write_bytes(b"ZIPDATA")
        job.archive_path = str(scratch)
        await job.save()

        await store.finalize_backup(job)

        assert not scratch.exists()
        assert fake.objects[f"{job.job_id}.zip"] == b"ZIPDATA"
        fresh = await BackupJob.find_one(BackupJob.job_id == job.job_id)
        assert fresh.archive_backend == "minio"
        assert fresh.archive_path == f"{job.job_id}.zip"

    @pytest.mark.asyncio
    async def test_minio_upload_failure_keeps_local_archive(self, client, monkeypatch):
        """A reachable local archive beats a recorded-but-absent object."""
        store, fake = _minio_store(monkeypatch)

        async def boom(*a, **k):
            from document_store.services.file_storage_client import FileStorageError
            raise FileStorageError("bucket unreachable")

        fake.upload_file = boom
        job = await _job()
        scratch = scratch_path_for(job.job_id)
        scratch.write_bytes(b"ZIPDATA")
        job.archive_path = str(scratch)
        await job.save()

        await store.finalize_backup(job)

        assert scratch.exists()
        fresh = await BackupJob.find_one(BackupJob.job_id == job.job_id)
        assert fresh.archive_backend == "local"


class TestFinalizeRestoreInput:
    @pytest.mark.asyncio
    async def test_local_mode_deletes_consumed_input(self, client, monkeypatch):
        monkeypatch.delenv("WIP_FILE_STORAGE_ENABLED", raising=False)
        store = ArchiveStore()
        job = await _job(kind=BackupJobKind.RESTORE, job_prefix="rst")
        scratch = scratch_path_for(job.job_id)
        scratch.write_bytes(b"INPUT")
        job.archive_path = str(scratch)
        await job.save()

        await store.finalize_restore_input(job)

        assert not scratch.exists()

    @pytest.mark.asyncio
    async def test_minio_mode_preserves_input_in_bucket(self, client, monkeypatch):
        store, fake = _minio_store(monkeypatch)
        job = await _job(kind=BackupJobKind.RESTORE, job_prefix="rst",
                         status=BackupJobStatus.FAILED)
        scratch = scratch_path_for(job.job_id)
        scratch.write_bytes(b"INPUT")
        job.archive_path = str(scratch)
        await job.save()

        await store.finalize_restore_input(job)

        assert not scratch.exists()
        assert fake.objects[f"{job.job_id}.zip"] == b"INPUT"
        fresh = await BackupJob.find_one(BackupJob.job_id == job.job_id)
        assert fresh.archive_backend == "minio"

    @pytest.mark.asyncio
    async def test_already_object_backed_just_removes_working_copy(
        self, client, monkeypatch
    ):
        """The restore-from-job path pre-copies the object; the terminal
        hook must only clean the scratch working copy."""
        store, fake = _minio_store(monkeypatch)
        job = await _job(kind=BackupJobKind.RESTORE, job_prefix="rst",
                         archive_backend="minio", archive_path=None)
        fake.objects[f"{job.job_id}.zip"] = b"COPIED"
        job.archive_path = f"{job.job_id}.zip"
        await job.save()
        working = scratch_path_for(job.job_id)
        working.write_bytes(b"COPIED")

        await store.finalize_restore_input(job)

        assert not working.exists()
        assert fake.objects[f"{job.job_id}.zip"] == b"COPIED"


class TestScratchSweep:
    def test_sweeps_only_expired(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WIP_BACKUP_RETENTION_HOURS", "1")
        store = ArchiveStore()
        old = scratch_path_for("bkp-old")
        new = scratch_path_for("bkp-new")
        old.write_bytes(b"x")
        new.write_bytes(b"x")
        import os
        two_hours_ago = __import__("time").time() - 7200
        os.utime(old, (two_hours_ago, two_hours_ago))

        removed = store.sweep_expired_scratch()

        assert removed == 1
        assert not old.exists()
        assert new.exists()

    def test_retention_zero_disables_sweep(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WIP_BACKUP_RETENTION_HOURS", "0")
        store = ArchiveStore()
        old = scratch_path_for("bkp-forever")
        old.write_bytes(b"x")
        import os
        long_ago = __import__("time").time() - 999999
        os.utime(old, (long_ago, long_ago))

        assert store.sweep_expired_scratch() == 0
        assert old.exists()


class TestLifecycleHook:
    @pytest.mark.asyncio
    async def test_hook_dispatches_on_terminal_phases_only(self, client, monkeypatch):
        _store, fake = _minio_store(monkeypatch)
        job = await _job()
        scratch = scratch_path_for(job.job_id)
        scratch.write_bytes(b"ZIPDATA")
        job.archive_path = str(scratch)
        await job.save()

        hook = archive_lifecycle_hook(job.job_id)

        class Event:
            def __init__(self, phase):
                self.phase = phase

        await hook(Event("phase_documents"))
        assert scratch.exists()  # non-terminal: untouched

        await hook(Event("complete"))
        assert not scratch.exists()
        assert f"{job.job_id}.zip" in fake.objects


# ---------------------------------------------------------------------------
# POST /backup/jobs/{job_id}/restore
# ---------------------------------------------------------------------------


def _patched_pipeline():
    return (
        patch(
            "document_store.api.backup.backup_service.make_direct_restore_runner",
            return_value=AsyncMock(),
        ),
        patch(
            "document_store.api.backup.backup_service.start_async_job",
            new=AsyncMock(return_value=None),
        ),
    )


class TestRestoreFromJob:
    @pytest.mark.asyncio
    async def test_creates_new_job_with_own_archive_copy(
        self, client: AsyncClient, auth_headers: dict
    ):
        src = await _job(namespaces=["wip"])
        archive = scratch_path_for(src.job_id)
        _make_v3_archive(archive, ["wip"])
        src.archive_path = str(archive)
        await src.save()

        p1, p2 = _patched_pipeline()
        with p1, p2:
            resp = await client.post(
                f"/api/document-store/backup/jobs/{src.job_id}/restore",
                headers=auth_headers,
                json={},
            )

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["kind"] == "restore"
        assert body["namespaces"] == ["wip"]
        assert body["options"]["restored_from_job"] == src.job_id

        new_job = await BackupJob.find_one(BackupJob.job_id == body["job_id"])
        assert new_job.archive_path != src.archive_path
        # The copy is independent: deleting the source file leaves it intact.
        new_copy = scratch_path_for(new_job.job_id)
        assert new_copy.exists()
        archive.unlink()
        assert new_copy.exists()

    @pytest.mark.asyncio
    async def test_missing_archive_is_410(
        self, client: AsyncClient, auth_headers: dict, tmp_path
    ):
        src = await _job(archive_path=str(tmp_path / "gone.zip"))
        resp = await client.post(
            f"/api/document-store/backup/jobs/{src.job_id}/restore",
            headers=auth_headers,
            json={},
        )
        assert resp.status_code == 410
        assert "retained" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_incomplete_backup_is_409(
        self, client: AsyncClient, auth_headers: dict
    ):
        src = await _job(status=BackupJobStatus.RUNNING)
        resp = await client.post(
            f"/api/document-store/backup/jobs/{src.job_id}/restore",
            headers=auth_headers,
            json={},
        )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_unknown_job_is_404(self, client: AsyncClient, auth_headers: dict):
        resp = await client.post(
            "/api/document-store/backup/jobs/bkp-doesnotexist/restore",
            headers=auth_headers,
            json={},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_manifest_authorization_refusal_cleans_up_the_copy(
        self, client: AsyncClient, auth_headers: dict
    ):
        """The archive manifest is authoritative for what a restore writes:
        a caller without admin on a carried namespace is refused, and the
        just-materialized archive copy must not be left behind."""
        src = await _job(namespaces=["wip"])
        archive = scratch_path_for(src.job_id)
        _make_v3_archive(archive, ["wip"])
        src.archive_path = str(archive)
        await src.save()

        before = set(archive.parent.glob("*.zip"))
        scoped_headers = {"X-API-Key": "test_scoped_key"}
        resp = await client.post(
            f"/api/document-store/backup/jobs/{src.job_id}/restore",
            headers=scoped_headers,
            json={},
        )
        assert resp.status_code in (403, 404)
        # No new job, no orphaned archive copy.
        assert set(archive.parent.glob("*.zip")) == before
        assert await BackupJob.find(
            BackupJob.kind == BackupJobKind.RESTORE
        ).count() == 0


# ---------------------------------------------------------------------------
# Restore upload records the manifest's namespaces on the job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restore_upload_records_manifest_namespaces(
    client: AsyncClient, auth_headers: dict, tmp_path
):
    """The upload restore already reads the manifest for authorization; the
    prefixes now land on the job record, giving download/restore-from-job
    their permission scope (and the jobs list its namespace report)."""
    archive = tmp_path / "upload.zip"
    _make_v3_archive(archive, ["wip"])

    p1, p2 = _patched_pipeline()
    with p1, p2:
        resp = await client.post(
            "/api/document-store/backup/namespaces/wip/restore",
            headers=auth_headers,
            files={"archive": ("upload.zip", archive.read_bytes(), "application/zip")},
            data={"mode": "restore"},
        )

    assert resp.status_code == 202, resp.text
    assert resp.json()["namespaces"] == ["wip"]


# ---------------------------------------------------------------------------
# CASE-800 — the archive moving mid-request must not truncate the download
# ---------------------------------------------------------------------------


class TestArchiveMovesDuringDownload:
    """A download holds the job record it loaded when the request started.
    finalize_backup can move the archive out from under it: upload, flip
    archive_backend/archive_path, then unlink the scratch file. The caller's
    copy still names a path that has just stopped existing.

    That window is guaranteed rather than unlucky — the terminal event is
    persisted (so pollers see COMPLETE) before the lifecycle hook runs at all,
    so a client that downloads the moment it sees a complete job aims straight
    at it. Failing there is especially bad because the route has already sent
    Content-Length: the client gets 200 and a short body, not an error.
    """

    @pytest.mark.asyncio
    async def test_stream_follows_the_archive_into_the_bucket(self, monkeypatch, tmp_path):
        store, _fake = _minio_store(monkeypatch)
        payload = b"PK\x03\x04" + b"archive-bytes" * 500

        job = await _job(archive_path=None)
        scratch = scratch_path_for(job.job_id)
        scratch.parent.mkdir(parents=True, exist_ok=True)
        scratch.write_bytes(payload)
        await job.set({BackupJob.archive_path: str(scratch)})

        # The download's copy of the record, loaded before the hook runs.
        stale = await BackupJob.find_one(BackupJob.job_id == job.job_id)

        await store.finalize_backup(job)
        assert not scratch.exists(), "precondition: the scratch copy is gone"
        assert stale.archive_backend == "local", "precondition: the caller's copy is stale"

        chunks = [chunk async for chunk in store.stream_archive(stale)]
        assert b"".join(chunks) == payload

    @pytest.mark.asyncio
    async def test_a_stale_job_still_reports_its_archive_as_retained(
        self, monkeypatch, tmp_path
    ):
        """archive_exists gates the download with a 410; the same staleness
        would report a perfectly good archive as un-retained."""
        store, _fake = _minio_store(monkeypatch)

        job = await _job(archive_path=None)
        scratch = scratch_path_for(job.job_id)
        scratch.parent.mkdir(parents=True, exist_ok=True)
        scratch.write_bytes(b"PK\x03\x04payload")
        await job.set({BackupJob.archive_path: str(scratch)})
        stale = await BackupJob.find_one(BackupJob.job_id == job.job_id)

        await store.finalize_backup(job)

        assert await store.archive_exists(stale) is True

    @pytest.mark.asyncio
    async def test_a_genuinely_missing_local_archive_still_fails(self, monkeypatch, tmp_path):
        """The fallback must not paper over a real loss. Without MinIO there is
        no bucket to fall back to, and a swept scratch file is simply gone —
        that has to keep surfacing as an error rather than an empty success."""
        monkeypatch.setenv("WIP_FILE_STORAGE_ENABLED", "false")
        store = ArchiveStore()
        configure_archive_store(store)

        job = await _job(archive_path=str(tmp_path / "never-existed.zip"))

        assert await store.archive_exists(job) is False
        with pytest.raises(FileNotFoundError):
            [chunk async for chunk in store.stream_archive(job)]
