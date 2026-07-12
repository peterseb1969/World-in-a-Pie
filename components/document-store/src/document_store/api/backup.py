"""REST endpoints for backup/restore (CASE-23 Phase 3 STEP 5).

This module is the public HTTP surface for the backup/restore subsystem. It
intentionally does **not** import ``wip_toolkit`` — that is Guardrail 1. All
toolkit interaction goes through the factory functions in
:mod:`document_store.services.backup_service`.

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
    Stream a job's retained archive. Backup jobs: their COMPLETE output
    (read permission, as before). Restore jobs: their input archive —
    valid regardless of job outcome, but it may carry several namespaces'
    data, so admin is required on every namespace the job spans.
* ``POST /backup/jobs/{job_id}/restore``
    Restore from a job's retained archive without re-uploading it. The new
    restore job gets its OWN archive copy so deleting either job never
    pulls the archive out from under the other.
* ``GET  /backup/jobs``
    List recent jobs, optionally filtered by namespace and/or status.
* ``DELETE /backup/jobs/{job_id}``
    Delete a terminal job record and its archive, wherever it lives.

Archive storage
---------------
Archives durably live in MinIO (dedicated bucket) when file storage is
enabled; the local ``$WIP_BACKUP_DIR`` is scratch/staging space. Without
MinIO there is no durable-retention promise: restore inputs are deleted at
the job's terminal state and backup outputs are swept once older than
``WIP_BACKUP_RETENTION_HOURS``. See ``services/archive_store.py``.

Multi-worker note
-----------------
The in-process :mod:`asyncio.Queue` in
:mod:`document_store.services.backup_service` is a worker-thread → event-loop
bridge whose consumer task persists each progress event onto the ``BackupJob``
MongoDB record; it is local to the uvicorn worker that started the job and is
never read by any HTTP endpoint. Both GET /jobs/{job_id} and the SSE stream
read the persisted record (the SSE generator polls it every 500ms), so every
endpoint here works from any worker — no session affinity required.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from beanie.odm.enums import SortDirection
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from wip_auth import UserIdentity, check_namespace_permission, require_api_key

from ..models.backup_job import (
    BackupJob,
    BackupJobKind,
    BackupJobSnapshot,
    BackupJobStatus,
    BackupProgressMessage,
    BackupRequest,
    RestoreFromJobRequest,
)
from ..services import backup_service
from ..services.archive_store import (
    BACKEND_MINIO,
    archive_lifecycle_hook,
    get_archive_store,
    scratch_path_for,
)

# NOTE — GUARDRAIL 1: do not add `import wip_toolkit` or `from wip_toolkit ...`
# anywhere in this file. Use the factory helpers in backup_service instead.
# Verification during review:
#   grep -rn "wip_toolkit" components/document-store/src/document_store/api/
# must return zero hits.

logger = logging.getLogger("document_store.api.backup")

router = APIRouter(prefix="/backup", tags=["Backup"])


def _archive_path_for(job_id: str) -> Path:
    """Scratch/staging path for a job's archive (see archive_store)."""
    return scratch_path_for(job_id)


