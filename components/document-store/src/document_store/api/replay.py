"""Event replay API endpoints."""

from fastapi import APIRouter, Depends, HTTPException

from wip_auth import require_api_key

from ..models.replay import ReplayRequest, ReplaySessionResponse
from ..services.replay_service import get_replay_service

router = APIRouter(prefix="/replay", tags=["Replay"])


@router.post("/start", response_model=ReplaySessionResponse)
async def start_replay(
    request: ReplayRequest,
    _auth=Depends(require_api_key),
):
    """Start a replay session to republish stored documents as NATS events.

    Replayed events go to a separate NATS stream (WIP_REPLAY_{session_id})
    with metadata.replay=true so consumers can distinguish them from live events.
    """
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
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/{session_id}", response_model=ReplaySessionResponse)
async def get_replay_session(
    session_id: str,
    _auth=Depends(require_api_key),
):
    """Get the current state of a replay session."""
    service = get_replay_service()
    session = service.get_session(session_id)

    if not session:
        raise HTTPException(status_code=404, detail=f"Replay session {session_id} not found")

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
    _auth=Depends(require_api_key),
):
    """Pause a running replay session."""
    service = get_replay_service()

    success = await service.pause(session_id)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot pause — session not running")

    session = service.get_session(session_id)
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
    _auth=Depends(require_api_key),
):
    """Resume a paused replay session."""
    service = get_replay_service()

    success = await service.resume(session_id)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot resume — session not paused")

    session = service.get_session(session_id)
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
    _auth=Depends(require_api_key),
):
    """Cancel a replay session and delete its NATS stream."""
    service = get_replay_service()

    success = await service.cancel(session_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Replay session {session_id} not found")

    session = service.get_session(session_id)
    return ReplaySessionResponse(
        session_id=session["session_id"],
        status=session["status"],
        total_count=session["total_count"],
        published=session["published"],
        throttle_ms=session["throttle_ms"],
        message="Replay cancelled and stream deleted",
    )
