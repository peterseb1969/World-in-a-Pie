"""Backup/restore job orchestration (CASE-23 Phase 3 STEP 3).

Bridges the sync wip-toolkit orchestrators (`run_export` / `run_import`) onto
the async FastAPI event loop. The toolkit runs in a worker thread; its
`progress_callback` hops events back to the loop via
``loop.call_soon_threadsafe`` into a per-job ``asyncio.Queue``. A consumer
coroutine drains the queue, updates the persistent ``BackupJob`` document in
MongoDB, and forwards each event to downstream subscribers (the SSE endpoint
in STEP 5).

Design notes
------------
* **Per-job state is process-local.** The ``_job_queues`` dict lives in this
  module and only the worker that started the job can stream live events
  from its queue. The durable state is the ``BackupJob`` record in MongoDB,
  so status endpoints in *any* worker still work via polling.
* **Callback faults do not break the job.** Every callback invocation from
  the toolkit is already wrapped in ``wip_toolkit._progress.emit`` which
  swallows exceptions. We additionally guard the thread→loop hop with a
  try/except so a dead loop doesn't take down the worker thread.
* **Executor is injectable** for tests — the module exposes a `set_executor`
  hook so tests can substitute a deterministic executor.
* **The toolkit runner is a parameter**, not a hardcoded import, so tests can
  pass a fake runner that emits scripted events instead of calling httpx.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Awaitable, Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wip_toolkit.client import WIPClient
from wip_toolkit.config import WIPConfig
from wip_toolkit.export.exporter import run_export
from wip_toolkit.import_.importer import run_import
from wip_toolkit.models import ProgressEvent

from ..models.backup_job import BackupJob, BackupJobStatus

logger = logging.getLogger("document_store.backup_service")


# Sentinel placed on the queue after the worker thread finishes (success or
# failure) so consumers know to stop awaiting. Using a module-level singleton
# keeps type narrowing simple (``event is _SENTINEL``).
class _Sentinel:
    """End-of-stream marker."""


_SENTINEL: _Sentinel = _Sentinel()

# Type of the function that actually runs the toolkit in a worker thread.
# Signature: (progress_callback) -> None. Takes the callback so it can emit
# phase events; raises on failure.
ToolkitRunner = Callable[[Callable[[ProgressEvent], None]], Any]

# Per-job in-process state (process-local by design — see module docstring).
_job_queues: dict[str, asyncio.Queue[ProgressEvent | _Sentinel]] = {}
_job_tasks: dict[str, asyncio.Task[None]] = {}

# Lazily-constructed module-level executor. Tests may override via
# ``set_executor``. Default: 4 threads, sufficient for v1.0 (backup jobs are
# I/O heavy but not concurrent at the per-caller level).
_executor: Executor | None = None


def _get_executor() -> Executor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="wip-backup"
        )
    return _executor


def set_executor(executor: Executor | None) -> None:
    """Override the executor (tests) or reset to the default (None)."""
    global _executor
    _executor = executor


def _percent_for_status(status: BackupJobStatus, event: ProgressEvent) -> float | None:
    """Pick the percent to persist.

    Preserve the last-known percent on error events (which carry None) so the
    UI doesn't snap back to 0% on failure.
    """
    if event.phase == "error":
        return None
    return event.percent


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

    job.phase = event.phase
    job.message = event.message
    if event.percent is not None:
        job.percent = event.percent

    if event.phase == "complete":
        job.status = BackupJobStatus.COMPLETE
        job.percent = 100.0
        job.completed_at = datetime.now(UTC)
        # Populate archive_size from disk if the archive file exists.
        # This is the first opportunity after run_export() has finalized
        # the ZIP; the API layer set archive_path at job creation but
        # cannot know the size until the worker thread writes the file.
        if job.archive_path:
            with contextlib.suppress(OSError):
                job.archive_size = Path(job.archive_path).stat().st_size
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


async def start_job(
    job_id: str,
    runner: ToolkitRunner,
    *,
    on_event: Callable[[ProgressEvent], Awaitable[None]] | None = None,
) -> asyncio.Task[None]:
    """Kick off a backup/restore job.

    Args:
        job_id: The BackupJob.job_id of a previously persisted record.
        runner: A callable that runs the toolkit synchronously. It receives
            a progress_callback and is expected to call it at phase
            boundaries. Exceptions bubble out and become FAILED status.
        on_event: Optional async hook called (on the loop thread) for every
            event as it is persisted — used by the SSE endpoint to forward
            events to subscribers.

    Returns:
        The asyncio.Task that consumes the queue. The task completes when
        the worker thread finishes and the sentinel has been drained.

    Raises:
        ValueError: If a job with this id is already running in this process.
    """
    if job_id in _job_queues:
        raise ValueError(f"Job {job_id} is already running in this worker")

    queue: asyncio.Queue[ProgressEvent | _Sentinel] = asyncio.Queue()
    _job_queues[job_id] = queue

    loop = asyncio.get_running_loop()

    def thread_callback(event: ProgressEvent) -> None:
        # Called from the worker thread by the toolkit's _emit() helper.
        try:
            loop.call_soon_threadsafe(queue.put_nowait, event)
        except RuntimeError:  # loop already closed
            logger.debug("Loop closed while delivering %s for %s", event.phase, job_id)

    def thread_target() -> None:
        try:
            runner(thread_callback)
        except Exception as exc:
            logger.exception("Toolkit run failed for job %s", job_id)
            err_event = ProgressEvent(
                phase="error",
                message=str(exc) or type(exc).__name__,
                details={"type": type(exc).__name__},
            )
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(queue.put_nowait, err_event)
        finally:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(queue.put_nowait, _SENTINEL)

    _get_executor().submit(thread_target)

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
            # Safety net: if the worker crashed before emitting a terminal
            # event, mark the job failed so it doesn't sit in RUNNING forever.
            job = await BackupJob.find_one(BackupJob.job_id == job_id)
            if job is not None and job.status == BackupJobStatus.RUNNING:
                await _mark_failed(job_id, "worker terminated without terminal event")
            _job_queues.pop(job_id, None)
            _job_tasks.pop(job_id, None)

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
# Loopback toolkit client + runner factories (CASE-23 Phase 3 STEP 4)
#
# Guardrail 1 (see docs/design/backup-restore-approach.md): this module is the
# single import chokepoint for ``wip_toolkit``. ``api/backup.py`` (STEP 5) must
# never ``import wip_toolkit`` directly — it calls these factories instead.
# ---------------------------------------------------------------------------


def _loopback_service_urls() -> dict[str, str] | None:
    """Return per-service base URLs for container-mode loopback, or None.

    Document-store can run in two shapes:

    * **Host / bare metal**: every WIP service is reachable at
      ``http://localhost:{port}`` for the default SERVICE_PORTS. The default
      :class:`WIPConfig` (``host="localhost"``) works as-is, so this function
      returns ``None`` and ``_loopback_config()`` skips the override.
    * **Container (podman compose)**: each service has its own hostname in
      the network (``wip-registry``, ``wip-def-store``, …). A single "host"
      is not enough; we need per-service URL overrides.

    Container mode is detected by the presence of ``REGISTRY_URL`` in the
    environment — document-store's compose file sets it. The other service
    URLs are derived from env vars when available, falling back to the
    conventional in-network hostnames. This keeps unit tests (which run in
    a venv with no container env) on the localhost path.
    """
    registry_url = os.getenv("REGISTRY_URL")
    if not registry_url:
        return None
    return {
        "registry": registry_url,
        "def-store": os.getenv("DEF_STORE_URL", "http://wip-def-store:8002"),
        "template-store": os.getenv(
            "TEMPLATE_STORE_URL", "http://wip-template-store:8003"
        ),
        "document-store": os.getenv(
            "DOCUMENT_STORE_URL", "http://wip-document-store:8004"
        ),
        "reporting-sync": os.getenv(
            "REPORTING_SYNC_URL", "http://wip-reporting-sync:8005"
        ),
        "ingest-gateway": os.getenv(
            "INGEST_GATEWAY_URL", "http://wip-ingest-gateway:8006"
        ),
    }


def _loopback_config(api_key: str | None = None) -> WIPConfig:
    """Build a :class:`WIPConfig` that points the toolkit at local services.

    Document-store runs inside the same network as Registry / Def-Store /
    Template-Store. On a host deployment they share ``localhost``; inside a
    podman compose network each has its own hostname. :func:`_loopback_service_urls`
    returns a per-service URL override for container mode, or None for host mode.

    The API key defaults to the ambient ``WIP_AUTH_LEGACY_API_KEY`` (the same
    env var the rest of document-store uses for its outbound service calls).

    Args:
        api_key: Override the API key. Defaults to the env var.

    Returns:
        A fully-resolved :class:`WIPConfig` ready to hand to :class:`WIPClient`.

    Raises:
        RuntimeError: If no API key is provided and the env var is unset.
    """
    resolved = api_key or os.getenv("WIP_AUTH_LEGACY_API_KEY")
    if not resolved:
        raise RuntimeError(
            "WIP_AUTH_LEGACY_API_KEY is not set; cannot build loopback WIPConfig"
        )
    return WIPConfig(
        host="localhost",
        proxy=False,
        api_key=resolved,
        verify_ssl=False,  # http loopback — TLS not in the path
        verbose=False,
        service_urls=_loopback_service_urls(),
        # Backups run inherently slow queries (bulk listing, closure
        # computation over large namespaces). 10 minutes per HTTP call
        # is generous but still a real backstop against hangs.
        request_timeout_seconds=600.0,
    )


def make_backup_runner(
    namespace: str,
    archive_path: str | Path,
    options: dict[str, Any] | None = None,
    *,
    api_key: str | None = None,
) -> ToolkitRunner:
    """Build a :data:`ToolkitRunner` that exports ``namespace`` to ``archive_path``.

    The returned callable runs :func:`wip_toolkit.export.exporter.run_export`
    synchronously inside the worker thread supplied by :func:`start_job`. All
    supported :func:`run_export` keyword options pass through ``options``
    (e.g. ``include_files``, ``latest_only``, ``skip_documents``).

    Guardrail 1: this factory exists so ``api/backup.py`` can construct a
    runner without importing the toolkit itself.
    """
    opts = dict(options or {})
    config = _loopback_config(api_key)

    def runner(progress_callback: Callable[[ProgressEvent], None]) -> Any:
        with WIPClient(config) as client:
            return run_export(
                client,
                namespace,
                archive_path,
                progress_callback=progress_callback,
                non_interactive=True,
                **opts,
            )

    return runner


def make_restore_runner(
    archive_path: str | Path,
    options: dict[str, Any] | None = None,
    *,
    api_key: str | None = None,
) -> ToolkitRunner:
    """Build a :data:`ToolkitRunner` that imports ``archive_path``.

    The returned callable runs :func:`wip_toolkit.import_.importer.run_import`
    synchronously inside the worker thread supplied by :func:`start_job`. All
    supported :func:`run_import` keyword options pass through ``options``
    (e.g. ``mode``, ``target_namespace``, ``register_synonyms``, ``dry_run``).

    Guardrail 1: this factory exists so ``api/backup.py`` can construct a
    runner without importing the toolkit itself.
    """
    opts = dict(options or {})
    config = _loopback_config(api_key)

    def runner(progress_callback: Callable[[ProgressEvent], None]) -> Any:
        with WIPClient(config) as client:
            return run_import(
                client,
                archive_path,
                progress_callback=progress_callback,
                non_interactive=True,
                **opts,
            )

    return runner
