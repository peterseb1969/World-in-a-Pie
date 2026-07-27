"""Archive storage for backup/restore jobs — MinIO-backed when available.

Backup/restore archives used to live only on the local filesystem at
``$WIP_BACKUP_DIR/{job_id}.zip`` — invisible to any blob store, unreachable
after a container reschedule, and swept by nothing. This module makes the
archive's durable home MinIO (a dedicated bucket, default ``wip-backups``)
whenever file storage is enabled, and demotes the local directory to what it
always really was: scratch space.

Two architectural constraints drive the design:

* **Archives are platform-meta, NOT File entities.** A restore archive must
  be retrievable precisely when the data it restores is gone — it cannot
  depend on Registry IDs or Mongo file records being intact, and a namespace
  full-delete must never sweep the archives that could undo it. So objects
  live under raw ``{job_id}.zip`` keys in their own bucket; the only
  metadata is on the ``BackupJob`` record.

* **Without MinIO there is no durable-retention promise.** Restore inputs
  are consumed and deleted at the job's terminal state. Backup outputs must
  outlive the async job contract (start → poll → download), so they stay in
  scratch and are swept opportunistically once older than
  ``WIP_BACKUP_RETENTION_HOURS`` (default 24; <= 0 disables the sweep).
  With MinIO the same sweep still runs — it catches scratch orphaned by a
  crash mid-job — but the archives themselves live in the bucket, where
  nothing expires them automatically (backups are precious; operators can
  attach bucket lifecycle rules if they want time-based retention).

The engines always read and write local files (a ZIP must be seekable), so
every MinIO interaction is upload-after-write or download-before-read with
the scratch path as the meeting point.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path

from ..models.backup_job import BackupJob, BackupJobKind
from .file_storage_client import (
    FileStorageClient,
    FileStorageError,
    is_file_storage_enabled,
)

logger = logging.getLogger("document_store.archive_store")

BACKEND_LOCAL = "local"
BACKEND_MINIO = "minio"


def scratch_dir() -> Path:
    """The local scratch/staging directory, created on first use."""
    path = Path(os.getenv("WIP_BACKUP_DIR", "/tmp/wip-backups"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def scratch_path_for(job_id: str) -> Path:
    return scratch_dir() / f"{job_id}.zip"


def _object_key_for(job_id: str) -> str:
    return f"{job_id}.zip"


def retention_hours() -> float:
    """Scratch retention window; <= 0 disables the sweep."""
    try:
        return float(os.getenv("WIP_BACKUP_RETENTION_HOURS", "24"))
    except ValueError:
        return 24.0


class ArchiveStore:
    """Backend-selecting archive storage for one document-store process."""

    def __init__(self, storage_client: FileStorageClient | None = None):
        self._client = storage_client
        self._bucket_ensured = False

    @property
    def backend(self) -> str:
        """Where NEW archives go. Existing jobs carry their own backend."""
        return BACKEND_MINIO if is_file_storage_enabled() else BACKEND_LOCAL

    def _storage(self) -> FileStorageClient:
        if self._client is None:
            self._client = FileStorageClient(
                bucket=os.getenv("WIP_BACKUP_BUCKET", "wip-backups")
            )
        return self._client

    async def _ensure_bucket(self) -> None:
        if not self._bucket_ensured:
            await self._storage().ensure_bucket_exists()
            self._bucket_ensured = True

    # ------------------------------------------------------------------
    # Existence / access
    # ------------------------------------------------------------------

    async def archive_exists(self, job: BackupJob) -> bool:
        if not job.archive_path:
            return False
        if job.archive_backend == BACKEND_MINIO:
            try:
                return await self._storage().exists(job.archive_path)
            except FileStorageError:
                return False
        if Path(job.archive_path).is_file():
            return True
        # A caller's copy of the job can predate finalize_backup moving the
        # archive into the bucket, in which case the scratch path it names is
        # gone but the archive is not. Answering False there reports a
        # perfectly good archive as un-retained.
        return await self._moved_to_bucket(job) is not None

    async def stream_archive(self, job: BackupJob) -> AsyncIterator[bytes]:
        """Chunked archive content for the download endpoint."""
        if job.archive_path is None:
            # Only a job that finished its backup carries an archive path;
            # callers reach here through download endpoints that already
            # require a completed job, so a None here is a broken invariant,
            # not a user error.
            raise FileNotFoundError(
                f"Backup job {job.job_id} has no archive_path — "
                "the backup never finalized an archive."
            )
        if job.archive_backend == BACKEND_MINIO:
            async for chunk in self._storage().download_stream(
                job.archive_path, chunk_size=1024 * 1024
            ):
                yield chunk
            return
        # Local file: read in an executor so a slow disk never blocks the
        # event loop mid-download.
        loop = asyncio.get_running_loop()
        try:
            # Opened outside a `with` on purpose: the open must be able to
            # fail HERE, before anything is yielded, so the fallback below can
            # still change course. Closed in the finally that guards the read
            # loop.
            fh = Path(job.archive_path).open("rb")  # noqa: SIM115
        except FileNotFoundError:
            # The archive moved out from under this request. finalize_backup
            # uploads the scratch copy, flips archive_backend/archive_path on
            # the record, and only then unlinks the scratch file — so a
            # download holding a job loaded before the flip names a path that
            # stops existing mid-request, while the record already points at
            # the object. The bytes are never lost: the unlink runs only after
            # a successful upload.
            #
            # Recovering here, BEFORE the first yield, is the whole point.
            # The download route sends Content-Length from the job, so an
            # exception raised once streaming has begun truncates a response
            # whose length was already promised — the client sees 200 plus a
            # short body rather than an error (CASE-800: 200 +
            # Content-Length: 5786 + zero bytes).
            key = await self._moved_to_bucket(job)
            if key is None:
                raise
            async for chunk in self._storage().download_stream(
                key, chunk_size=1024 * 1024
            ):
                yield chunk
            return
        try:
            while True:
                chunk = await loop.run_in_executor(None, fh.read, 1024 * 1024)
                if not chunk:
                    return
                yield chunk
        finally:
            fh.close()

    async def _moved_to_bucket(self, job: BackupJob) -> str | None:
        """The object key this job's archive now lives under, or None.

        Re-reads the record rather than deriving the key, so a scratch file
        that vanished for any OTHER reason (the TTL sweep on a local-only
        install) still reports honestly as gone. Deterministic, not a second
        race: finalize_backup flips the record BEFORE unlinking, so any job
        whose scratch file has disappeared under it already reads as MinIO.
        """
        if self.backend != BACKEND_MINIO:
            return None
        fresh = await BackupJob.find_one(BackupJob.job_id == job.job_id)
        if fresh is None or fresh.archive_backend != BACKEND_MINIO:
            return None
        return fresh.archive_path or None

    async def materialize_for_restore(self, src_job: BackupJob, new_job_id: str) -> Path:
        """Give a new restore job its OWN archive copy, staged locally.

        The new job must never share the source job's archive: DELETE on
        either job removes that job's archive, and a shared file/object
        would be pulled out from under the survivor. MinIO gets a
        server-side object copy (near-free) plus a scratch download for the
        engine to read; local mode gets a hardlink (same bytes, independent
        directory entry) with a plain copy as fallback.
        """
        if src_job.archive_path is None:
            # Restores materialize from a COMPLETED backup job; a missing
            # archive path means the source job never finalized.
            raise FileNotFoundError(
                f"Backup job {src_job.job_id} has no archive_path — "
                "cannot materialize an archive that was never finalized."
            )
        dest = scratch_path_for(new_job_id)
        if src_job.archive_backend == BACKEND_MINIO:
            await self._ensure_bucket()
            await self._storage().copy_object(
                src_job.archive_path, _object_key_for(new_job_id)
            )
            await self._storage().download_to_file(src_job.archive_path, str(dest))
            return dest
        src = Path(src_job.archive_path)
        try:
            os.link(src, dest)
        except OSError:
            import shutil
            shutil.copy2(src, dest)
        return dest

    # ------------------------------------------------------------------
    # Terminal-state finalization (called from the job-event hook)
    # ------------------------------------------------------------------

    async def finalize_backup(self, job: BackupJob) -> None:
        """A backup reached COMPLETE: move its archive to the durable home.

        MinIO mode uploads the scratch file to the bucket and deletes the
        scratch copy; if the upload fails, the archive stays local and the
        job record says so — a reachable local archive beats a recorded-but-
        absent object. Local mode leaves the file where the contract puts it
        (scratch, TTL-swept). Dry-run backups produce no file; nothing to do.
        """
        if not job.archive_path:
            return
        scratch = Path(job.archive_path)
        if not scratch.is_file():
            return
        if self.backend != BACKEND_MINIO:
            return
        key = _object_key_for(job.job_id)
        try:
            await self._ensure_bucket()
            await self._storage().upload_file(key, str(scratch))
        except FileStorageError:
            logger.exception(
                "Archive upload to bucket failed for %s — archive retained locally",
                job.job_id,
            )
            return
        # Atomic field update, never a full-document save: this runs after a
        # slow bucket upload, and other writers (the validation back-link)
        # land on the job during that await. A save() here replays the whole
        # pre-upload copy and silently erases their fields — observed live as
        # validation_job_ids flipping back to [] ~50ms after being written.
        await job.set({
            BackupJob.archive_backend: BACKEND_MINIO,
            BackupJob.archive_path: key,
        })
        with contextlib.suppress(OSError):
            scratch.unlink()

    async def finalize_restore_input(self, job: BackupJob) -> None:
        """A restore reached a terminal state: settle its input archive.

        The input is fully consumed either way, so the scratch copy always
        goes. With MinIO the archive is preserved in the bucket first —
        regardless of job outcome, since a FAILED restore's input is the
        most useful one to re-download — unless it is already there (the
        restore-from-job path pre-copies the object). Without MinIO the
        input is simply deleted: no durable-retention promise.
        """
        if not job.archive_path:
            return
        if job.archive_backend == BACKEND_MINIO:
            # Already object-backed (restore-from-job): scratch was a
            # working copy; remove it.
            with contextlib.suppress(OSError):
                scratch_path_for(job.job_id).unlink()
            return
        scratch = Path(job.archive_path)
        if not scratch.is_file():
            return
        if self.backend == BACKEND_MINIO:
            key = _object_key_for(job.job_id)
            try:
                await self._ensure_bucket()
                if not await self._storage().exists(key):
                    await self._storage().upload_file(key, str(scratch))
                # Atomic field update — see finalize_backup. This exact site
                # was the CASE-747 clobber: it held a copy across the upload
                # await while trigger_validation_for wrote the back-link,
                # then save() replaced the document from the stale copy.
                await job.set({
                    BackupJob.archive_backend: BACKEND_MINIO,
                    BackupJob.archive_path: key,
                })
            except FileStorageError:
                logger.exception(
                    "Restore-input upload to bucket failed for %s — input retained locally",
                    job.job_id,
                )
                return
        with contextlib.suppress(OSError):
            scratch.unlink()

    async def discard_failed_backup(self, job: BackupJob) -> None:
        """A backup FAILED: a partial ZIP is not an archive — remove it."""
        if job.archive_backend == BACKEND_LOCAL and job.archive_path:
            with contextlib.suppress(OSError):
                Path(job.archive_path).unlink()

    async def discard_materialized(self, new_job_id: str, object_copied: bool) -> None:
        """Undo materialize_for_restore when job creation aborts after it
        (e.g. the manifest-based authorization refuses the caller)."""
        with contextlib.suppress(OSError):
            scratch_path_for(new_job_id).unlink()
        if object_copied:
            with contextlib.suppress(FileStorageError):
                await self._storage().delete(_object_key_for(new_job_id))

    async def delete_archive(self, job: BackupJob) -> None:
        """Remove a job's archive from wherever it lives (job deletion)."""
        if not job.archive_path:
            return
        if job.archive_backend == BACKEND_MINIO:
            try:
                await self._storage().delete(job.archive_path)
            except FileStorageError:
                logger.exception(
                    "Failed to remove bucket archive for %s at %s",
                    job.job_id, job.archive_path,
                )
            # A scratch working copy may also exist (crash mid-run).
            with contextlib.suppress(OSError):
                scratch_path_for(job.job_id).unlink()
            return
        path = Path(job.archive_path)
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            logger.exception(
                "Failed to remove archive for %s at %s", job.job_id, path
            )

    # ------------------------------------------------------------------
    # Scratch hygiene
    # ------------------------------------------------------------------

    def sweep_expired_scratch(self) -> int:
        """Delete scratch archives older than the retention window.

        Called opportunistically when new jobs start — no cron needed. Only
        ever touches the local scratch directory; bucket objects are never
        expired by the platform. The age gate keeps it away from anything a
        running job could still be using.
        """
        hours = retention_hours()
        if hours <= 0:
            return 0
        cutoff = time.time() - hours * 3600
        removed = 0
        try:
            entries = list(scratch_dir().glob("*.zip"))
        except OSError:
            return 0
        for entry in entries:
            try:
                if entry.stat().st_mtime < cutoff:
                    entry.unlink()
                    removed += 1
            except OSError:
                continue
        if removed:
            logger.info("Swept %d expired scratch archive(s)", removed)
        return removed


