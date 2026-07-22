"""REST endpoints for backup/restore (CASE-23 Phase 3 STEP 5).

This module is the public HTTP surface for the backup/restore subsystem. It
intentionally does **not** import ``wip_archive`` (the archive-format /
remap library) — that is Guardrail 1. All archive interaction goes through
the factory functions in :mod:`document_store.services.backup_service`.

Endpoints
---------

* ``POST /backup/namespaces/{namespace}/backup``
    Create a backup job and kick off the export in a worker thread. Returns
    the initial :class:`BackupJobSnapshot` immediately (HTTP 202).
* ``POST /backup/namespaces/{namespace}/restore``
    Multipart upload of an archive + form fields. The upload is streamed to
    disk and a restore job is kicked off against it. Returns the initial
    snapshot. ``mode`` selects the semantics: ``restore`` requires an empty
    target, ``merge`` reconciles the archive into a namespace that already
    holds data: definitions must be compatible (see ``add_missing`` /
    ``extend_terminologies``), then documents merge under ``on_clash``.
* ``POST /backup/namespaces/{namespace}/validate``
    Verify a namespace's referential and identity integrity as a job. Started
    automatically after every restore, and available on demand.
* ``GET  /backup/jobs/{job_id}``
    Latest persisted snapshot for a job.
* ``GET  /backup/jobs/{job_id}/events``
    Server-Sent Events stream of :class:`BackupProgressMessage` envelopes.
    **Guardrail 2:** the wire type is ``BackupProgressMessage``, never
    ``wip_archive.models.ProgressEvent``.
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

# NOTE — GUARDRAIL 1: do not add `import wip_archive` or `from wip_archive ...`
# anywhere in this file. Use the factory helpers in backup_service instead.
# Verification during review:
#   grep -rn "wip_archive" components/document-store/src/document_store/api/
# must return zero hits.

logger = logging.getLogger("document_store.api.backup")

router = APIRouter(prefix="/backup", tags=["Backup"])


def _archive_path_for(job_id: str) -> Path:
    """Scratch/staging path for a job's archive (see archive_store)."""
    return scratch_path_for(job_id)


