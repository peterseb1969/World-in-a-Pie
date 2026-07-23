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
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx as _httpx
from beanie.odm.operators.update.array import Push
from wip_archive.exceptions import ArchiveError
from wip_archive.models import ProgressEvent

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
            # Query parameter, not JSON body — the route reads the query
            # string; a body is silently ignored and the sync degrades to
            # whole-instance.
            resp = await client.post(
                f"{url}/api/reporting-sync/sync/batch",
                params={"namespace": namespace},
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


async def _persist_event(job_id: str, event: ProgressEvent) -> None:
    """Apply a ProgressEvent to the BackupJob MongoDB record.

    Every write here is field-scoped (atomic $set / $push), never a
    full-document save. This pipeline runs concurrently with detached
    writers on the same record — the validation back-link, the archive
    lifecycle hook, any future follow-up — and a full save from a copy
    loaded before their write silently erases it. That lost-update class
    bit twice (the result null-out, the back-link clobber) before the
    remaining sites here were converted; each writer now touches exactly
    the fields it owns, so interleaving cannot destroy another's write.
    """
    job = await BackupJob.find_one(BackupJob.job_id == job_id)
    if job is None:
        logger.warning("BackupJob %s disappeared while streaming progress", job_id)
        return

    # Warnings accumulate on the job record instead of overwriting the
    # rolling phase/message — a completed job with warnings succeeded; the
    # warnings say what to double-check (e.g. reporting parity incomplete).
    # An atomic push: two warnings landing concurrently both survive, and
    # nothing else on the record is touched.
    if event.phase == "warning":
        await job.update(Push({BackupJob.warnings: event.message}))
        return

    fields: dict[Any, Any] = {
        BackupJob.phase: event.phase,
        BackupJob.message: event.message,
    }

    # Transition from PENDING to RUNNING on the first 'start' event. The
    # read-then-set is safe single-writer: the per-job queue serializes
    # this pipeline's events in-process.
    if event.phase == "start" and job.status == BackupJobStatus.PENDING:
        fields[BackupJob.status] = BackupJobStatus.RUNNING
        fields[BackupJob.started_at] = datetime.now(UTC)

    # Percent is preserved on error events (which carry None) so the UI
    # doesn't snap back to 0% on failure — simply not written here.
    if event.percent is not None:
        fields[BackupJob.percent] = event.percent

    if event.phase == "complete":
        fields[BackupJob.status] = BackupJobStatus.COMPLETE
        fields[BackupJob.percent] = 100.0
        fields[BackupJob.completed_at] = datetime.now(UTC)
        # A structured outcome rides on the terminal event rather than being
        # written separately afterwards. Saving it in a second pass raced this
        # one: the consumer had already loaded the record, so its save put the
        # result back to null. Observed exactly that on a live dry run.
        if event.details:
            fields[BackupJob.result] = dict(event.details)
        # Populate archive_size from disk if the archive file exists.
        # This is the first opportunity after the backup engine has finalized
        # the ZIP; the API layer set archive_path at job creation but
        # cannot know the size until the worker writes the file.
        if job.archive_path:
            with contextlib.suppress(OSError):
                fields[BackupJob.archive_size] = Path(job.archive_path).stat().st_size
        # After a successful restore, trigger a batch sync so reporting-sync
        # picks up the restored documents in PostgreSQL. The restore engine
        # writes directly to MongoDB and bypasses the NATS event path that
        # reporting-sync normally subscribes to.
        #
        # A dry run gets neither this nor the validation below. It wrote
        # nothing, so there is nothing to sync and nothing to verify — and a
        # preview that spawns follow-up jobs against a namespace it did not
        # create is not a preview. Observed doing exactly that: a dry run into
        # a non-existent namespace produced a validation job reporting
        # "healthy, 0 documents".
        if (
            job.kind == BackupJobKind.RESTORE
            and job.namespace
            and not job.options.get("dry_run")
        ):
            # Persist the terminal state BEFORE scheduling the follow-ups —
            # they read the record and must see it terminal. (The write
            # being field-scoped already protects the validation back-link
            # from being clobbered; the ordering keeps the follow-ups from
            # observing a job that still looks RUNNING.)
            await job.set(fields)
            # One sync per namespace the restore WROTE — a multi-target fresh
            # restore (namespace_map with several targets) needs each target
            # synced; job.namespace alone would cover only the first.
            for target in dict.fromkeys(job.namespaces or [job.namespace]):
                _track(asyncio.ensure_future(_trigger_reporting_batch_sync(target)))
            # And verify what was written. A restore validates nothing while
            # writing, so this is the only thing that would notice a dangling
            # reference or an identity hash that no longer matches its data.
            # It runs as its own job and the restore does not wait for it —
            # the data is committed either way, and blocking a fast restore on
            # verification would defeat the point.
            _track(asyncio.ensure_future(trigger_validation_for(job)))
            return
    elif event.phase == "error":
        fields[BackupJob.status] = BackupJobStatus.FAILED
        fields[BackupJob.error] = event.message
        fields[BackupJob.completed_at] = datetime.now(UTC)

    await job.set(fields)


async def _mark_failed(job_id: str, error: str) -> None:
    """Persist a terminal FAILED state when the worker thread raises."""
    job = await BackupJob.find_one(BackupJob.job_id == job_id)
    if job is None:
        return
    await job.set({
        BackupJob.status: BackupJobStatus.FAILED,
        BackupJob.error: error,
        BackupJob.phase: "error",
        BackupJob.completed_at: datetime.now(UTC),
    })


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
    """Read an archive's manifest.

    A malformed archive raises a typed ``ArchiveError`` (not-a-zip, no
    manifest, unparseable manifest) so the restore route can refuse it with a
    400 at upload time instead of minting a job that fails later. Only a
    genuinely UNEXPECTED read error falls through to None (logged) — the caller
    treats an absent manifest as "proceed with the request's target".

    Lives here (not in the API layer) so ``api/backup.py`` keeps its
    no-toolkit-imports guardrail intact — the manifest read used to be a
    function-local toolkit import inside the restore endpoint, which the
    guardrail's own verification grep would have flagged.
    """
    from wip_archive.archive import ArchiveReader

    try:
        with ArchiveReader(Path(archive_path)) as reader:
            return reader.read_manifest()
    except ArchiveError:
        raise  # typed malformed-archive error → the route turns it into a 400
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
            skip_documents=opts.get("skip_documents", False),
            latest_only=opts.get("latest_only", False),
            tmp_dir=Path(backup_dir),
        )

    return runner


