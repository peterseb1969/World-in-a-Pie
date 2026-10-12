"""Tests for ingest gateway FastAPI endpoints (health, status, metrics)."""

import os
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
        "nats_client": state.nats_client,
        "worker": state.worker,
        "start_time": state.start_time,
    }

    mock_nc = MagicMock()
    mock_nc.is_connected = True
    state.nats_client = mock_nc

    mock_worker = MagicMock()
    mock_worker.is_running = True
    mock_worker.messages_processed = 42
    mock_worker.messages_failed = 3
    state.worker = mock_worker

    state.start_time = 1_000_000.0

    yield

    state.nats_client = originals["nats_client"]
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
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["nats_connected"] is True
        assert data["worker_running"] is False

    @pytest.mark.asyncio
    async def test_unhealthy_when_nats_disconnected(self, client):
        state.nats_client.is_connected = False

        resp = await client.get("/api/ingest-gateway/health")
        data = resp.json()
        assert data["status"] == "unhealthy"
        assert data["nats_connected"] is False

    @pytest.mark.asyncio
    async def test_unhealthy_when_no_nats_client(self, client):
        state.nats_client = None

        resp = await client.get("/api/ingest-gateway/health")
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
        assert data["total_success"] == 39

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
async def test_health_probe_needs_no_key():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as anon:
        resp = await anon.get("/api/ingest-gateway/health")
    assert resp.status_code == 200