async def _authorize_archive_restore(
    archive_path: Path,
    identity: UserIdentity,
    fallback_target: str,
) -> tuple[list[str], str]:
    """Manifest-driven restore authorization, shared by BOTH restore entry
    points (upload and restore-from-job) so neither can bypass it.

    In restore mode the archive manifest is authoritative for which
    namespaces get written — an archive may carry several, and admin is
    required on EVERY one of them; a check against only the request's
    anchor namespace would be a privilege hole. Returns the manifest's
    namespace prefixes and the effective target ('' for a multi-namespace
    archive: each namespace restores to itself).

    A pre-v3 archive is flat (no namespaces/ subtree), so the restore loop
    would read nothing, create the namespace, and report success — an empty
    namespace with no error. Reject it synchronously here, before any job
    or namespace exists. Only an *unreadable* manifest falls through to the
    fallback target (the engine fails on a truly broken archive later); a
    readable non-v3 manifest is a caller error.
    """
    manifest = backup_service.read_archive_manifest(archive_path)

    prefixes: list[str] = []
    if manifest is not None:
        if not manifest.format_version.startswith("3"):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Archive is format v{manifest.format_version} — this "
                    "endpoint restores v3 archives. Convert it first: "
                    "python -m wip_toolkit convert-archive <src> <dst>"
                ),
            )
        prefixes = manifest.namespace_prefixes()

    effective_target = fallback_target
    if prefixes:
        # Admin on every namespace the restore will write.
        for ns in prefixes:
            await check_namespace_permission(identity, ns, "admin")
        # Single-namespace archive → that namespace is the target. Multi →
        # each restores to itself; leave target empty so the engine loops.
        effective_target = prefixes[0] if len(prefixes) == 1 else ""

    return prefixes, effective_target


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
    identity: UserIdentity = Depends(require_api_key),
) -> BackupJobSnapshot:
    """Kick off a backup of one or more namespaces.

    The URL ``{namespace}`` is the anchor. ``all_namespaces`` backs up every
    registry namespace; ``namespaces`` adds an explicit set alongside the
    anchor. Admin permission is required on every namespace in the resolved set.
    """
    # Resolve the namespace set.
    if request.all_namespaces:
        ns_list = await backup_service.list_all_namespaces()
    elif request.namespaces:
        ns_list = list(dict.fromkeys([namespace, *request.namespaces]))
    else:
        ns_list = [namespace]

    if not ns_list:
        raise HTTPException(status_code=400, detail="No namespaces resolved to back up.")

    # Admin on every namespace in the set (the anchor was implied by the URL).
    for ns in ns_list:
        await check_namespace_permission(identity, ns, "admin")

    get_archive_store().sweep_expired_scratch()

    job_id = f"bkp-{uuid.uuid4().hex[:16]}"
    archive_path = _archive_path_for(job_id)
    options = request.model_dump()

    job = BackupJob(
        job_id=job_id,
        kind=BackupJobKind.BACKUP,
        namespace=namespace,
        namespaces=ns_list,
        archive_path=str(archive_path),
        options=options,
        created_by=identity.identity_string if hasattr(identity, "identity_string") else str(identity),
    )
    await job.insert()

    runner = backup_service.make_direct_backup_runner(
        namespaces=ns_list,
        archive_path=archive_path,
        options=options,
    )
    try:
        await backup_service.start_async_job(
            job_id, runner, on_event=archive_lifecycle_hook(job_id)
        )
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
    batch_size: int = Form(50, ge=1, le=500),
    continue_on_error: bool = Form(False),
    dry_run: bool = Form(False),
    identity: UserIdentity = Depends(require_api_key),
) -> BackupJobSnapshot:
    """Upload an archive and restore it into ``namespace``.

    The archive is streamed to disk at ``$WIP_BACKUP_DIR/{job_id}.zip`` so
    multi-GB uploads don't buffer in RAM.
    """
    effective_target = target_namespace or namespace
    await check_namespace_permission(identity, effective_target, "admin")

    if mode not in ("restore", "fresh"):
        raise HTTPException(
            status_code=400, detail=f"Invalid mode '{mode}' — must be 'restore' or 'fresh'"
        )
    if mode == "fresh":
        raise HTTPException(
            status_code=400, detail="Fresh mode is not yet implemented. Use 'restore' mode."
        )

    get_archive_store().sweep_expired_scratch()

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

    prefixes: list[str] = []
    if mode == "restore":
        try:
            prefixes, effective_target = await _authorize_archive_restore(
                archive_path, identity, fallback_target=effective_target
            )
        except HTTPException:
            archive_path.unlink(missing_ok=True)
            raise

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
        namespace=effective_target or namespace,
        # The manifest's prefixes are the namespaces this restore writes; a
        # single-namespace archive derives prefixes == [effective_target], so
        # this one expression covers both the single- and multi-namespace
        # paths. Only an unreadable manifest leaves prefixes empty — fall back
        # to the scalar namespace so the field is never silently blank.
        namespaces=prefixes or [effective_target or namespace],
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
        await backup_service.start_async_job(
            job_id, runner, on_event=archive_lifecycle_hook(job_id)
        )
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
    identity: UserIdentity = Depends(require_api_key),
) -> BackupJobSnapshot:
    job = await BackupJob.find_one(BackupJob.job_id == job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Backup job {job_id} not found")
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
    identity: UserIdentity = Depends(require_api_key),
) -> list[BackupJobSnapshot]:
    query: dict = {}
    if namespace is not None:
        query["namespace"] = namespace
    if status is not None:
        query["status"] = status
    cursor = BackupJob.find(query).sort([("created_at", SortDirection.DESCENDING)]).limit(limit)
    jobs = await cursor.to_list()
    # Filter to namespaces the caller can read (cheap for small limits).
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
    identity: UserIdentity = Depends(require_api_key),
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


