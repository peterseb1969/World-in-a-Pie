"""Event replay API endpoints."""

from fastapi import APIRouter, Depends, HTTPException

from wip_auth import UserIdentity, check_namespace_permission, require_api_key

from ..models.replay import ReplayRequest, ReplaySessionResponse
from ..services.replay_service import get_replay_service

router = APIRouter(prefix="/replay", tags=["Replay"])


async def _enforce_replay_admin(session_id: str) -> dict:
    """CASE-384 — look up a replay session and require admin permission
    on its source namespace before pause/resume/cancel/get operations.

    Returns the session dict so the caller doesn't double-fetch.
    Raises HTTPException 404 if the session doesn't exist (same surface
    as the previous behaviour for missing sessions).
    """
    service = get_replay_service()
    session = service.get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"Replay session {session_id} not found",
        )
    # Filter shape: session["filter"]["namespace"]. Defensive default to
    # the empty string so a malformed session can't bypass auth via a
    # missing field.
    namespace = session.get("filter", {}).get("namespace", "")
    if not namespace:
        raise HTTPException(
            status_code=500,
            detail=f"Replay session {session_id} has no namespace recorded",
        )
    # _enforce_replay_admin is a helper called from handlers that already
    # bind `identity` via Depends(require_api_key); pull it back from the
    # ContextVar here since the helper isn't itself wired into FastAPI's DI.
    from wip_auth import require_current_identity
    identity = require_current_identity()
    await check_namespace_permission(identity, namespace, "admin")
    return session


@router.post("/start", response_model=ReplaySessionResponse)
async def start_replay(
    request: ReplayRequest,
    identity: UserIdentity = Depends(require_api_key),
):
    """Start a replay session to republish stored documents as NATS events.

    Replayed events go to a separate NATS stream (WIP_REPLAY_{session_id})
    with metadata.replay=true so consumers can distinguish them from live events.
    """
    # Replay is admin-level — it republishes events and can trigger downstream side effects
    await check_namespace_permission(identity, request.filter.namespace, "admin")

    service = get_replay_service()

    try:
        session = await service.start_replay(
            filter_config=request.filter.model_dump(),
            throttle_ms=request.throttle_ms,
            batch_size=request.batch_size,
        )
        return ReplaySessionResponse(
            session_id=session["session_id"],
            status=session["status"],
            total_count=session["total_count"],
            published=session["published"],
            throttle_ms=session["throttle_ms"],
            message=f"Replay started: {session['total_count']} documents to publish",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.get("/{session_id}", response_model=ReplaySessionResponse)
async def get_replay_session(
    session_id: str,
    identity: UserIdentity = Depends(require_api_key),
):
    """Get the current state of a replay session."""
    session = await _enforce_replay_admin(session_id)

    return ReplaySessionResponse(
        session_id=session["session_id"],
        status=session["status"],
        total_count=session["total_count"],
        published=session["published"],
        throttle_ms=session["throttle_ms"],
        message=f"{session['published']}/{session['total_count']} published",
    )


@router.post("/{session_id}/pause", response_model=ReplaySessionResponse)
async def pause_replay(
    session_id: str,
    identity: UserIdentity = Depends(require_api_key),
):
    """Pause a running replay session."""
    await _enforce_replay_admin(session_id)
    service = get_replay_service()

    success = await service.pause(session_id)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot pause — session not running")

    session = service.get_session(session_id)
    assert session is not None  # pause() succeeded → session exists
    return ReplaySessionResponse(
        session_id=session["session_id"],
        status=session["status"],
        total_count=session["total_count"],
        published=session["published"],
        throttle_ms=session["throttle_ms"],
        message="Replay paused",
    )


@router.post("/{session_id}/resume", response_model=ReplaySessionResponse)
async def resume_replay(
    session_id: str,
    identity: UserIdentity = Depends(require_api_key),
):
    """Resume a paused replay session."""
    await _enforce_replay_admin(session_id)
    service = get_replay_service()

    success = await service.resume(session_id)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot resume — session not paused")

    session = service.get_session(session_id)
    assert session is not None  # resume() succeeded → session exists
    return ReplaySessionResponse(
        session_id=session["session_id"],
        status=session["status"],
        total_count=session["total_count"],
        published=session["published"],
        throttle_ms=session["throttle_ms"],
        message="Replay resumed",
    )


@router.delete("/{session_id}", response_model=ReplaySessionResponse)
async def cancel_replay(
    session_id: str,
    identity: UserIdentity = Depends(require_api_key),
):
    """Cancel a replay session and delete its NATS stream."""
    await _enforce_replay_admin(session_id)
    service = get_replay_service()

    success = await service.cancel(session_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Replay session {session_id} not found")

    session = service.get_session(session_id)
    assert session is not None  # cancel() succeeded → session exists
    return ReplaySessionResponse(
        session_id=session["session_id"],
        status=session["status"],
        total_count=session["total_count"],
        published=session["published"],
        throttle_ms=session["throttle_ms"],
        message="Replay cancelled and stream deleted",
    )