# Issues stored on the job record. A namespace with a systematic problem
# produces one issue per document; the full set belongs in a re-run with the
# endpoint, not in a job record other endpoints page through.
VALIDATION_ISSUE_SAMPLE = 50


def make_validation_runner(
    job_id: str,
    namespace: str,
    options: dict[str, Any] | None = None,
) -> AsyncRunner:
    """Build an :data:`AsyncRunner` that verifies one namespace's integrity.

    Restore writes without validating — deliberately, for speed — so this is
    where a restored namespace gets checked: every reference resolves, and
    every identity hash still matches its own document's data.

    The outcome lands on the job record rather than being returned, because
    the caller is an HTTP client that already left with a job id.
    """
    opts = dict(options or {})

    async def runner(progress_callback: Callable[[ProgressEvent], None]) -> Any:
        from .integrity_service import check_all_documents

        progress_callback(ProgressEvent(
            phase="start",
            message=f"Validating namespace '{namespace}'",
            percent=0,
        ))

        def on_progress(checked: int, total: int) -> None:
            progress_callback(ProgressEvent(
                phase="phase_validate",
                message=f"[{namespace}] checked {checked}/{total} document(s)",
                percent=min(round(checked / total * 95, 1), 95.0) if total else 95.0,
                current=checked,
                total=total,
            ))

        result = await check_all_documents(
            namespace=namespace,
            check_term_refs=opts.get("check_term_refs", True),
            check_identity=opts.get("check_identity", True),
            limit=opts.get("limit", 0),
            progress=on_progress,
        )

        # A namespace with problems is a completed job with findings, not a
        # failed one: the check ran, and its answer is the deliverable.
        if result.status != "healthy":
            progress_callback(ProgressEvent(
                phase="warning",
                message=(
                    f"[{namespace}] integrity {result.status}: "
                    f"{result.summary.documents_with_issues} document(s) with "
                    f"issues out of {result.summary.documents_checked} checked"
                ),
            ))

        # The integrity result rides the terminal event, the same fix the
        # dry-run path already received: writing it in a separate second
        # pass raced the consumer's per-event load-modify-save — the save
        # landed inside an in-flight progress event's persist window and
        # that event's full save put the result back to null. Small jobs
        # lost it almost always (their tail events were still draining);
        # long jobs survived (queue empty by save time). result_kind
        # discriminates this payload from the restore dry-run plan that
        # shares the job.result field.
        progress_callback(ProgressEvent(
            phase="complete",
            message=(
                f"[{namespace}] validation complete — {result.status}, "
                f"{result.summary.documents_checked} document(s) checked"
            ),
            percent=100,
            details={
                "result_kind": "namespace_integrity",
                "status": result.status,
                "summary": result.summary.model_dump(),
                "issues": [
                    issue.model_dump()
                    for issue in result.issues[:VALIDATION_ISSUE_SAMPLE]
                ],
                "issues_truncated": max(
                    0, len(result.issues) - VALIDATION_ISSUE_SAMPLE
                ),
            },
        ))

    return runner