def archive_lifecycle_hook(job_id: str):
    """Async per-event hook that settles a job's archive at terminal state.

    Wired into ``start_async_job(on_event=...)`` so it runs on the consumer
    task AFTER the terminal event has been persisted — sequential with the
    progress pipeline, but CONCURRENT with the detached follow-up tasks the
    terminal event schedules (batch sync, validation): those interleave into
    this hook's awaits and write their own fields on the same job document.
    The hook must therefore only ever write the fields it owns, via atomic
    updates — a full-document save from its held copy erases the concurrent
    writers' work. Only the ``phase`` attribute of the event is touched,
    keeping this module free of toolkit types.
    """

    async def hook(event) -> None:
        if event.phase not in ("complete", "error"):
            return
        job = await BackupJob.find_one(BackupJob.job_id == job_id)
        if job is None:
            return
        store = get_archive_store()
        if job.kind == BackupJobKind.BACKUP:
            if event.phase == "complete":
                await store.finalize_backup(job)
            else:
                await store.discard_failed_backup(job)
        else:
            await store.finalize_restore_input(job)

    return hook


_archive_store: ArchiveStore | None = None


def get_archive_store() -> ArchiveStore:
    global _archive_store
    if _archive_store is None:
        _archive_store = ArchiveStore()
    return _archive_store


def configure_archive_store(store: ArchiveStore | None) -> None:
    """Replace (or reset with None) the process singleton — for tests."""
    global _archive_store
    _archive_store = store
