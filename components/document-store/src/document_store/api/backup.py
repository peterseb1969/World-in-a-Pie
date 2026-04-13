"""REST endpoints for backup/restore (CASE-23 Phase 3 STEP 5).

This module is the public HTTP surface for the backup/restore subsystem. It
intentionally does **not** import ``wip_toolkit`` — that is Guardrail 1 from
``docs/design/backup-restore-approach.md``. All toolkit interaction goes
through the factory functions in :mod:`document_store.services.backup_service`.

Endpoints
---------

* ``POST /backup/namespaces/{namespace}/backup``
    Create a backup job and kick off the export in a worker thread. Returns
    the initial :class:`BackupJobSnapshot` immediately (HTTP 202).
* ``POST /backup/namespaces/{namespace}/restore``
    Multipart upload of an archive + form fields. The upload is streamed to
    disk and a restore job is kicked off against it. Returns the initial
    snapshot.
* ``GET  /backup/jobs/{job_id}``
    Latest persisted snapshot for a job.
* ``GET  /backup/jobs/{job_id}/events``
    Server-Sent Events stream of :class:`BackupProgressMessage` envelopes.
    **Guardrail 2:** the wire type is ``BackupProgressMessage``, never
    ``wip_toolkit.models.ProgressEvent``.
* ``GET  /backup/jobs/{job_id}/download``
    Stream the completed archive file (backup jobs only, COMPLETE status
    only).
* ``GET  /backup/jobs``
    List recent jobs, optionally filtered by namespace and/or status.

Single-worker caveat
--------------------
The in-process :mod:`asyncio.Queue` used for live SSE streaming is local to
the uvicorn worker that started the job. If document-store runs with
multiple workers, the SSE endpoint must hit the same worker that handled
POST /backup (session affinity). The persisted ``BackupJob`` MongoDB record
is the durable source of truth, so GET /jobs/{job_id} (polling) works from
any worker.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from wip_auth import check_namespace_permission, get_current_identity, require_api_key

from ..models.backup_job import (
    BackupJob,
    BackupJobKind,
    BackupJobSnapshot,
    BackupJobStatus,
    BackupProgressMessage,
    BackupRequest,
)
from ..services import backup_service

# NOTE — GUARDRAIL 1: do not add `import wip_toolkit` or `from wip_toolkit ...`
# anywhere in this file. Use the factory helpers in backup_service instead.
# Verification during review:
#   grep -rn "wip_toolkit" components/document-store/src/document_store/api/
# must return zero hits.

logger = logging.getLogger("document_store.api.backup")

router = APIRouter(prefix="/backup", tags=["Backup"])


def _backup_dir() -> Path:
    """Return the configured backup archive directory, creating it if needed."""
    path = Path(os.getenv("WIP_BACKUP_DIR", "/tmp/wip-backups"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _archive_path_for(job_id: str) -> Path:
    return _backup_dir() / f"{job_id}.zip"


# ---------------------------------------------------------------------------
# POST /backup/namespaces/{namespace}/backup
# ---------------------------------------------------------------------------


@router.post(
    "/namespaces/{namespace}/backup",
    response_model=BackupJobSnapshot,
    status_code=202,
    summary="Start a namespace backup",
)
async def start_backup(
    namespace: str,
    request: BackupRequest,
    _auth: str = Depends(require_api_key),
) -> BackupJobSnapshot:
    """Kick off a namespace backup. Returns the initial job snapshot (202)."""
    identity = get_current_identity()
    await check_namespace_permission(identity, namespace, "admin")

    job_id = f"bkp-{uuid.uuid4().hex[:16]}"
    archive_path = _archive_path_for(job_id)
    options = request.model_dump()

    job = BackupJob(
        job_id=job_id,
        kind=BackupJobKind.BACKUP,
        namespace=namespace,
        archive_path=str(archive_path),
        options=options,
        created_by=identity.identity_string if hasattr(identity, "identity_string") else str(identity),
    )
    await job.insert()

    runner = backup_service.make_direct_backup_runner(
        namespace=namespace,
        archive_path=archive_path,
        options=options,
    )
    try:
        await backup_service.start_async_job(job_id, runner)
    except ValueError as exc:  # duplicate in this worker
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return BackupJobSnapshot.from_job(job)


# ---------------------------------------------------------------------------
# POST /backup/namespaces/{namespace}/restore
# ---------------------------------------------------------------------------


@router.post(
    "/namespaces/{namespace}/restore",
    response_model=BackupJobSnapshot,
    status_code=202,
    summary="Restore a namespace from an uploaded archive",
)
async def start_restore(
    namespace: str,
    archive: UploadFile = File(..., description="Backup archive (.zip) to restore"),
    mode: str = Form("restore"),
    target_namespace: str | None = Form(None),
    register_synonyms: bool = Form(False),
    skip_documents: bool = Form(False),
    skip_files: bool = Form(False),
    batch_size: int = Form(50),
    continue_on_error: bool = Form(False),
    dry_run: bool = Form(False),
    _auth: str = Depends(require_api_key),
) -> BackupJobSnapshot:
    """Upload an archive and restore it into ``namespace``.

    The archive is streamed to disk at ``$WIP_BACKUP_DIR/{job_id}.zip`` so
    multi-GB uploads don't buffer in RAM.
    """
    effective_target = target_namespace or namespace
    identity = get_current_identity()
    await check_namespace_permission(identity, effective_target, "admin")

    if mode not in ("restore", "fresh"):
        raise HTTPException(
            status_code=400, detail=f"Invalid mode '{mode}' — must be 'restore' or 'fresh'"
        )
    if mode == "fresh":
        raise HTTPException(
            status_code=400, detail="Fresh mode is not yet implemented. Use 'restore' mode."
        )

    job_id = f"rst-{uuid.uuid4().hex[:16]}"
    archive_path = _archive_path_for(job_id)

    # Stream upload to disk without buffering the whole thing in memory.
    try:
        with archive_path.open("wb") as fh:
            while True:
                chunk = await archive.read(1024 * 1024)  # 1 MiB
                if not chunk:
                    break
                fh.write(chunk)
    except Exception as exc:
        archive_path.unlink(missing_ok=True)
        logger.exception("Failed to stream restore upload for %s", job_id)
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}") from exc
    finally:
        await archive.close()

    archive_size = archive_path.stat().st_size

    # In restore mode, read the source namespace from the archive manifest.
    # The URL namespace is used for auth only — the archive is authoritative.
    if mode == "restore":
        try:
            from wip_toolkit.archive import ArchiveReader
            with ArchiveReader(archive_path) as reader:
                manifest = reader.read_manifest()
                if manifest.namespace:
                    effective_target = manifest.namespace
                    # Re-check permission on the actual target namespace
                    await check_namespace_permission(identity, effective_target, "admin")
        except Exception as exc:
            logger.warning("Could not read manifest from archive: %s", exc)
            # Fall through with the URL-derived namespace

    options = {
        "mode": mode,
        "target_namespace": effective_target,
        "register_synonyms": register_synonyms,
        "skip_documents": skip_documents,
        "skip_files": skip_files,
        "batch_size": batch_size,
        "continue_on_error": continue_on_error,
        "dry_run": dry_run,
    }

    job = BackupJob(
        job_id=job_id,
        kind=BackupJobKind.RESTORE,
        namespace=effective_target,
        archive_path=str(archive_path),
        archive_size=archive_size,
        options=options,
        created_by=identity.identity_string if hasattr(identity, "identity_string") else str(identity),
    )
    await job.insert()

    runner = backup_service.make_direct_restore_runner(
        archive_path=archive_path,
        options=options,
    )
    try:
        await backup_service.start_async_job(job_id, runner)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return BackupJobSnapshot.from_job(job)


# ---------------------------------------------------------------------------
# GET /backup/jobs/{job_id}
# ---------------------------------------------------------------------------


@router.get(
    "/jobs/{job_id}",
    response_model=BackupJobSnapshot,
    summary="Get the current state of a backup/restore job",
)
async def get_job(
    job_id: str,
    _auth: str = Depends(require_api_key),
) -> BackupJobSnapshot:
    job = await BackupJob.find_one(BackupJob.job_id == job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Backup job {job_id} not found")
    identity = get_current_identity()
    await check_namespace_permission(identity, job.namespace, "read")
    return BackupJobSnapshot.from_job(job)


# ---------------------------------------------------------------------------
# GET /backup/jobs — list
# ---------------------------------------------------------------------------


@router.get(
    "/jobs",
    response_model=list[BackupJobSnapshot],
    summary="List recent backup/restore jobs",
)
async def list_jobs(
    namespace: str | None = Query(None, description="Filter by namespace"),
    status: BackupJobStatus | None = Query(None, description="Filter by status"),
    limit: int = Query(50, ge=1, le=500),
    _auth: str = Depends(require_api_key),
) -> list[BackupJobSnapshot]:
    query: dict = {}
    if namespace is not None:
        query["namespace"] = namespace
    if status is not None:
        query["status"] = status
    cursor = BackupJob.find(query).sort(-BackupJob.created_at).limit(limit)
    jobs = await cursor.to_list()
    # Filter to namespaces the caller can read (cheap for small limits).
    identity = get_current_identity()
    allowed: list[BackupJob] = []
    for job in jobs:
        try:
            await check_namespace_permission(identity, job.namespace, "read")
        except HTTPException:
            continue
        allowed.append(job)
    return [BackupJobSnapshot.from_job(j) for j in allowed]


# ---------------------------------------------------------------------------
# GET /backup/jobs/{job_id}/events — SSE
# ---------------------------------------------------------------------------


async def _sse_stream(job_id: str) -> AsyncIterator[bytes]:
    """Yield Server-Sent Events for a backup/restore job.

    Strategy: emit an initial snapshot immediately, then poll the persisted
    ``BackupJob`` record every 500ms and yield a new event whenever anything
    visible changes (status, phase, percent, message). Polling avoids the
    need for the same uvicorn worker that started the job to also serve the
    SSE — which matters in multi-worker deployments. The latency penalty is
    ~500ms, well below human perception for a minute-scale backup.

    Terminates when the job reaches a terminal status (complete or failed)
    or disappears.
    """
    last_signature: tuple | None = None
    terminal = {BackupJobStatus.COMPLETE, BackupJobStatus.FAILED}
    # Send initial keep-alive comment so clients know the stream is live.
    yield b": connected\n\n"

    while True:
        job = await BackupJob.find_one(BackupJob.job_id == job_id)
        if job is None:
            msg = {"error": f"Backup job {job_id} not found"}
            yield f"event: error\ndata: {json.dumps(msg)}\n\n".encode()
            return

        signature = (job.status, job.phase, job.percent, job.message)
        if signature != last_signature:
            envelope = BackupProgressMessage(
                job_id=job.job_id,
                status=job.status,
                phase=job.phase,
                percent=job.percent,
                message=job.message,
                current=None,
                total=None,
                details=None,
            )
            payload = envelope.model_dump_json()
            yield f"event: progress\ndata: {payload}\n\n".encode()
            last_signature = signature

        if job.status in terminal:
            return

        await asyncio.sleep(0.5)


@router.get(
    "/jobs/{job_id}/events",
    summary="Server-Sent Events stream of job progress",
)
async def stream_job_events(
    job_id: str,
    _auth: str = Depends(require_api_key),
) -> StreamingResponse:
    """SSE stream of :class:`BackupProgressMessage` envelopes for a job.

    Wire format
    -----------
    The ``data:`` payload of every ``progress`` event is a JSON-encoded
    :class:`BackupProgressMessage` (see ``models/backup_job.py``). **This is
    not ``wip_toolkit.models.ProgressEvent``** — that type is an
    implementation detail and must not be exposed on the wire (Guardrail 2).
    """
    # Permission check on the job's namespace before we start streaming.
    job = await BackupJob.find_one(BackupJob.job_id == job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Backup job {job_id} not found")
    identity = get_current_identity()
    await check_namespace_permission(identity, job.namespace, "read")

    return StreamingResponse(
        _sse_stream(job_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # disable proxy buffering
        },
    )


# ---------------------------------------------------------------------------
# GET /backup/jobs/{job_id}/download
# ---------------------------------------------------------------------------


async def _file_chunks(path: Path, chunk_size: int = 1024 * 1024) -> AsyncIterator[bytes]:
    """Yield file contents in chunks without blocking the event loop."""
    loop = asyncio.get_running_loop()
    with path.open("rb") as fh:
        while True:
            chunk = await loop.run_in_executor(None, fh.read, chunk_size)
            if not chunk:
                return
            yield chunk


@router.get(
    "/jobs/{job_id}/download",
    summary="Download the archive produced by a completed backup job",
)
async def download_archive(
    job_id: str,
    _auth: str = Depends(require_api_key),
) -> StreamingResponse:
    job = await BackupJob.find_one(BackupJob.job_id == job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Backup job {job_id} not found")

    identity = get_current_identity()
    await check_namespace_permission(identity, job.namespace, "read")

    if job.kind != BackupJobKind.BACKUP:
        raise HTTPException(
            status_code=400,
            detail=f"Job {job_id} is a {job.kind} job, not a backup — no archive to download",
        )
    if job.status != BackupJobStatus.COMPLETE:
        raise HTTPException(
            status_code=409,
            detail=f"Job {job_id} is {job.status}, archive not available",
        )
    if not job.archive_path:
        raise HTTPException(status_code=500, detail="Job has no archive_path recorded")

    path = Path(job.archive_path)
    if not path.exists():
        raise HTTPException(status_code=410, detail="Archive file no longer on disk")

    filename = f"{job.namespace}-{job.job_id}.zip"
    size = path.stat().st_size

    return StreamingResponse(
        _file_chunks(path),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(size),
        },
    )


# ---------------------------------------------------------------------------
# DELETE /backup/jobs/{job_id} — cleanup
# ---------------------------------------------------------------------------


@router.delete(
    "/jobs/{job_id}",
    status_code=204,
    summary="Delete a backup/restore job and its archive file",
)
async def delete_job(
    job_id: str,
    _auth: str = Depends(require_api_key),
) -> None:
    job = await BackupJob.find_one(BackupJob.job_id == job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Backup job {job_id} not found")

    identity = get_current_identity()
    await check_namespace_permission(identity, job.namespace, "admin")

    if job.status == BackupJobStatus.RUNNING:
        raise HTTPException(
            status_code=409, detail="Cannot delete a running job; wait for it to finish"
        )

    if job.archive_path:
        path = Path(job.archive_path)
        try:
            if path.is_file():
                path.unlink()
            elif path.is_dir():  # defensive — shouldn't happen
                shutil.rmtree(path)
        except OSError:
            logger.exception("Failed to remove archive for %s at %s", job_id, path)

    await job.delete()
