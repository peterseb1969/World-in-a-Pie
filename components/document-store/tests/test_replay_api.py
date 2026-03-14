"""Integration tests for replay API endpoints.

These tests mock the replay service to avoid needing NATS/MongoDB.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from httpx import AsyncClient

from document_store.models.replay import ReplayStatus


def _mock_replay_service(sessions=None):
    """Create a mock replay service with optional pre-loaded sessions."""
    mock = MagicMock()
    _sessions = dict(sessions or {})

    async def mock_start_replay(filter_config, throttle_ms=10, batch_size=100):
        return {
            "session_id": "abc12345",
            "status": "running",
            "total_count": 50,
            "published": 0,
            "throttle_ms": throttle_ms,
            "batch_size": batch_size,
            "filter": filter_config,
            "stream_name": "WIP_REPLAY_ABC12345",
            "subject_prefix": "wip.replay.abc12345",
        }

    mock.start_replay = mock_start_replay

    def mock_get_session(session_id):
        return _sessions.get(session_id)

    mock.get_session = mock_get_session

    async def mock_pause(session_id):
        s = _sessions.get(session_id)
        if not s or s["status"] != "running":
            return False
        s["status"] = "paused"
        return True

    mock.pause = mock_pause

    async def mock_resume(session_id):
        s = _sessions.get(session_id)
        if not s or s["status"] != "paused":
            return False
        s["status"] = "running"
        return True

    mock.resume = mock_resume

    async def mock_cancel(session_id):
        s = _sessions.get(session_id)
        if not s:
            return False
        s["status"] = "cancelled"
        return True

    mock.cancel = mock_cancel

    return mock


@pytest.mark.asyncio
async def test_start_replay(client: AsyncClient, auth_headers: dict):
    """POST /replay/start creates a replay session."""
    mock_service = _mock_replay_service()

    with patch("document_store.api.replay.get_replay_service", return_value=mock_service):
        response = await client.post(
            "/api/document-store/replay/start",
            headers=auth_headers,
            json={
                "filter": {"namespace": "wip", "template_value": "PATIENT"},
                "throttle_ms": 20,
                "batch_size": 50,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == "abc12345"
    assert data["status"] == "running"
    assert data["total_count"] == 50


@pytest.mark.asyncio
async def test_start_replay_no_docs(client: AsyncClient, auth_headers: dict):
    """POST /replay/start returns 400 when no docs match filter."""
    mock_service = MagicMock()

    async def fail_start(**kwargs):
        raise ValueError("No documents match the replay filter")

    mock_service.start_replay = fail_start

    with patch("document_store.api.replay.get_replay_service", return_value=mock_service):
        response = await client.post(
            "/api/document-store/replay/start",
            headers=auth_headers,
            json={"filter": {"namespace": "wip", "template_value": "NONEXISTENT"}},
        )

    assert response.status_code == 400
    assert "No documents" in response.json()["detail"]


@pytest.mark.asyncio
async def test_start_replay_nats_unavailable(client: AsyncClient, auth_headers: dict):
    """POST /replay/start returns 503 when NATS is down."""
    mock_service = MagicMock()

    async def fail_nats(**kwargs):
        raise RuntimeError("NATS not connected")

    mock_service.start_replay = fail_nats

    with patch("document_store.api.replay.get_replay_service", return_value=mock_service):
        response = await client.post(
            "/api/document-store/replay/start",
            headers=auth_headers,
            json={"filter": {"namespace": "wip"}},
        )

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_get_session(client: AsyncClient, auth_headers: dict):
    """GET /replay/{id} returns session state."""
    sessions = {
        "sess-001": {
            "session_id": "sess-001",
            "status": "running",
            "total_count": 100,
            "published": 42,
            "throttle_ms": 10,
        }
    }
    mock_service = _mock_replay_service(sessions)

    with patch("document_store.api.replay.get_replay_service", return_value=mock_service):
        response = await client.get(
            "/api/document-store/replay/sess-001",
            headers=auth_headers,
        )

    assert response.status_code == 200
    data = response.json()
    assert data["published"] == 42


@pytest.mark.asyncio
async def test_get_session_not_found(client: AsyncClient, auth_headers: dict):
    """GET /replay/{id} returns 404 for unknown session."""
    mock_service = _mock_replay_service()

    with patch("document_store.api.replay.get_replay_service", return_value=mock_service):
        response = await client.get(
            "/api/document-store/replay/nonexistent",
            headers=auth_headers,
        )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_pause_replay(client: AsyncClient, auth_headers: dict):
    """POST /replay/{id}/pause pauses a running session."""
    sessions = {
        "sess-002": {
            "session_id": "sess-002",
            "status": "running",
            "total_count": 100,
            "published": 30,
            "throttle_ms": 10,
        }
    }
    mock_service = _mock_replay_service(sessions)

    with patch("document_store.api.replay.get_replay_service", return_value=mock_service):
        response = await client.post(
            "/api/document-store/replay/sess-002/pause",
            headers=auth_headers,
        )

    assert response.status_code == 200
    assert response.json()["status"] == "paused"


@pytest.mark.asyncio
async def test_pause_not_running(client: AsyncClient, auth_headers: dict):
    """POST /replay/{id}/pause returns 400 if not running."""
    sessions = {
        "sess-003": {
            "session_id": "sess-003",
            "status": "completed",
            "total_count": 100,
            "published": 100,
            "throttle_ms": 10,
        }
    }
    mock_service = _mock_replay_service(sessions)

    with patch("document_store.api.replay.get_replay_service", return_value=mock_service):
        response = await client.post(
            "/api/document-store/replay/sess-003/pause",
            headers=auth_headers,
        )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_resume_replay(client: AsyncClient, auth_headers: dict):
    """POST /replay/{id}/resume resumes a paused session."""
    sessions = {
        "sess-004": {
            "session_id": "sess-004",
            "status": "paused",
            "total_count": 100,
            "published": 30,
            "throttle_ms": 10,
        }
    }
    mock_service = _mock_replay_service(sessions)

    with patch("document_store.api.replay.get_replay_service", return_value=mock_service):
        response = await client.post(
            "/api/document-store/replay/sess-004/resume",
            headers=auth_headers,
        )

    assert response.status_code == 200
    assert response.json()["status"] == "running"


@pytest.mark.asyncio
async def test_cancel_replay(client: AsyncClient, auth_headers: dict):
    """DELETE /replay/{id} cancels and cleans up."""
    sessions = {
        "sess-005": {
            "session_id": "sess-005",
            "status": "running",
            "total_count": 100,
            "published": 50,
            "throttle_ms": 10,
        }
    }
    mock_service = _mock_replay_service(sessions)

    with patch("document_store.api.replay.get_replay_service", return_value=mock_service):
        response = await client.delete(
            "/api/document-store/replay/sess-005",
            headers=auth_headers,
        )

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_not_found(client: AsyncClient, auth_headers: dict):
    """DELETE /replay/{id} returns 404 for unknown session."""
    mock_service = _mock_replay_service()

    with patch("document_store.api.replay.get_replay_service", return_value=mock_service):
        response = await client.delete(
            "/api/document-store/replay/nonexistent",
            headers=auth_headers,
        )

    assert response.status_code == 404