@router.get(
    "/jobs/{job_id}/download",
    summary="Download a job's retained archive",
)
async def download_archive(
    job_id: str,
    identity: UserIdentity = Depends(require_api_key),
) -> StreamingResponse:
    """Stream the archive a job retains, wherever it lives.

    Backup jobs serve their output: COMPLETE status required (an incomplete
    backup has no valid archive) and read permission suffices, as before.
    Restore jobs serve their INPUT: it is fully on disk before the job even
    starts, so it is valid regardless of the job's outcome — a FAILED
    restore's input is the most useful one to re-download. But it may carry
    several namespaces' data, so admin is required on every namespace the
    job spans, mirroring what restoring the same archive would demand.
    """
    job = await BackupJob.find_one(BackupJob.job_id == job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Backup job {job_id} not found")

    if job.kind == BackupJobKind.BACKUP:
        await check_namespace_permission(identity, job.namespace, "read")
        if job.status != BackupJobStatus.COMPLETE:
            raise HTTPException(
                status_code=409,
                detail=f"Job {job_id} is {job.status}, archive not available",
            )
    else:
        for ns in job.namespaces or [job.namespace]:
            await check_namespace_permission(identity, ns, "admin")

    if not job.archive_path:
        raise HTTPException(status_code=500, detail="Job has no archive_path recorded")

    store = get_archive_store()
    if not await store.archive_exists(job):
        raise HTTPException(
            status_code=410,
            detail=(
                "Archive no longer retained. Without file storage enabled, "
                "restore inputs are deleted when the job finishes and backup "
                "archives are swept after the retention window."
            ),
        )

    size = job.archive_size
    if job.archive_backend != BACKEND_MINIO:
        with contextlib.suppress(OSError):
            size = Path(job.archive_path).stat().st_size

    headers = {
        "Content-Disposition": f'attachment; filename="{job.namespace}-{job.job_id}.zip"',
    }
    if size is not None:
        headers["Content-Length"] = str(size)

    return StreamingResponse(
        store.stream_archive(job),
        media_type="application/zip",
        headers=headers,
    )


# ---------------------------------------------------------------------------
# POST /backup/jobs/{job_id}/restore — restore from a retained archive
# ---------------------------------------------------------------------------


@router.post(
    "/jobs/{job_id}/restore",
    response_model=BackupJobSnapshot,
    status_code=202,
    summary="Restore from an existing job's retained archive",
)
async def restore_from_job(
    job_id: str,
    request: RestoreFromJobRequest,
    identity: UserIdentity = Depends(require_api_key),
) -> BackupJobSnapshot:
    """Re-run a restore (or restore a backup you already made) without
    re-uploading the archive — the bytes are already retained server-side.

    The new restore job receives its OWN archive copy (a server-side object
    copy in the bucket, or a hardlink in scratch), so deleting either job
    never removes the archive out from under the other. Authorization is
    two-layered: admin on every namespace the SOURCE job spans (reading the
    archive is download-equivalent), then the same manifest-driven
    per-namespace admin checks the upload restore runs (the archive
    manifest is authoritative for what gets written).
    """
    src = await BackupJob.find_one(BackupJob.job_id == job_id)
    if src is None:
        raise HTTPException(status_code=404, detail=f"Backup job {job_id} not found")

    for ns in src.namespaces or [src.namespace]:
        await check_namespace_permission(identity, ns, "admin")

    if src.kind == BackupJobKind.BACKUP and src.status != BackupJobStatus.COMPLETE:
        raise HTTPException(
            status_code=409,
            detail=f"Job {job_id} is {src.status} — no completed archive to restore from",
        )

    store = get_archive_store()
    if not src.archive_path or not await store.archive_exists(src):
        raise HTTPException(
            status_code=410,
            detail=(
                "Archive no longer retained. Without file storage enabled, "
                "restore inputs are deleted when the job finishes and backup "
                "archives are swept after the retention window."
            ),
        )

    get_archive_store().sweep_expired_scratch()

    new_job_id = f"rst-{uuid.uuid4().hex[:16]}"
    object_copied = src.archive_backend == BACKEND_MINIO
    scratch = await store.materialize_for_restore(src, new_job_id)

    try:
        prefixes, effective_target = await _authorize_archive_restore(
            scratch, identity, fallback_target=src.namespace
        )
    except HTTPException:
        await store.discard_materialized(new_job_id, object_copied)
        raise

    options = {
        "mode": "restore",
        "target_namespace": effective_target,
        "skip_documents": request.skip_documents,
        "skip_files": request.skip_files,
        "batch_size": request.batch_size,
        "restored_from_job": src.job_id,
    }

    job = BackupJob(
        job_id=new_job_id,
        kind=BackupJobKind.RESTORE,
        namespace=effective_target or src.namespace,
        # Same never-blank rule as the upload restore: manifest prefixes,
        # falling back to the scalar namespace on an unreadable manifest.
        namespaces=prefixes or [effective_target or src.namespace],
        archive_path=f"{new_job_id}.zip" if object_copied else str(scratch),
        archive_backend=BACKEND_MINIO if object_copied else "local",
        archive_size=scratch.stat().st_size,
        options=options,
        created_by=identity.identity_string if hasattr(identity, "identity_string") else str(identity),
    )
    await job.insert()

    runner = backup_service.make_direct_restore_runner(
        archive_path=scratch,
        options=options,
    )
    try:
        await backup_service.start_async_job(
            new_job_id, runner, on_event=archive_lifecycle_hook(new_job_id)
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return BackupJobSnapshot.from_job(job)


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
    identity: UserIdentity = Depends(require_api_key),
) -> None:
    job = await BackupJob.find_one(BackupJob.job_id == job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Backup job {job_id} not found")

    await check_namespace_permission(identity, job.namespace, "admin")

    if job.status == BackupJobStatus.RUNNING:
        raise HTTPException(
            status_code=409, detail="Cannot delete a running job; wait for it to finish"
        )

    await get_archive_store().delete_archive(job)

    await job.delete()