async def trigger_validation_for(restore_job: BackupJob) -> list[str]:
    """Start a validation job per namespace a restore wrote, and link them.

    One job per namespace rather than one for the archive: a multi-namespace
    restore's namespaces are verified independently, and a single combined
    result would not say which of them is unhealthy.

    Best-effort. A restore that succeeded must not be reported as failed
    because its follow-up check could not be started.
    """
    namespaces = restore_job.namespaces or (
        [restore_job.namespace] if restore_job.namespace else []
    )
    started: list[str] = []
    for namespace in namespaces:
        job_id = f"val-{uuid.uuid4().hex[:16]}"
        try:
            job = BackupJob(
                job_id=job_id,
                kind=BackupJobKind.VALIDATE,
                namespace=namespace,
                namespaces=[namespace],
                options={"triggered_by": restore_job.job_id},
                created_by=restore_job.created_by,
            )
            await job.insert()
            await start_async_job(
                job_id, make_validation_runner(job_id, namespace)
            )
            started.append(job_id)
        except Exception:
            logger.warning(
                "Could not start validation for namespace %s after restore %s",
                namespace, restore_job.job_id, exc_info=True,
            )

    if started:
        # Atomic field update: this task runs detached from the progress
        # pipeline, concurrent with the archive lifecycle hook — a full save
        # from either side erases the other's fields.
        fresh = await BackupJob.find_one(BackupJob.job_id == restore_job.job_id)
        if fresh is not None:
            await fresh.set({BackupJob.validation_job_ids: started})
    return started


def make_direct_restore_runner(
    archive_path: str | Path,
    options: dict[str, Any] | None = None,
    job_id: str | None = None,
) -> AsyncRunner:
    """Build an :data:`AsyncRunner` that writes ``archive_path`` into MongoDB.

    ``options['mode']`` picks the engine entry point: ``restore`` inserts an
    archive into an empty namespace preserving every id, ``merge`` reconciles
    it against a namespace that already holds data, and ``fresh`` re-mints
    every identity so a namespace can be restored beside the one it came from.
    They differ in preconditions and in what they do on a collision, so each
    takes its own parameter set — an option reaching the wrong mode is
    rejected at the API surface rather than dropped here.
    """
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

        if opts.get("mode") == "fresh":
            await engine.run_remap(
                Path(archive_path),
                target_namespace=opts.get("target_namespace", ""),
                namespace_map=opts.get("namespace_map"),
                skip_documents=opts.get("skip_documents", False),
                skip_files=opts.get("skip_files", False),
                batch_size=opts.get("batch_size", 500),
                dry_run=opts.get("dry_run", False),
            )
            return
        if opts.get("mode") == "merge":
            await engine.run_merge(
                Path(archive_path),
                target_namespace=opts.get("target_namespace", ""),
                on_clash=opts.get("on_clash", "skip"),
                add_missing=opts.get("add_missing", False),
                extend_terminologies=opts.get("extend_terminologies", False),
                skip_documents=opts.get("skip_documents", False),
                skip_files=opts.get("skip_files", False),
                batch_size=opts.get("batch_size", 500),
                dry_run=opts.get("dry_run", False),
            )
            return
        await engine.run_restore(
            Path(archive_path),
            target_namespace=opts.get("target_namespace", ""),
            skip_documents=opts.get("skip_documents", False),
            skip_files=opts.get("skip_files", False),
            batch_size=opts.get("batch_size", 500),
            drop_stale_reporting=opts.get("drop_stale_reporting", False),
            dry_run=opts.get("dry_run", False),
        )

    return runner
