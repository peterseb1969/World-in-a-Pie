"""Unit tests for ReplayService — mocks NATS and MongoDB."""

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock, PropertyMock

import pytest

from document_store.services.replay_service import ReplayService
from document_store.models.replay import ReplayStatus


@pytest.mark.asyncio
async def test_get_session_returns_none_for_unknown():
    """get_session returns None for non-existent session."""
    service = ReplayService()
    assert service.get_session("nonexistent") is None


@pytest.mark.asyncio
async def test_list_sessions_empty():
    """list_sessions returns empty list initially."""
    service = ReplayService()
    assert service.list_sessions() == []


@pytest.mark.asyncio
async def test_pause_unknown_session():
    """pause returns False for unknown session."""
    service = ReplayService()
    result = await service.pause("nonexistent")
    assert result is False


@pytest.mark.asyncio
async def test_resume_unknown_session():
    """resume returns False for unknown session."""
    service = ReplayService()
    result = await service.resume("nonexistent")
    assert result is False


@pytest.mark.asyncio
async def test_cancel_unknown_session():
    """cancel returns False for unknown session."""
    service = ReplayService()
    result = await service.cancel("nonexistent")
    assert result is False


@pytest.mark.asyncio
async def test_pause_resume_lifecycle():
    """Test pause/resume on a manually injected session."""
    from document_store.models.replay import ReplaySession, ReplayFilter

    service = ReplayService()
    session = ReplaySession(
        session_id="test-001",
        filter=ReplayFilter(namespace="wip"),
        stream_name="WIP_REPLAY_TEST001",
        subject_prefix="wip.replay.test-001",
        total_count=10,
        throttle_ms=10,
        batch_size=100,
        status=ReplayStatus.RUNNING,
    )
    service._sessions["test-001"] = session
    service._pause_flags["test-001"] = asyncio.Event()
    service._pause_flags["test-001"].set()  # running

    # Pause
    result = await service.pause("test-001")
    assert result is True
    assert session.status == ReplayStatus.PAUSED
    assert not service._pause_flags["test-001"].is_set()

    # Can't pause again
    result = await service.pause("test-001")
    assert result is False

    # Resume
    result = await service.resume("test-001")
    assert result is True
    assert session.status == ReplayStatus.RUNNING
    assert service._pause_flags["test-001"].is_set()

    # Can't resume again
    result = await service.resume("test-001")
    assert result is False


@pytest.mark.asyncio
async def test_cancel_running_session():
    """Cancel a running session cleans up."""
    from document_store.models.replay import ReplaySession, ReplayFilter

    service = ReplayService()
    session = ReplaySession(
        session_id="test-002",
        filter=ReplayFilter(namespace="wip"),
        stream_name="WIP_REPLAY_TEST002",
        subject_prefix="wip.replay.test-002",
        total_count=10,
        throttle_ms=10,
        batch_size=100,
        status=ReplayStatus.RUNNING,
    )
    service._sessions["test-002"] = session
    service._pause_flags["test-002"] = asyncio.Event()
    service._pause_flags["test-002"].set()

    # Create a real asyncio task that we can cancel
    async def _noop():
        await asyncio.sleep(999)

    real_task = asyncio.create_task(_noop())
    service._tasks["test-002"] = real_task

    with patch.object(service, "_cleanup_stream", new_callable=AsyncMock) as mock_cleanup:
        result = await service.cancel("test-002")

    assert result is True
    assert session.status == ReplayStatus.CANCELLED
    assert real_task.cancelled()
    mock_cleanup.assert_awaited_once_with("WIP_REPLAY_TEST002")


@pytest.mark.asyncio
async def test_cancel_completed_session():
    """Cancel a completed session still marks as cancelled."""
    from document_store.models.replay import ReplaySession, ReplayFilter

    service = ReplayService()
    session = ReplaySession(
        session_id="test-003",
        filter=ReplayFilter(namespace="wip"),
        stream_name="WIP_REPLAY_TEST003",
        subject_prefix="wip.replay.test-003",
        total_count=10,
        throttle_ms=10,
        batch_size=100,
        status=ReplayStatus.COMPLETED,
    )
    service._sessions["test-003"] = session

    with patch.object(service, "_cleanup_stream", new_callable=AsyncMock):
        result = await service.cancel("test-003")

    assert result is True
    assert session.status == ReplayStatus.CANCELLED