def _validate_merge_options(
    mode: str,
    *,
    on_clash: str,
    add_missing: bool,
    extend_terminologies: bool,
    drop_stale_reporting: bool,
) -> None:
    """Reject options the chosen mode cannot honour.

    Shared by both restore entry points. A merge option silently ignored on a
    plain restore would misrepresent what ran — the caller asked for clash
    handling and got an empty-target insert — so a non-default outside merge
    mode is an error rather than a no-op. ``drop_stale_reporting`` is
    meaningless for a merge: a live namespace's reporting schema is expected
    to hold tables, and dropping it would delete the reporting data the merge
    is adding to.
    """
    if mode != "merge":
        for name, value, default in (
            ("on_clash", on_clash, "skip"),
            ("add_missing", add_missing, False),
            ("extend_terminologies", extend_terminologies, False),
        ):
            if value != default:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"'{name}' applies to mode='merge' only. Both other "
                        "modes write into an empty namespace, so there is "
                        "nothing for them to clash with."
                    ),
                )
        return

    if on_clash not in ("skip", "overwrite", "newer"):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid on_clash '{on_clash}' — must be 'skip', 'overwrite' "
                "or 'newer'"
            ),
        )
    if drop_stale_reporting:
        raise HTTPException(
            status_code=400,
            detail=(
                "'drop_stale_reporting' does not apply to a merge — the target "
                "namespace is live, so its reporting schema is expected to hold "
                "tables, and dropping it would discard the data being merged into."
            ),
        )


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
    # Parameters of the retired toolkit export path. The direct backup engine
    # always includes term relations, carries synonyms inside registry
    # entries, has no template filter, no dry-run walk, and no latest-only
    # version filter. Silently ignoring any of these would make the archive
    # contents differ from what the caller asked for (template_prefixes and
    # latest_only especially: the caller expects a filtered export and gets
    # everything — latest_only even stamped the manifest as latest-only while
    # the archive carried every version), so reject loudly.
    _dead_backup_fields = (
        ("skip_closure", request.skip_closure),
        ("skip_synonyms", request.skip_synonyms),
        ("template_prefixes", request.template_prefixes is not None),
        ("dry_run", request.dry_run),
        ("latest_only", request.latest_only),
    )
    for _dead_name, _dead_set in _dead_backup_fields:
        if _dead_set:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{_dead_name}' is not supported by the backup engine — "
                    "it belonged to the retired toolkit export path and has "
                    "no effect. Remove it from the request."
                ),
            )

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
    mode: str = Form(
        "restore",
        description=(
            "'restore' requires an empty target and inserts everything, "
            "preserving every id. 'merge' takes the archive as a delta "
            "against an existing, possibly non-empty namespace, preserving "
            "ids and resolving what the target already holds by the on_clash "
            "policy. 'fresh' keeps nothing: every entity is registered anew "
            "with a Registry-minted id and every reference is rewritten, "
            "which is what lets a namespace be restored beside the one it "
            "came from."
        ),
    ),
    target_namespace: str | None = Form(
        None,
        description=(
            "Where to write. A restore and a merge write each archived "
            "namespace to itself unless merging into a different one; a "
            "'fresh' restore requires this, since it is placing new "
            "identities somewhere."
        ),
    ),
    namespace_map: str | None = Form(
        None,
        description=(
            "Fresh only — JSON object mapping EVERY archived namespace to "
            "its target, e.g. {\"ns-a\": \"copy-a\", \"ns-b\": \"copy-a\"}. "
            "Required for multi-namespace archives (there is no implicit "
            "default: an unmapped namespace restored to its old name would "
            "collide with the live original). Several sources may share one "
            "target; Registry-key collisions between them refuse at plan "
            "time. A target may equal its source name only when that "
            "namespace is empty or absent."
        ),
    ),
    on_clash: str = Form(
        "skip",
        description=(
            "Merge only — what to do when the target already holds a "
            "document's identity. 'skip' (default) keeps the target's "
            "version; 'overwrite' appends the archive's latest version on "
            "top of the target's head, preserving both histories; 'newer' "
            "does the same but only where the archive's copy was updated "
            "more recently, keeping the target's on a tie."
        ),
    ),
    add_missing: bool = Form(
        False,
        description=(
            "Merge only — insert terminologies and templates the target does "
            "not have. Without it, a definition the target lacks refuses the "
            "merge: changing a live namespace's definitions is an active "
            "decision, never a side effect of restoring data into it."
        ),
    ),
    extend_terminologies: bool = Form(
        False,
        description=(
            "Merge only — add terms the target's terminology is missing. "
            "Separate from add_missing on purpose: allowing new vocabulary "
            "entries is not the same decision as allowing new schemas."
        ),
    ),
    register_synonyms: bool = Form(False),
    skip_documents: bool = Form(False),
    skip_files: bool = Form(False),
    batch_size: int = Form(500, ge=1, le=500),
    continue_on_error: bool = Form(False),
    dry_run: bool = Form(
        False,
        description=(
            "Run every precondition (archive format, empty targets, "
            "reporting schema) and report what would be restored, without "
            "writing anything."
        ),
    ),
    drop_stale_reporting: bool = Form(
        False,
        description=(
            "When the target namespace's reporting schema already holds "
            "tables (stale data that would shadow the restore), drop it "
            "before restoring. Without this flag such a restore refuses."
        ),
    ),
    identity: UserIdentity = Depends(require_api_key),
) -> BackupJobSnapshot:
    """Upload an archive and restore it into ``namespace``.

    The archive is streamed to disk at ``$WIP_BACKUP_DIR/{job_id}.zip`` so
    multi-GB uploads don't buffer in RAM.
    """
    effective_target = target_namespace or namespace
    await check_namespace_permission(identity, effective_target, "admin")

    if mode not in ("restore", "merge", "fresh"):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid mode '{mode}' — must be 'restore', 'merge' or 'fresh'"
            ),
        )

    parsed_namespace_map: dict[str, str] | None = None
    if namespace_map is not None:
        if mode != "fresh":
            raise HTTPException(
                status_code=400,
                detail="namespace_map applies only to mode='fresh'",
            )
        try:
            parsed_namespace_map = json.loads(namespace_map)
        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=400,
                detail=f"namespace_map is not valid JSON: {e}",
            ) from e
        if not isinstance(parsed_namespace_map, dict) or not all(
            isinstance(k, str) and isinstance(v, str) and v
            for k, v in parsed_namespace_map.items()
        ) or not parsed_namespace_map:
            raise HTTPException(
                status_code=400,
                detail=(
                    "namespace_map must be a non-empty JSON object of "
                    "{source: target} strings"
                ),
            )
        # New identities land in every mapped target — each needs admin.
        for target in set(parsed_namespace_map.values()):
            await check_namespace_permission(identity, target, "admin")

    if mode == "fresh" and not target_namespace and not parsed_namespace_map:
        raise HTTPException(
            status_code=400,
            detail=(
                "mode='fresh' mints new identities, so it must be told where "
                "to put them: pass target_namespace (single-namespace "
                "archive) or namespace_map."
            ),
        )

    _validate_merge_options(
        mode,
        on_clash=on_clash,
        add_missing=add_missing,
        extend_terminologies=extend_terminologies,
        drop_stale_reporting=drop_stale_reporting,
    )
    # Parameters of the retired toolkit import path. The direct restore engine
    # has no per-item error tolerance and no synonym registration; silently
    # ignoring a request for either would misrepresent what the restore did,
    # so a request that sets them is rejected outright.
    for _dead_name, _dead_value in (
        ("register_synonyms", register_synonyms),
        ("continue_on_error", continue_on_error),
    ):
        if _dead_value:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{_dead_name}' is not supported by the restore engine — "
                    "it belonged to the retired toolkit import path and has "
                    "no effect. Remove it from the request."
                ),
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
    if mode == "fresh":
        # A fresh restore reads the archive but writes only to the target, so
        # admin there is the permission that matters — requiring it on the
        # source namespaces would demand rights over an instance the caller
        # may have nothing to do with.
        await check_namespace_permission(identity, effective_target, "admin")
    elif mode in ("restore", "merge"):
        try:
            prefixes, effective_target = await _authorize_archive_restore(
                archive_path, identity, fallback_target=effective_target
            )
            # A merge honors the caller's redirect. _authorize_archive_restore
            # resolves the target from the MANIFEST (correct for restore,
            # where every namespace restores to itself) — for merge that
            # silently replaced the caller's target with the archive's source,
            # so "merge into scratch-ns" executed against the live source
            # namespace instead, and the engine's whole redirect path
            # (namespace rewrite, composite-key rehash, ids-must-be-free) was
            # unreachable from the API.
            if mode == "merge" and target_namespace:
                if len(prefixes) > 1:
                    # The engine would refuse this too, but only after a job
                    # exists; refusing here turns a silent self-merge of live
                    # namespaces into an immediate, visible error.
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "target_namespace override is not supported for a "
                            "multi-namespace archive in merge mode — merge "
                            "each namespace to itself (omit target_namespace) "
                            "or split the archive."
                        ),
                    )
                # The archive prefixes were admin-checked above, but a
                # redirected merge WRITES to the caller's target — admin
                # there is the permission that gates the operation.
                await check_namespace_permission(
                    identity, target_namespace, "admin"
                )
                effective_target = target_namespace
        except HTTPException:
            archive_path.unlink(missing_ok=True)
            raise

    options = {
        "mode": mode,
        "target_namespace": effective_target,
        "namespace_map": parsed_namespace_map,
        "on_clash": on_clash,
        "add_missing": add_missing,
        "extend_terminologies": extend_terminologies,
        "register_synonyms": register_synonyms,
        "skip_documents": skip_documents,
        "skip_files": skip_files,
        "batch_size": batch_size,
        "continue_on_error": continue_on_error,
        "dry_run": dry_run,
        "drop_stale_reporting": drop_stale_reporting,
    }

    # A fresh restore WRITES to the resolved targets, not to the archive's
    # namespaces. The job record must say so, because the post-restore
    # automation (batch sync, validation) derives its scope from the job: a
    # map-only fresh restore that recorded the URL path param here synced and
    # "validated healthy" the SOURCE namespace while the target went
    # unchecked — a green verdict about the wrong namespace (CASE-745).
    # restore/merge keep the archive-prefixes semantics: they write to the
    # archived namespaces.
    if mode == "fresh":
        write_targets = (
            list(dict.fromkeys(parsed_namespace_map.values()))
            if parsed_namespace_map
            else [effective_target]
        )
        job_namespace = write_targets[0]
    elif mode == "merge" and target_namespace:
        # A redirected merge writes to the caller's target, not the archive's
        # namespace — same reasoning as fresh above: sync and validation
        # derive their scope from the job record.
        write_targets = [effective_target]
        job_namespace = effective_target
    else:
        # The manifest's prefixes are the namespaces this restore writes; a
        # single-namespace archive derives prefixes == [effective_target], so
        # this one expression covers both the single- and multi-namespace
        # paths. Only an unreadable manifest leaves prefixes empty — fall back
        # to the scalar namespace so the field is never silently blank. The
        # scalar `namespace` stays the URL auth anchor here — each archived
        # namespace restores into itself, so no single one of them IS the job.
        write_targets = prefixes or [effective_target or namespace]
        job_namespace = effective_target or namespace

    job = BackupJob(
        job_id=job_id,
        kind=BackupJobKind.RESTORE,
        namespace=job_namespace,
        namespaces=write_targets,
        archive_path=str(archive_path),
        archive_size=archive_size,
        options=options,
        created_by=identity.identity_string if hasattr(identity, "identity_string") else str(identity),
    )
    await job.insert()

    runner = backup_service.make_direct_restore_runner(
        archive_path=archive_path,
        options=options,
        job_id=job_id,
    )
    try:
        await backup_service.start_async_job(
            job_id, runner, on_event=archive_lifecycle_hook(job_id)
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return BackupJobSnapshot.from_job(job)


# ---------------------------------------------------------------------------
# POST /backup/namespaces/{namespace}/validate
# ---------------------------------------------------------------------------


@router.post(
    "/namespaces/{namespace}/validate",
    response_model=BackupJobSnapshot,
    status_code=202,
    summary="Verify a namespace's referential and identity integrity",
)
async def start_validation(
    namespace: str,
    check_term_refs: bool = Query(
        True,
        description=(
            "Check term references. One cached lookup per distinct term, so "
            "the cost scales with vocabulary size rather than document count."
        ),
    ),
    check_identity: bool = Query(
        True,
        description=(
            "Recompute each document's identity hash from its own data and "
            "compare it to the stored one."
        ),
    ),
    limit: int = Query(
        0, ge=0, description="Stop after this many documents (0 = all)"
    ),
    identity: UserIdentity = Depends(require_api_key),
) -> BackupJobSnapshot:
    """Check that a namespace's data is internally consistent.

    Every reference resolves — template, term, document, file — and every
    document's stored identity hash still matches its own content. This is the
    referential twin of reporting-sync's parity check: that one compares
    MongoDB against PostgreSQL, this one compares MongoDB against itself.

    It matters most after a restore, which writes with `insert_many` and
    validates nothing (deliberately — per-record validation while writing
    would undo the bulk write path that makes restore fast). A restore starts
    one of these per restored namespace automatically and records the job ids
    on its own record; this endpoint is the same check on demand.

    Findings do not fail the job. The check ran and its answer is the
    deliverable: the job completes, `result.status` is healthy / warning /
    error, and `result.issues` carries a capped sample.
    """
    await check_namespace_permission(identity, namespace, "read")

    job_id = f"val-{uuid.uuid4().hex[:16]}"
    options = {
        "check_term_refs": check_term_refs,
        "check_identity": check_identity,
        "limit": limit,
    }
    job = BackupJob(
        job_id=job_id,
        kind=BackupJobKind.VALIDATE,
        namespace=namespace,
        namespaces=[namespace],
        options=options,
        created_by=identity.identity_string if hasattr(identity, "identity_string") else str(identity),
    )
    await job.insert()

    runner = backup_service.make_validation_runner(job_id, namespace, options)
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
    not ``wip_archive.models.ProgressEvent``** — that type is an
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
    if request.mode not in ("restore", "merge"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode '{request.mode}' — must be 'restore' or 'merge'",
        )
    _validate_merge_options(
        request.mode,
        on_clash=request.on_clash,
        add_missing=request.add_missing,
        extend_terminologies=request.extend_terminologies,
        drop_stale_reporting=False,
    )

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
        "mode": request.mode,
        "target_namespace": effective_target,
        "on_clash": request.on_clash,
        "add_missing": request.add_missing,
        "extend_terminologies": request.extend_terminologies,
        "skip_documents": request.skip_documents,
        "skip_files": request.skip_files,
        "batch_size": request.batch_size,
        "dry_run": request.dry_run,
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
        job_id=new_job_id,
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
