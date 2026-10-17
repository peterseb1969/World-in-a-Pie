"""
WIP Ingest Gateway Service

FastAPI application providing health endpoints and NATS ingest management.
Consumes messages from WIP_INGEST stream and forwards to REST APIs.
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import APIRouter, Depends, FastAPI

from wip_auth import require_api_key, setup_auth, setup_key_sync
from wip_auth.events import EventBus, StreamSpec
from wip_auth.security import check_production_security

from . import __version__
from .config import settings
from .http_client import IngestHTTPClient
from .models import HealthResponse, MetricsResponse, StatusResponse
from .result_publisher import ResultPublisher
from .worker import IngestWorker

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class AppState:
    """Application state holding connections and worker."""

    # The shared self-healing NATS connection: connects in the background,
    # reconnects without limit, ensures both ingest streams, and supervises
    # the worker (restarting it whenever its consumer loop dies).
    bus: EventBus = EventBus(None, name="ingest-gateway")
    http_client: IngestHTTPClient | None = None
    result_publisher: ResultPublisher | None = None
    worker: IngestWorker | None = None
    worker_task: asyncio.Task | None = None
    start_time: float = 0.0


state = AppState()


def _ingest_streams() -> list[StreamSpec]:
    """The ingest gateway's two streams, ensured on every (re)connect."""
    return [
        # Explicit subjects so the ingest stream does not overlap the results
        # stream (wip.ingest.> would also capture wip.ingest.results.>).
        StreamSpec(
            name=settings.nats_ingest_stream_name,
            subjects=(
                "wip.ingest.terminologies.>",
                "wip.ingest.terms.>",
                "wip.ingest.templates.>",
                "wip.ingest.documents.>",
            ),
            max_msgs=settings.stream_max_msgs,
            max_bytes=settings.stream_max_bytes,
        ),
        StreamSpec(
            name=settings.nats_results_stream_name,
            subjects=("wip.ingest.results.>",),
            max_msgs=settings.stream_max_msgs,
            max_bytes=settings.stream_max_bytes,
        ),
    ]


async def _run_worker(js: Any) -> None:
    """One run of the ingest worker, bound to the bus's current connection."""
    assert state.worker is not None and state.result_publisher is not None
    state.result_publisher.js = js
    state.worker.nc = state.bus.client
    state.worker.js = js
    await state.worker.start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - startup and shutdown."""
    # Refuse to start in a production variant on known-default secrets.
    check_production_security()
    logger.info(f"Starting {settings.service_name} v{__version__}...")
    state.start_time = time.time()

    # NATS connects in the background and heals itself, so a late or
    # vanished NATS never blocks startup or leaves the worker dead.
    state.bus = EventBus(settings.nats_url, name="ingest-gateway", streams=_ingest_streams())
    await state.bus.start()

    # Initialize HTTP client
    state.http_client = IngestHTTPClient()

    # The publisher and worker are bound to a JetStream context per run.
    state.result_publisher = ResultPublisher(None)
    state.worker = IngestWorker(None, None, state.http_client, state.result_publisher)
    state.worker_task = state.bus.supervise("ingest-worker", _run_worker)
    logger.info("Ingest worker scheduled (runs while NATS is connected)")

    # Pick up runtime API keys (created via the Registry) like the other
    # services; REGISTRY_URL / REGISTRY_API_KEY come from the environment.
    key_sync = await setup_key_sync(_providers)
    if key_sync:
        logger.info("Key sync started (polling Registry for runtime API keys)")

    yield

    # Shutdown
    logger.info(f"Shutting down {settings.service_name}...")

    if key_sync:
        await key_sync.stop()

    # Stop worker
    if state.worker:
        await state.worker.stop()

    if state.worker_task:
        state.worker_task.cancel()
        with suppress(asyncio.CancelledError):
            await state.worker_task

    # Close HTTP client
    if state.http_client:
        await state.http_client.close()

    # Stop the NATS event bus
    await state.bus.stop()
    logger.info("NATS event bus stopped")

    logger.info(f"{settings.service_name} shutdown complete")


app = FastAPI(
    title="WIP Ingest Gateway",
    description="JetStream-based ingestion gateway for WIP services",
    version=__version__,
    lifespan=lifespan,
)

# Authentication (reads WIP_AUTH_* env vars; MASTER_API_KEY maps to the
# legacy key). The
# middleware only IDENTIFIES the caller; the router-level require_api_key
# below enforces. The health probe and the service root live on the app,
# outside the router, and the probe is exempted so a stale key on a monitor
# does not turn into a 401.
_providers = setup_auth(app, public_paths=["/api/ingest-gateway/health"])

router = APIRouter(
    prefix="/api/ingest-gateway", dependencies=[Depends(require_api_key)]
)


@app.get("/api/ingest-gateway/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint."""
    nats_ok = state.bus.connected
    worker_ok = state.worker is not None and state.worker.is_running

    if nats_ok and worker_ok:
        status = "healthy"
    elif nats_ok:
        status = "degraded"
    else:
        status = "unhealthy"

    return HealthResponse(
        status=status,
        service=settings.service_name,
        version=__version__,
        nats_connected=nats_ok,
        worker_running=worker_ok,
        details={
            "nats_url": settings.nats_url,
            "ingest_stream": settings.nats_ingest_stream_name,
            "results_stream": settings.nats_results_stream_name,
            # Redeliveries after transient forward failures, and messages
            # given up on after the last allowed delivery. Reported, not
            # turned into a non-200: this endpoint is also the liveness
            # probe, and restarting the gateway cannot fix a down target.
            "messages_retried": state.worker.messages_retried if state.worker else 0,
            "messages_gave_up": state.worker.messages_gave_up if state.worker else 0,
        },
    )


@router.get("/status", response_model=StatusResponse)
async def get_status() -> StatusResponse:
    """Get current worker status and statistics."""
    nats_ok = state.bus.connected
    uptime = time.time() - state.start_time if state.start_time > 0 else 0

    return StatusResponse(
        running=state.worker.is_running if state.worker else False,
        nats_connected=nats_ok,
        messages_processed=state.worker.messages_processed if state.worker else 0,
        messages_failed=state.worker.messages_failed if state.worker else 0,
        messages_retried=state.worker.messages_retried if state.worker else 0,
        messages_gave_up=state.worker.messages_gave_up if state.worker else 0,
        uptime_seconds=uptime,
    )


@router.get("/metrics", response_model=MetricsResponse)
async def get_metrics() -> MetricsResponse:
    """Get detailed metrics."""
    uptime = time.time() - state.start_time if state.start_time > 0 else 0
    total_processed = state.worker.messages_processed if state.worker else 0
    total_failed = state.worker.messages_failed if state.worker else 0
    total_success = total_processed - total_failed

    # Calculate average duration (placeholder - would need tracking)
    avg_duration = 0.0

    return MetricsResponse(
        total_processed=total_processed,
        total_failed=total_failed,
        total_success=total_success,
        by_action={},  # Would need per-action tracking
        avg_duration_ms=avg_duration,
        uptime_seconds=uptime,
    )


# On the app, not the router: a public service-root probe, like the other
# services' prefixed roots.
@app.get("/api/ingest-gateway/")
async def root() -> dict[str, Any]:
    """Root endpoint with service info."""
    return {
        "service": settings.service_name,
        "version": __version__,
        "docs": "/docs",
        "health": "/api/ingest-gateway/health",
        "status": "/api/ingest-gateway/status",
        "metrics": "/api/ingest-gateway/metrics",
    }

app.include_router(router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "ingest_gateway.main:app",
        host="0.0.0.0",
        port=8006,
        reload=settings.debug,
    )
