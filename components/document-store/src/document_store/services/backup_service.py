"""Backup/restore job orchestration.

One execution path: ``start_async_job`` + ``AsyncRunner``. The async
DirectBackupEngine / DirectRestoreEngine run as coroutines directly on the
event loop, feeding a queue→consumer→persist→SSE pipeline. (A second,
thread-based path for the retired sync-toolkit runners was removed once its
last producers went away.)

Design notes
------------
* **Per-job state is process-local.** The ``_job_queues`` dict lives in this
  module and only the worker that started the job can stream live events
  from its queue. The durable state is the ``BackupJob`` record in MongoDB,
  so status endpoints in *any* worker still work via polling.
* **The runner is a parameter**, not a hardcoded import, so tests can pass a
  fake runner that emits scripted events instead of running a real engine.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx as _httpx
from wip_toolkit.models import ProgressEvent

from ..models.backup_job import BackupJob, BackupJobKind, BackupJobStatus

logger = logging.getLogger("document_store.backup_service")

# Detached background tasks (fire-and-forget). Holding a strong reference
# keeps them alive against asyncio's weak-reference task tracking, which
# would otherwise GC them mid-flight. Tasks self-discard on completion.
# RUF006.
_bg_tasks: set[asyncio.Task[Any]] = set()


def _track(task: asyncio.Task[Any]) -> asyncio.Task[Any]:
    """Pin a detached task against GC; auto-discard on completion."""
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task


async def _trigger_reporting_batch_sync(namespace: str) -> None:
    """Fire-and-forget POST to reporting-sync's batch endpoint.

    Called after a successful restore so that the restored documents appear
    in PostgreSQL. Failures are logged but never propagated — reporting-sync
    is a convenience layer, not a correctness dependency.
    """
    url = os.getenv("REPORTING_SYNC_URL", "http://wip-reporting-sync:8005")
    api_key = cast(str, os.getenv("REGISTRY_API_KEY") or os.getenv("WIP_AUTH_LEGACY_API_KEY", ""))
    try:
        async with _httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{url}/api/reporting-sync/sync/batch",
                json={"namespace": namespace},
                headers={"X-API-Key": api_key},
            )
            logger.info(
                "Triggered reporting batch sync for namespace %s: HTTP %s",
                namespace, resp.status_code,
            )
    except Exception:
        logger.warning(
            "Could not trigger reporting batch sync for namespace %s (non-fatal)",
            namespace, exc_info=True,
        )


# Sentinel placed on the queue after the producer finishes (success or
# failure) so consumers know to stop awaiting. Using a module-level singleton
# keeps type narrowing simple (``event is _SENTINEL``).
class _Sentinel:
    """End-of-stream marker."""


_SENTINEL: _Sentinel = _Sentinel()

# Type of the coroutine function that runs a backup/restore job. Receives a
# progress_callback it must call at phase boundaries; raises on failure.
AsyncRunner = Callable[[Callable[[ProgressEvent], None]], Awaitable[Any]]

# Per-job in-process state (process-local by design — see module docstring).
_job_queues: dict[str, asyncio.Queue[ProgressEvent | _Sentinel]] = {}
_job_tasks: dict[str, asyncio.Task[None]] = {}


def _percent_for_status(status: BackupJobStatus, event: ProgressEvent) -> float | None:
    """Pick the percent to persist.

    Preserve the last-known percent on error events (which carry None) so the
    UI doesn't snap back to 0% on failure.
    """
    if event.phase == "error":
        return None
    return cast(float | None, event.percent)


async def _persist_event(job_id: str, event: ProgressEvent) -> None:
    """Apply a ProgressEvent to the BackupJob MongoDB record."""
    job = await BackupJob.find_one(BackupJob.job_id == job_id)
    if job is None:
        logger.warning("BackupJob %s disappeared while streaming progress", job_id)
        return

    # Transition from PENDING to RUNNING on the first 'start' event.
    if event.phase == "start" and job.status == BackupJobStatus.PENDING:
        job.status = BackupJobStatus.RUNNING
        job.started_at = datetime.now(UTC)

    # Warnings accumulate on the job record instead of overwriting the
    # rolling phase/message — a completed job with warnings succeeded; the
    # warnings say what to double-check (e.g. reporting parity incomplete).
    if event.phase == "warning":
        job.warnings.append(event.message)
        await job.save()
        return

    job.phase = event.phase
    job.message = event.message
    if event.percent is not None:
        job.percent = event.percent

    if event.phase == "complete":
        job.status = BackupJobStatus.COMPLETE
        job.percent = 100.0
        job.completed_at = datetime.now(UTC)
        # Populate archive_size from disk if the archive file exists.
        # This is the first opportunity after the backup engine has finalized
        # the ZIP; the API layer set archive_path at job creation but
        # cannot know the size until the worker writes the file.
        if job.archive_path:
            with contextlib.suppress(OSError):
                job.archive_size = Path(job.archive_path).stat().st_size
        # After a successful restore, trigger a batch sync so reporting-sync
        # picks up the restored documents in PostgreSQL. The restore engine
        # writes directly to MongoDB and bypasses the NATS event path that
        # reporting-sync normally subscribes to.
        if job.kind == BackupJobKind.RESTORE and job.namespace:
            _track(asyncio.ensure_future(_trigger_reporting_batch_sync(job.namespace)))
    elif event.phase == "error":
        job.status = BackupJobStatus.FAILED
        job.error = event.message
        job.completed_at = datetime.now(UTC)

    await job.save()


async def _mark_failed(job_id: str, error: str) -> None:
    """Persist a terminal FAILED state when the worker thread raises."""
    job = await BackupJob.find_one(BackupJob.job_id == job_id)
    if job is None:
        return
    job.status = BackupJobStatus.FAILED
    job.error = error
    job.phase = "error"
    job.completed_at = datetime.now(UTC)
    await job.save()


async def start_async_job(
    job_id: str,
    runner: AsyncRunner,
    *,
    on_event: Callable[[ProgressEvent], Awaitable[None]] | None = None,
) -> asyncio.Task[None]:
    """Kick off a backup/restore job.

    Args:
        job_id: The BackupJob.job_id of a previously persisted record.
        runner: A coroutine function that performs the job. It receives a
            progress_callback and is expected to call it at phase
            boundaries. Exceptions become FAILED status.
        on_event: Optional async hook called for every event as it is
            persisted. Currently unused — the SSE endpoint polls the
            persisted record instead of subscribing; kept as an
            extension point.

    Returns:
        The asyncio.Task that consumes the queue. The task completes when
        the runner finishes and the sentinel has been drained.

    Raises:
        ValueError: If a job with this id is already running in this process.
    """
    if job_id in _job_queues:
        raise ValueError(f"Job {job_id} is already running in this worker")

    queue: asyncio.Queue[ProgressEvent | _Sentinel] = asyncio.Queue()
    _job_queues[job_id] = queue

    def callback(event: ProgressEvent) -> None:
        queue.put_nowait(event)

    async def produce() -> None:
        try:
            await runner(callback)
        except Exception as exc:
            logger.exception("Async engine failed for job %s", job_id)
            err_event = ProgressEvent(
                phase="error",
                message=str(exc) or type(exc).__name__,
                details={"type": type(exc).__name__},
            )
            queue.put_nowait(err_event)
        finally:
            queue.put_nowait(_SENTINEL)

    async def consume() -> None:
        try:
            while True:
                item = await queue.get()
                if isinstance(item, _Sentinel):
                    return
                try:
                    await _persist_event(job_id, item)
                except Exception:
                    logger.exception("Failed to persist event for %s", job_id)
                if on_event is not None:
                    try:
                        await on_event(item)
                    except Exception:
                        logger.exception("on_event hook failed for %s", job_id)
        finally:
            job = await BackupJob.find_one(BackupJob.job_id == job_id)
            if job is not None and job.status == BackupJobStatus.RUNNING:
                await _mark_failed(job_id, "worker terminated without terminal event")
            _job_queues.pop(job_id, None)
            _job_tasks.pop(job_id, None)

    # Launch producer and consumer concurrently. The consumer task is
    # tracked in _job_tasks for status lookup; the producer is detached
    # (it finishes when the export/import is done and emits sentinel).
    # Pin the producer against asyncio GC via _track.
    _track(asyncio.create_task(produce(), name=f"backup-produce-{job_id}"))
    task = asyncio.create_task(consume(), name=f"backup-consume-{job_id}")
    _job_tasks[job_id] = task
    return task


def get_job_task(job_id: str) -> asyncio.Task[None] | None:
    """Return the consumer task for an in-process job, or None."""
    return _job_tasks.get(job_id)


async def wait_for_job(job_id: str, timeout: float | None = None) -> None:
    """Await the consumer task for a job, if it exists in this process."""
    task = _job_tasks.get(job_id)
    if task is None:
        return
    if timeout is None:
        await task
    else:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)


# ---------------------------------------------------------------------------
# Direct engine factories (CASE-23 redesign)
#
# These produce AsyncRunner callables that use DirectBackupEngine /
# DirectRestoreEngine with direct motor reads instead of HTTP fan-out.
# ---------------------------------------------------------------------------


def read_archive_manifest(archive_path: str | Path) -> Any | None:
    """Read an archive's manifest, or None if it is unreadable.

    Lives here (not in the API layer) so ``api/backup.py`` keeps its
    no-toolkit-imports guardrail intact — the manifest read used to be a
    function-local toolkit import inside the restore endpoint, which the
    guardrail's own verification grep would have flagged.
    """
    from wip_toolkit.archive import ArchiveReader

    try:
        with ArchiveReader(Path(archive_path)) as reader:
            return reader.read_manifest()
    except Exception as exc:
        logger.warning("Could not read manifest from archive %s: %s", archive_path, exc)
        return None


async def list_all_namespaces() -> list[str]:
    """Every namespace prefix the registry knows (CASE-542 'all' backup).

    Reads the registry's ``namespaces`` collection directly via the shared
    motor client (document-store shares the MongoDB instance). Includes 'wip'.
    """
    db_name = os.getenv("REGISTRY_DATABASE_NAME", "wip_registry")
    client = cast(Any, BackupJob.get_motor_collection().database.client)
    prefixes = await client[db_name]["namespaces"].distinct("prefix")
    return sorted(p for p in prefixes if p)


def make_direct_backup_runner(
    namespaces: str | list[str],
    archive_path: str | Path,
    options: dict[str, Any] | None = None,
) -> AsyncRunner:
    """Build an :data:`AsyncRunner` that backs up one or more namespaces via
    direct Mongo reads (CASE-542). A bare string is accepted for back-compat
    and treated as a single-element list."""
    ns_list = [namespaces] if isinstance(namespaces, str) else list(namespaces)
    opts = dict(options or {})
    backup_dir = os.getenv("WIP_BACKUP_DIR", "/tmp/wip-backups")
    Path(backup_dir).mkdir(parents=True, exist_ok=True)

    async def runner(progress_callback: Callable[[ProgressEvent], None]) -> Any:
        from .backup_engine import DirectBackupEngine
        from .file_storage_client import get_file_storage_client, is_file_storage_enabled

        mongo_client = cast(Any, BackupJob.get_motor_collection().database.client)
        storage = get_file_storage_client() if is_file_storage_enabled() else None
        engine = DirectBackupEngine(mongo_client, storage, progress_callback)
        await engine.run_backup(
            ns_list,
            Path(archive_path),
            include_files=opts.get("include_files", False),
            include_inactive=opts.get("include_inactive", False),
            skip_documents=opts.get("skip_documents", False),
            latest_only=opts.get("latest_only", False),
            tmp_dir=Path(backup_dir),
        )

    return runner


def make_direct_restore_runner(
    archive_path: str | Path,
    options: dict[str, Any] | None = None,
) -> AsyncRunner:
    """Build an :data:`AsyncRunner` that restores from ``archive_path`` via direct Mongo writes."""
    opts = dict(options or {})

    async def runner(progress_callback: Callable[[ProgressEvent], None]) -> Any:
        from .backup_engine import DirectRestoreEngine
        from .file_storage_client import get_file_storage_client, is_file_storage_enabled
        from .reporting_client import ReportingSyncClient

        mongo_client = cast(Any, BackupJob.get_motor_collection().database.client)
        storage = get_file_storage_client() if is_file_storage_enabled() else None
        engine = DirectRestoreEngine(
            mongo_client, storage, progress_callback,
            reporting_client=ReportingSyncClient(),
        )
        await engine.run_restore(
            Path(archive_path),
            target_namespace=opts.get("target_namespace", ""),
            skip_documents=opts.get("skip_documents", False),
            skip_files=opts.get("skip_files", False),
            batch_size=opts.get("batch_size", 500),
            drop_stale_reporting=opts.get("drop_stale_reporting", False),
        )

    return runner
