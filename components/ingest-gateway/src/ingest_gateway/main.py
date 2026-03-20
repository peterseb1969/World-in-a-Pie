"""
WIP Ingest Gateway Service

FastAPI application providing health endpoints and NATS ingest management.
Consumes messages from WIP_INGEST stream and forwards to REST APIs.
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

import nats
from fastapi import FastAPI
from nats.js import JetStreamContext

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

    nats_client: nats.NATS | None = None
    jetstream: JetStreamContext | None = None
    http_client: IngestHTTPClient | None = None
    result_publisher: ResultPublisher | None = None
    worker: IngestWorker | None = None
    worker_task: asyncio.Task | None = None
    start_time: float = 0.0


state = AppState()


async def connect_nats() -> tuple[nats.NATS, JetStreamContext]:
    """Connect to NATS and get JetStream context."""
    logger.info(f"Connecting to NATS at {settings.nats_url}...")
    nc = await nats.connect(settings.nats_url)
    js = nc.jetstream()

    # Ensure WIP_INGEST stream exists
    try:
        stream_info = await js.stream_info(settings.nats_ingest_stream_name)
        logger.info(
            f"Stream {settings.nats_ingest_stream_name} exists "
            f"(messages: {stream_info.state.messages})"
        )
    except nats.js.errors.NotFoundError:
        logger.info(f"Creating stream {settings.nats_ingest_stream_name}...")
        # Use explicit subjects to avoid overlap with results stream
        # (wip.ingest.> would also capture wip.ingest.results.>)
        await js.add_stream(
            name=settings.nats_ingest_stream_name,
            subjects=[
                "wip.ingest.terminologies.>",
                "wip.ingest.terms.>",
                "wip.ingest.templates.>",
                "wip.ingest.documents.>",
            ],
            retention="limits",
            max_msgs=settings.stream_max_msgs,
            max_bytes=settings.stream_max_bytes,
        )
        logger.info(f"Stream {settings.nats_ingest_stream_name} created")

    # Ensure WIP_INGEST_RESULTS stream exists
    try:
        results_info = await js.stream_info(settings.nats_results_stream_name)
        logger.info(
            f"Stream {settings.nats_results_stream_name} exists "
            f"(messages: {results_info.state.messages})"
        )
    except nats.js.errors.NotFoundError:
        logger.info(f"Creating stream {settings.nats_results_stream_name}...")
        # Note: max_age removed due to nats-py serialization issue
        await js.add_stream(
            name=settings.nats_results_stream_name,
            subjects=["wip.ingest.results.>"],
            retention="limits",
            max_msgs=settings.stream_max_msgs,
            max_bytes=settings.stream_max_bytes,
        )
        logger.info(f"Stream {settings.nats_results_stream_name} created")

    logger.info("Connected to NATS with JetStream")
    return nc, js


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - startup and shutdown."""
    logger.info(f"Starting {settings.service_name} v{__version__}...")
    state.start_time = time.time()

    # Connect to NATS
    try:
        state.nats_client, state.jetstream = await connect_nats()
    except Exception as e:
        logger.error(f"Failed to connect to NATS: {e}")
        raise

    # Initialize HTTP client
    state.http_client = IngestHTTPClient()

    # Initialize result publisher
    state.result_publisher = ResultPublisher(state.jetstream)

    # Create and start worker
    state.worker = IngestWorker(
        state.nats_client,
        state.jetstream,
        state.http_client,
        state.result_publisher,
    )
    state.worker_task = asyncio.create_task(state.worker.start())
    logger.info("Ingest worker started")

    yield

    # Shutdown
    logger.info(f"Shutting down {settings.service_name}...")

    # Stop worker
    if state.worker:
        await state.worker.stop()

    if state.worker_task:
        state.worker_task.cancel()
        try:
            await state.worker_task
        except asyncio.CancelledError:
            pass

    # Close HTTP client
    if state.http_client:
        await state.http_client.close()

    # Close NATS connection
    if state.nats_client:
        await state.nats_client.close()
        logger.info("NATS connection closed")

    logger.info(f"{settings.service_name} shutdown complete")


app = FastAPI(
    title="WIP Ingest Gateway",
    description="JetStream-based ingestion gateway for WIP services",
    version=__version__,
    lifespan=lifespan,
)


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint."""
    nats_ok = state.nats_client is not None and state.nats_client.is_connected
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
        },
    )


@app.get("/status", response_model=StatusResponse)
async def get_status() -> StatusResponse:
    """Get current worker status and statistics."""
    nats_ok = state.nats_client is not None and state.nats_client.is_connected
    uptime = time.time() - state.start_time if state.start_time > 0 else 0

    return StatusResponse(
        running=state.worker.is_running if state.worker else False,
        nats_connected=nats_ok,
        messages_processed=state.worker.messages_processed if state.worker else 0,
        messages_failed=state.worker.messages_failed if state.worker else 0,
        uptime_seconds=uptime,
    )


@app.get("/metrics", response_model=MetricsResponse)
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


@app.get("/")
async def root() -> dict[str, Any]:
    """Root endpoint with service info."""
    return {
        "service": settings.service_name,
        "version": __version__,
        "docs": "/docs",
        "health": "/health",
        "status": "/status",
        "metrics": "/metrics",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "ingest_gateway.main:app",
        host="0.0.0.0",
        port=8006,
        reload=settings.debug,
    )
