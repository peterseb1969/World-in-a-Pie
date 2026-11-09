"""Tests for ingest gateway FastAPI endpoints (health, status, metrics)."""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from ingest_gateway.main import app, state


@pytest_asyncio.fixture
async def client():
    """HTTP test client (does not trigger lifespan/NATS)."""
    transport = ASGITransport(app=app)
    headers = {"X-API-Key": os.environ["WIP_AUTH_LEGACY_API_KEY"]}
    async with AsyncClient(
        transport=transport, base_url="http://test", headers=headers
    ) as ac:
        yield ac


@pytest.fixture(autouse=True)
def mock_state():
    """Set up mock state for all tests, restore after."""
    originals = {
        "bus": state.bus,
        "worker": state.worker,
        "start_time": state.start_time,
    }

    state.bus = SimpleNamespace(connected=True)

    mock_worker = MagicMock()
    mock_worker.is_running = True
    mock_worker.messages_processed = 42
    mock_worker.messages_failed = 3
    mock_worker.messages_retried = 0
    mock_worker.messages_gave_up = 0
    mock_worker.messages_without_outcome = 1
    mock_worker.by_action = {
        "documents.bulk": {"success": 30, "partial": 7, "failed": 2},
        "terms.bulk": {"success": 2},
    }
    mock_worker.avg_duration_ms = 12.5
    state.worker = mock_worker

    state.start_time = 1_000_000.0

    yield

    state.bus = originals["bus"]
    state.worker = originals["worker"]
    state.start_time = originals["start_time"]


class TestHealthEndpoint:

    @pytest.mark.asyncio
    async def test_healthy(self, client):
        resp = await client.get("/api/ingest-gateway/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["nats_connected"] is True
        assert data["worker_running"] is True
        assert data["service"] == "wip-ingest-gateway"

    @pytest.mark.asyncio
    async def test_degraded_when_worker_stopped(self, client):
        state.worker.is_running = False

        resp = await client.get("/api/ingest-gateway/health")
        # The worker restarts on its own: still ready.
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["nats_connected"] is True
        assert data["worker_running"] is False

    @pytest.mark.asyncio
    async def test_unhealthy_when_nats_disconnected(self, client):
        state.bus.connected = False

        resp = await client.get("/api/ingest-gateway/health")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "unhealthy"
        assert data["nats_connected"] is False

    @pytest.mark.asyncio
    async def test_unhealthy_when_nats_not_configured(self, client):
        state.bus = SimpleNamespace(connected=False)

        resp = await client.get("/api/ingest-gateway/health")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "unhealthy"
        assert data["nats_connected"] is False


class TestStatusEndpoint:

    @pytest.mark.asyncio
    async def test_status(self, client):
        resp = await client.get("/api/ingest-gateway/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is True
        assert data["nats_connected"] is True
        assert data["messages_processed"] == 42
        assert data["messages_failed"] == 3
        assert data["uptime_seconds"] > 0

    @pytest.mark.asyncio
    async def test_status_no_worker(self, client):
        state.worker = None

        resp = await client.get("/api/ingest-gateway/status")
        data = resp.json()
        assert data["running"] is False
        assert data["messages_processed"] == 0
        assert data["messages_failed"] == 0


class TestMetricsEndpoint:

    @pytest.mark.asyncio
    async def test_metrics(self, client):
        resp = await client.get("/api/ingest-gateway/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_processed"] == 42
        assert data["total_failed"] == 3
        # A partial outcome is not a success.
        assert data["total_success"] == 32
        assert data["total_partial"] == 7
        assert data["total_without_outcome"] == 1
        assert data["by_action"]["documents.bulk"]["partial"] == 7
        assert data["avg_duration_ms"] == 12.5

    @pytest.mark.asyncio
    async def test_metrics_no_worker(self, client):
        state.worker = None

        resp = await client.get("/api/ingest-gateway/metrics")
        data = resp.json()
        assert data["total_processed"] == 0
        assert data["total_failed"] == 0
        assert data["total_success"] == 0


class TestRootEndpoint:

    @pytest.mark.asyncio
    async def test_root(self, client):
        resp = await client.get("/api/ingest-gateway/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["service"] == "wip-ingest-gateway"
        assert "version" in data
        assert data["health"] == "/api/ingest-gateway/health"
        assert data["docs"] == "/docs"
        # Build provenance, as on every service root.
        assert set(data["build"]) == {"version", "sha", "built_at", "image_tag"}


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/ingest-gateway/status", "/api/ingest-gateway/metrics"])
async def test_operational_routes_require_a_key(path):
    """Status and metrics expose worker internals; without a key, or with a
    wrong one, they refuse (CASE-855, Phase 2b C6)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as anon:
        assert (await anon.get(path)).status_code == 401
        wrong = await anon.get(path, headers={"X-API-Key": "not-a-key"})
        assert wrong.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/ingest-gateway/health", "/health"])
async def test_health_probe_needs_no_key(path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as anon:
        resp = await anon.get(path)
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


@pytest.mark.asyncio
async def test_liveness_answers_while_nats_is_down():
    """Liveness must not follow NATS: a restart cannot bring NATS back."""
    state.bus = SimpleNamespace(connected=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as anon:
        assert (await anon.get("/api/ingest-gateway/health")).status_code == 503
        assert (await anon.get("/health")).status_code == 503
        live = await anon.get("/livez")
    assert live.status_code == 200


def test_results_stream_expires_outcomes():
    """RESULTS_MAX_AGE_SECONDS reaches the results stream."""
    from ingest_gateway.config import settings
    from ingest_gateway.main import _ingest_streams

    results = next(s for s in _ingest_streams() if s.name == settings.nats_results_stream_name)
    assert results.max_age_seconds == settings.results_max_age_seconds > 0
