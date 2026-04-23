"""
WIP Reporting Sync Service

FastAPI application providing health endpoints and sync management.
The actual sync work is done by the worker module.
"""

import asyncio
import contextlib
import csv
import io
import logging
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import asyncpg
import httpx
import nats
from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from nats.js import JetStreamContext
from pydantic import BaseModel, Field

from wip_auth.ratelimit import setup_rate_limiting
from wip_auth.security import check_production_security
from wip_auth.startup import retry_async

from . import __version__
from .batch_sync import BatchSyncService
from .config import settings
from .metrics import metrics
from .models import (
    AlertConfig,
    AlertsResponse,
    BatchSyncJob,
    BatchSyncResponse,
    ConsumerInfo,
    HealthResponse,
    MetricsResponse,
    SyncStatus,
)
from .search_service import (
    ActivityResponse,
    EntityReferencesResponse,
    ReferencedByResponse,
    SearchRequest,
    SearchResponse,
    SearchService,
    TermDocumentsResponse,
)
from .worker import run_sync_worker

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# Global connections
class AppState:
    """Application state holding connections."""

    nats_client: nats.NATS | None = None
    jetstream: JetStreamContext | None = None
    postgres_pool: asyncpg.Pool | None = None
    sync_task: asyncio.Task | None = None
    alert_check_task: asyncio.Task | None = None
    initial_sync_task: asyncio.Task | None = None
    batch_sync_service: BatchSyncService | None = None
    search_service: SearchService | None = None
    sync_status: SyncStatus = SyncStatus(
        running=False,
        connected_to_nats=False,
        connected_to_postgres=False,
    )


state = AppState()


async def get_consumer_info() -> ConsumerInfo | None:
    """Get NATS consumer information including pending messages."""
    if not state.jetstream:
        return None

    try:
        consumer = await state.jetstream.consumer_info(
            settings.nats_stream_name,
            settings.nats_durable_name,
        )
        return ConsumerInfo(
            stream_name=settings.nats_stream_name,
            consumer_name=settings.nats_durable_name,
            pending_messages=consumer.num_pending,
            pending_bytes=0,  # Not directly available
            delivered_messages=consumer.delivered.stream_seq,
            ack_pending=consumer.num_ack_pending,
            redelivered=consumer.num_redelivered,
            last_delivered=None,  # Would need to parse from consumer info
        )
    except Exception as e:
        logger.debug(f"Could not get consumer info: {e}")
        return None


async def run_alert_check_loop() -> None:
    """Background task that periodically checks alert conditions."""
    logger.info("Alert check loop started")

    while True:
        try:
            config = metrics.get_alert_config()
            await asyncio.sleep(config.check_interval_seconds)

            if not config.enabled:
                continue

            # Get current state
            nats_ok = state.nats_client is not None and state.nats_client.is_connected
            postgres_ok = state.postgres_pool is not None

            # Quick postgres check
            if postgres_ok:
                try:
                    async with state.postgres_pool.acquire() as conn:
                        await conn.fetchval("SELECT 1")
                except Exception:
                    postgres_ok = False

            consumer_info = await get_consumer_info()

            # Check alerts
            await metrics.check_alerts(consumer_info, nats_ok, postgres_ok)

        except asyncio.CancelledError:
            logger.info("Alert check loop cancelled")
            break
        except Exception as e:
            logger.error(f"Error in alert check loop: {e}")
            await asyncio.sleep(10)  # Back off on error


# CASE-52: NATS client callbacks. Update sync_status on connection
# lifecycle events so the /status endpoint reflects reality, not just
# "did we connect at startup." Belt-and-suspenders: the status endpoint
# also does a live is_connected check, so missed callbacks don't leave
# a stale flag.
async def _on_nats_disconnected() -> None:
    logger.warning("NATS connection dropped")
    state.sync_status.connected_to_nats = False


async def _on_nats_reconnected() -> None:
    logger.info("NATS connection re-established")
    state.sync_status.connected_to_nats = True


async def _on_nats_closed() -> None:
    logger.warning("NATS connection closed")
    state.sync_status.connected_to_nats = False


async def connect_nats() -> tuple[nats.NATS, JetStreamContext]:
    """Connect to NATS and get JetStream context."""
    logger.info(f"Connecting to NATS at {settings.nats_url}...")
    nc = await nats.connect(
        settings.nats_url,
        disconnected_cb=_on_nats_disconnected,
        reconnected_cb=_on_nats_reconnected,
        closed_cb=_on_nats_closed,
    )
    js = nc.jetstream()

    # Ensure the stream exists
    try:
        await js.stream_info(settings.nats_stream_name)
        logger.info(f"Stream {settings.nats_stream_name} exists")
    except nats.js.errors.NotFoundError:
        # Create the stream if it doesn't exist
        logger.info(f"Creating stream {settings.nats_stream_name}...")
        await js.add_stream(
            name=settings.nats_stream_name,
            subjects=[
                "wip.documents.>",
                "wip.templates.>",
                "wip.terminologies.>",
                "wip.terms.>",
                "wip.relationships.>",
            ],
            retention="limits",
            max_msgs=1_000_000,
            max_bytes=1024 * 1024 * 1024,  # 1GB
        )
        logger.info(f"Stream {settings.nats_stream_name} created")

    logger.info("Connected to NATS with JetStream")
    return nc, js


async def connect_postgres() -> asyncpg.Pool:
    """Connect to PostgreSQL and return connection pool."""
    logger.info(
        f"Connecting to PostgreSQL at {settings.postgres_host}:{settings.postgres_port}..."
    )
    pool = await asyncpg.create_pool(
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
        min_size=settings.postgres_pool_min,
        max_size=settings.postgres_pool_max,
    )
    logger.info("Connected to PostgreSQL")
    return pool


async def init_postgres_schema(pool: asyncpg.Pool) -> None:
    """Initialize PostgreSQL schema (migration tracking table, etc.)."""
    async with pool.acquire() as conn:
        # Create schema migrations tracking table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS _wip_schema_migrations (
                template_value TEXT NOT NULL,
                template_version INTEGER NOT NULL,
                migration_sql TEXT NOT NULL,
                applied_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                PRIMARY KEY (template_value, template_version)
            )
        """)

        # Create sync status tracking table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS _wip_sync_status (
                template_value TEXT PRIMARY KEY,
                last_sync_at TIMESTAMP WITH TIME ZONE,
                documents_synced BIGINT DEFAULT 0,
                last_error TEXT,
                last_error_at TIMESTAMP WITH TIME ZONE
            )
        """)

        logger.info("PostgreSQL schema initialized")


async def _initial_metadata_sync(batch_sync_service: BatchSyncService) -> None:
    """Background: wait for def-store to be healthy, then batch-sync metadata.

    `batch_sync_terminologies` and `batch_sync_terms` swallow connection
    errors internally and return empty results — a single attempt against
    a not-yet-ready def-store silently yields 0-row tables. Polling
    def-store's /health first and only then calling the batch syncs
    makes them succeed on the first attempt rather than silently fail.
    """
    async def _verify_def_store_ready() -> None:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.def_store_url}/health")
            resp.raise_for_status()

    try:
        await retry_async(
            _verify_def_store_ready,
            retry_on=(
                httpx.ConnectError,
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.HTTPStatusError,
            ),
            description="Def-Store health check (initial metadata sync)",
        )
        logger.info("Def-Store healthy, running initial metadata sync...")
    except TimeoutError as e:
        logger.error(f"Def-Store never became healthy: {e}")
        logger.error(
            "Initial metadata sync skipped; trigger POST /sync/terminologies "
            "manually once def-store is reachable."
        )
        return

    try:
        t_result = await batch_sync_service.batch_sync_terminologies()
        logger.info(f"Initial terminology sync: {t_result}")
        t_result = await batch_sync_service.batch_sync_terms()
        logger.info(f"Initial term sync: {t_result}")
    except Exception as e:
        logger.error(f"Initial terminology/term sync failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - startup and shutdown."""
    # Startup — security check first
    check_production_security()
    logger.info(f"Starting {settings.service_name} v{__version__}...")

    try:
        # Connect to NATS with retry — tolerates NATS not being ready yet
        # on fresh k8s boot, node drain, pod reschedule.
        state.nats_client, state.jetstream = await retry_async(
            connect_nats,
            retry_on=(ConnectionRefusedError, OSError, nats.errors.NoServersError),
            description="NATS connect",
        )
        state.sync_status.connected_to_nats = True
    except Exception as e:
        logger.error(f"Failed to connect to NATS: {e}")
        state.sync_status.connected_to_nats = False

    try:
        # Connect to PostgreSQL with retry — same reasoning as NATS above.
        state.postgres_pool = await retry_async(
            connect_postgres,
            retry_on=(ConnectionRefusedError, OSError),
            description="PostgreSQL connect",
        )
        state.sync_status.connected_to_postgres = True

        # Initialize schema
        await init_postgres_schema(state.postgres_pool)

        # Ensure metadata tables exist
        from .schema_manager import SchemaManager
        sm = SchemaManager(state.postgres_pool)
        await sm.ensure_terminologies_table()
        await sm.ensure_terms_table()
        await sm.ensure_templates_table()
        await sm.ensure_term_relationships_table()
    except Exception as e:
        logger.error(f"Failed to connect to PostgreSQL: {e}")
        state.sync_status.connected_to_postgres = False

    # Initialize batch sync service
    if state.postgres_pool:
        state.batch_sync_service = BatchSyncService(state.postgres_pool)
        logger.info("Batch sync service initialized")

        # Initial metadata sync runs as a background task. Waits for
        # def-store to be healthy before firing batch_sync_terminologies/
        # batch_sync_terms, because those methods swallow connection
        # errors internally — a single attempt against a not-yet-ready
        # def-store silently yields empty tables. Backgrounded so it
        # doesn't block the HTTP listener either.
        state.initial_sync_task = asyncio.create_task(
            _initial_metadata_sync(state.batch_sync_service)
        )
        logger.info("Initial metadata sync scheduled (background)")

    # Initialize search service (works with or without PostgreSQL)
    state.search_service = SearchService(state.postgres_pool)
    logger.info("Search service initialized")

    # Start the sync worker task if both connections are up
    if state.sync_status.connected_to_nats and state.sync_status.connected_to_postgres:
        state.sync_task = asyncio.create_task(
            run_sync_worker(
                state.nats_client,
                state.jetstream,
                state.postgres_pool,
                state.sync_status,
            )
        )
        state.sync_status.running = True
        logger.info("Sync worker started")
    else:
        logger.warning("Sync worker not started - missing connections")

    # Start alert check background task
    state.alert_check_task = asyncio.create_task(run_alert_check_loop())
    logger.info("Alert check loop started")

    logger.info(f"{settings.service_name} started successfully")

    yield

    # Shutdown
    logger.info(f"Shutting down {settings.service_name}...")

    # Cancel alert check task
    if state.alert_check_task:
        state.alert_check_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await state.alert_check_task

    # Cancel sync task
    if state.sync_task:
        state.sync_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await state.sync_task

    # Cancel initial metadata sync task (if still running)
    if state.initial_sync_task and not state.initial_sync_task.done():
        state.initial_sync_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await state.initial_sync_task

    # Close connections
    if state.postgres_pool:
        await state.postgres_pool.close()
        logger.info("PostgreSQL connection closed")

    if state.nats_client:
        await state.nats_client.close()
        logger.info("NATS connection closed")

    logger.info(f"{settings.service_name} shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="WIP Reporting Sync",
    description="Syncs documents from MongoDB to PostgreSQL for reporting",
    version=__version__,
    lifespan=lifespan,
)

# Setup rate limiting (reads WIP_RATE_LIMIT, default 40000/minute)
setup_rate_limiting(app)

router = APIRouter(prefix="/api/reporting-sync")


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint."""
    nats_ok = state.nats_client is not None and state.nats_client.is_connected
    postgres_ok = state.postgres_pool is not None

    # Quick postgres check
    if postgres_ok:
        try:
            async with state.postgres_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
        except Exception:
            postgres_ok = False

    if nats_ok and postgres_ok:
        status = "healthy"
    elif nats_ok or postgres_ok:
        status = "degraded"
    else:
        status = "unhealthy"

    return HealthResponse(
        status=status,
        service=settings.service_name,
        version=__version__,
        nats_connected=nats_ok,
        postgres_connected=postgres_ok,
        details={
            "nats_url": settings.nats_url,
            "postgres_host": settings.postgres_host,
            "stream_name": settings.nats_stream_name,
        },
    )


@router.get("/status", response_model=SyncStatus)
async def get_sync_status() -> SyncStatus:
    """Get current sync worker status.

    CASE-52: refresh the connection flags from live state on every call.
    The lifespan startup sets them once; NATS callbacks (if connected
    to a client that fires them) update on disconnect/reconnect; this
    endpoint is the final authority that a caller sees reality, not a
    latched startup flag. Postgres is probed with a short-timeout
    `SELECT 1` — asyncpg has no cheap liveness property.
    """
    state.sync_status.connected_to_nats = bool(
        state.nats_client and state.nats_client.is_connected
    )
    state.sync_status.connected_to_postgres = await _postgres_live_check()
    return state.sync_status


async def _postgres_live_check() -> bool:
    """Probe the PostgreSQL pool with a 1-second-timeout SELECT 1."""
    if not state.postgres_pool:
        return False
    try:
        async with state.postgres_pool.acquire() as conn:
            await asyncio.wait_for(conn.fetchval("SELECT 1"), timeout=1.0)
        return True
    except Exception:
        return False


# =============================================================================
# MONITORING & METRICS ENDPOINTS
# =============================================================================


@router.get("/metrics", response_model=MetricsResponse)
async def get_metrics() -> MetricsResponse:
    """
    Get comprehensive metrics for the sync service.

    Includes:
    - Uptime and connection status
    - Event processing stats (processed, failed, rate)
    - NATS consumer info (pending messages, lag)
    - Processing latency statistics (min, max, avg, percentiles)
    - Per-template sync statistics
    - Error breakdown by type
    """
    nats_ok = state.nats_client is not None and state.nats_client.is_connected
    postgres_ok = state.postgres_pool is not None

    # Quick postgres check
    if postgres_ok:
        try:
            async with state.postgres_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
        except Exception:
            postgres_ok = False

    consumer_info = await get_consumer_info()

    return metrics.build_metrics_response(
        nats_connected=nats_ok,
        postgres_connected=postgres_ok,
        consumer_info=consumer_info,
    )


@router.get("/metrics/consumer", response_model=ConsumerInfo | None)
async def get_consumer_metrics() -> ConsumerInfo | None:
    """
    Get NATS consumer information.

    Returns details about the JetStream consumer including:
    - Pending messages (queue depth)
    - Acknowledgement pending count
    - Redelivered message count
    - Delivered message count
    """
    return await get_consumer_info()


@router.get("/alerts", response_model=AlertsResponse)
async def get_alerts() -> AlertsResponse:
    """
    Get current alerts and alert configuration.

    Returns:
    - Current alert configuration (thresholds, webhook settings)
    - Active alerts (currently triggered)
    - Recently resolved alerts
    """
    return AlertsResponse(
        config=metrics.get_alert_config(),
        active_alerts=metrics.get_active_alerts(),
        resolved_alerts=metrics.get_resolved_alerts(),
    )


@router.put("/alerts/config", response_model=AlertConfig)
async def update_alert_config(config: AlertConfig) -> AlertConfig:
    """
    Update alert configuration.

    Configure:
    - enabled: Enable/disable alert checking
    - check_interval_seconds: How often to check alert conditions
    - thresholds: Warning/critical thresholds for each alert type
    - webhook_url: Optional webhook for notifications
    - webhook_headers: Custom headers for webhook requests
    """
    metrics.update_alert_config(config)
    return metrics.get_alert_config()


@router.post("/alerts/test")
async def test_alerts() -> dict[str, Any]:
    """
    Manually trigger an alert check and return results.

    Useful for testing alert configuration without waiting for the check interval.
    """
    nats_ok = state.nats_client is not None and state.nats_client.is_connected
    postgres_ok = state.postgres_pool is not None

    if postgres_ok:
        try:
            async with state.postgres_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
        except Exception:
            postgres_ok = False

    consumer_info = await get_consumer_info()
    new_alerts = await metrics.check_alerts(consumer_info, nats_ok, postgres_ok)

    return {
        "checked": True,
        "nats_connected": nats_ok,
        "postgres_connected": postgres_ok,
        "consumer_pending": consumer_info.pending_messages if consumer_info else None,
        "new_alerts": [a.model_dump(mode="json") for a in new_alerts],
        "active_alerts": [a.model_dump(mode="json") for a in metrics.get_active_alerts()],
    }


@router.get("/schema/{template_value}")
async def get_schema(template_value: str) -> dict[str, Any]:
    """Get the PostgreSQL schema for a template."""
    if not state.postgres_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not connected")

    table_name = f"doc_{template_value.lower()}"

    async with state.postgres_pool.acquire() as conn:
        # Check if table exists
        exists = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = 'public'
                AND table_name = $1
            )
            """,
            table_name,
        )

        if not exists:
            raise HTTPException(
                status_code=404,
                detail=f"Table {table_name} does not exist",
            )

        # Get column information
        columns = await conn.fetch(
            """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = $1
            ORDER BY ordinal_position
            """,
            table_name,
        )

        # Get row count
        row_count = await conn.fetchval(f'SELECT COUNT(*) FROM "{table_name}"')

        return {
            "template_value": template_value,
            "table_name": table_name,
            "columns": [
                {
                    "name": col["column_name"],
                    "type": col["data_type"],
                    "nullable": col["is_nullable"] == "YES",
                    "default": col["column_default"],
                }
                for col in columns
            ],
            "row_count": row_count,
        }


@router.post("/sync/batch/terminologies")
async def trigger_terminology_sync(
    namespace: str,
    page_size: int = 100,
) -> dict[str, Any]:
    """
    Batch sync all terminologies from Def-Store to PostgreSQL.

    Args:
        namespace: Namespace to sync (default: wip)
        page_size: Page size for API fetches (default: 100)
    """
    if not state.batch_sync_service:
        raise HTTPException(status_code=503, detail="Batch sync service not available")

    result = await state.batch_sync_service.batch_sync_terminologies(
        namespace=namespace,
        page_size=page_size,
    )

    return {
        "status": "completed",
        "table": "terminologies",
        **result,
    }


@router.post("/sync/batch/terms")
async def trigger_term_sync(
    namespace: str,
    page_size: int = 100,
) -> dict[str, Any]:
    """
    Batch sync all terms from Def-Store to PostgreSQL.

    Iterates through all terminologies and fetches their terms.

    Args:
        namespace: Namespace to sync (default: wip)
        page_size: Page size for API fetches (default: 100)
    """
    if not state.batch_sync_service:
        raise HTTPException(status_code=503, detail="Batch sync service not available")

    result = await state.batch_sync_service.batch_sync_terms(
        namespace=namespace,
        page_size=page_size,
    )

    return {
        "status": "completed",
        "table": "terms",
        **result,
    }


@router.post("/sync/batch/relationships")
async def trigger_relationship_sync(
    namespace: str,
    page_size: int = 100,
) -> dict[str, Any]:
    """
    Batch sync all term relationships from Def-Store to PostgreSQL.

    Fetches all active relationships via the Def-Store ontology API
    and upserts them into the term_relationships table.

    Args:
        namespace: Namespace to sync (default: wip)
        page_size: Page size for API fetches (default: 100)
    """
    if not state.batch_sync_service:
        raise HTTPException(status_code=503, detail="Batch sync service not available")

    result = await state.batch_sync_service.batch_sync_relationships(
        namespace=namespace,
        page_size=page_size,
    )

    return {
        "status": "completed",
        "table": "term_relationships",
        **result,
    }


@router.post("/sync/batch", response_model=list[BatchSyncResponse])
async def trigger_batch_sync_all(
    force: bool = False,
    page_size: int = 100,
) -> list[BatchSyncResponse]:
    """
    Trigger batch sync for all templates.

    This fetches all templates and syncs their documents to PostgreSQL.
    Templates with sync_enabled=false are skipped.
    """
    if not state.batch_sync_service:
        raise HTTPException(status_code=503, detail="Batch sync service not available")

    jobs = await state.batch_sync_service.start_batch_sync_all(
        force=force,
        page_size=page_size,
    )

    return [
        BatchSyncResponse(
            job_id=job.job_id,
            template_value=job.template_value,
            status=job.status,
            message=f"Batch sync started for {job.template_value}",
        )
        for job in jobs
    ]


@router.get("/sync/batch/jobs", response_model=list[BatchSyncJob])
async def list_batch_jobs() -> list[BatchSyncJob]:
    """List all batch sync jobs."""
    if not state.batch_sync_service:
        raise HTTPException(status_code=503, detail="Batch sync service not available")

    return state.batch_sync_service.list_jobs()


@router.get("/sync/batch/jobs/{job_id}", response_model=BatchSyncJob)
async def get_batch_job(job_id: str) -> BatchSyncJob:
    """Get a specific batch sync job by ID."""
    if not state.batch_sync_service:
        raise HTTPException(status_code=503, detail="Batch sync service not available")

    job = state.batch_sync_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return job


@router.post("/sync/batch/{template_value}", response_model=BatchSyncResponse)
async def trigger_batch_sync(
    template_value: str,
    force: bool = False,
    page_size: int = 100,
) -> BatchSyncResponse:
    """
    Trigger a batch sync for a specific template.

    This fetches all documents for the template from Document Store
    and syncs them to PostgreSQL.

    Args:
        template_value: Template code to sync
        force: Force re-sync even if table already has data
        page_size: Number of documents to fetch per page (10-1000)
    """
    if not state.batch_sync_service:
        raise HTTPException(status_code=503, detail="Batch sync service not available")

    if page_size < 10 or page_size > 1000:
        raise HTTPException(status_code=400, detail="page_size must be between 10 and 1000")

    job = await state.batch_sync_service.start_batch_sync(
        template_value=template_value,
        force=force,
        page_size=page_size,
    )

    return BatchSyncResponse(
        job_id=job.job_id,
        template_value=job.template_value,
        status=job.status,
        message=f"Batch sync started for {template_value}",
    )


@router.delete("/sync/batch/jobs/{job_id}")
async def cancel_batch_job(job_id: str) -> dict[str, Any]:
    """Cancel a running batch sync job."""
    if not state.batch_sync_service:
        raise HTTPException(status_code=503, detail="Batch sync service not available")

    cancelled = await state.batch_sync_service.cancel_job(job_id)
    if cancelled:
        return {"status": "cancelled", "job_id": job_id}
    else:
        return {"status": "not_running", "job_id": job_id}


@router.delete("/sync/batch/jobs")
async def clear_completed_jobs() -> dict[str, Any]:
    """Clear all completed/failed/cancelled jobs from memory."""
    if not state.batch_sync_service:
        raise HTTPException(status_code=503, detail="Batch sync service not available")

    count = state.batch_sync_service.clear_completed_jobs()
    return {"cleared": count}


# =============================================================================
# INTEGRITY CHECK ENDPOINTS
# =============================================================================


class IntegrityIssue(BaseModel):
    """A single referential integrity issue."""

    type: str
    severity: str
    source: str = Field(..., description="Source service (template-store or document-store)")
    entity_id: str = Field(..., description="ID of the entity with the issue")
    entity_value: str | None = Field(None, description="Value of the entity (if applicable)")
    field_path: str | None = Field(None, description="Field path")
    reference: str
    message: str


class IntegritySummary(BaseModel):
    """Summary of aggregated integrity check results."""

    total_templates: int = 0
    total_documents: int = 0
    documents_checked: int = 0
    templates_with_issues: int = 0
    documents_with_issues: int = 0
    orphaned_terminology_refs: int = 0
    orphaned_template_refs: int = 0
    orphaned_term_refs: int = 0
    inactive_refs: int = 0


class AggregatedIntegrityResult(BaseModel):
    """Aggregated integrity check result from all services."""

    status: str = Field(
        ...,
        description="Overall status: healthy, warning, error, partial (if some services unreachable)"
    )
    checked_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    services_checked: list[str] = Field(default_factory=list)
    services_unavailable: list[str] = Field(default_factory=list)
    summary: IntegritySummary = Field(default_factory=IntegritySummary)
    issues: list[IntegrityIssue] = Field(default_factory=list)


@router.get("/health/integrity", response_model=AggregatedIntegrityResult)
async def aggregated_integrity_check(
    template_status: str | None = None,
    document_status: str | None = None,
    template_limit: int = 0,
    document_limit: int = 0,
    check_term_refs: bool = True,
    recent_first: bool = False,
) -> AggregatedIntegrityResult:
    """
    Aggregated referential integrity check across all services.

    Calls Template Store and Document Store integrity endpoints and combines results.

    This endpoint provides a unified view of data quality issues:
    - Orphaned terminology references (templates referencing missing terminologies)
    - Orphaned template references (documents referencing missing templates)
    - Orphaned term references (documents with term_references to missing terms)
    - Inactive references (references to deprecated/inactive entities)

    Args:
        template_status: Filter templates by status ('active', 'deprecated', 'inactive')
        document_status: Filter documents by status ('active', 'inactive', 'archived')
        template_limit: Maximum templates to check (default 1000)
        document_limit: Maximum documents to check (default 1000)
        check_term_refs: Whether to check term references in documents (default true)

    Returns:
        Aggregated integrity check results from all services
    """
    services_checked = []
    services_unavailable = []
    all_issues: list[IntegrityIssue] = []
    summary = IntegritySummary()

    # Check Template Store
    template_store_url = settings.template_store_url
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            params = {"limit": template_limit}
            if template_status:
                params["status"] = template_status

            response = await client.get(
                f"{template_store_url}/health/integrity",
                params=params,
                headers={"X-API-Key": settings.api_key},
            )

            if response.status_code == 200:
                data = response.json()
                services_checked.append("template-store")

                # Extract summary
                ts_summary = data.get("summary", {})
                summary.total_templates = ts_summary.get("total_templates", 0)
                summary.templates_with_issues = ts_summary.get("templates_with_issues", 0)
                summary.orphaned_terminology_refs = ts_summary.get("orphaned_terminology_refs", 0)
                summary.inactive_refs += ts_summary.get("inactive_terminology_refs", 0)
                summary.inactive_refs += ts_summary.get("inactive_template_refs", 0)

                # Convert issues
                for issue in data.get("issues", []):
                    all_issues.append(IntegrityIssue(
                        type=issue.get("type"),
                        severity=issue.get("severity", "warning"),
                        source="template-store",
                        entity_id=issue.get("template_id"),
                        entity_value=issue.get("template_value"),
                        field_path=issue.get("field_path"),
                        reference=issue.get("reference"),
                        message=issue.get("message"),
                    ))
            else:
                logger.warning(f"Template Store integrity check failed: {response.status_code}")
                services_unavailable.append("template-store")
    except Exception as e:
        logger.error(f"Failed to reach Template Store for integrity check: {e}")
        services_unavailable.append("template-store")

    # Check Document Store
    # Timeout scales with limit: ~2min for 10k, ~10min for 50k, 30min for "all" (0)
    doc_timeout = 1800.0 if document_limit == 0 else max(120.0, document_limit * 0.012)
    document_store_url = settings.document_store_url
    try:
        async with httpx.AsyncClient(timeout=doc_timeout) as client:
            params = {"limit": document_limit, "check_term_refs": check_term_refs, "recent_first": recent_first}
            if document_status:
                params["status"] = document_status

            response = await client.get(
                f"{document_store_url}/health/integrity",
                params=params,
                headers={"X-API-Key": settings.api_key},
            )

            if response.status_code == 200:
                data = response.json()
                services_checked.append("document-store")

                # Extract summary
                ds_summary = data.get("summary", {})
                summary.total_documents = ds_summary.get("total_documents", 0)
                summary.documents_checked = ds_summary.get("documents_checked", 0)
                summary.documents_with_issues = ds_summary.get("documents_with_issues", 0)
                summary.orphaned_template_refs = ds_summary.get("orphaned_template_refs", 0)
                summary.orphaned_term_refs = ds_summary.get("orphaned_term_refs", 0)
                summary.inactive_refs += ds_summary.get("inactive_template_refs", 0)

                # Convert issues
                for issue in data.get("issues", []):
                    all_issues.append(IntegrityIssue(
                        type=issue.get("type"),
                        severity=issue.get("severity", "warning"),
                        source="document-store",
                        entity_id=issue.get("document_id"),
                        entity_value=None,
                        field_path=issue.get("field_path"),
                        reference=issue.get("reference"),
                        message=issue.get("message"),
                    ))
            else:
                logger.warning(f"Document Store integrity check failed: {response.status_code}")
                services_unavailable.append("document-store")
    except Exception as e:
        logger.error(f"Failed to reach Document Store for integrity check: {e}")
        services_unavailable.append("document-store")

    # Determine overall status
    has_errors = any(i.severity == "error" for i in all_issues)
    has_warnings = any(i.severity == "warning" for i in all_issues)

    if services_unavailable:
        status = "partial" if services_checked else "error"
    elif has_errors:
        status = "error"
    elif has_warnings:
        status = "warning"
    else:
        status = "healthy"

    return AggregatedIntegrityResult(
        status=status,
        services_checked=services_checked,
        services_unavailable=services_unavailable,
        summary=summary,
        issues=all_issues,
    )


# =============================================================================
# SEARCH & ACTIVITY ENDPOINTS
# =============================================================================


@router.post("/search", response_model=SearchResponse)
async def unified_search(request: SearchRequest) -> SearchResponse:
    """
    Unified search across all WIP entity types.

    Searches terminologies, terms, templates, and documents in parallel.
    Results are sorted by relevance (exact matches first).

    Args:
        request: Search parameters
            - query: Search string (required)
            - types: Entity types to search (optional, defaults to all)
            - status: Filter by status (optional)
            - limit: Max results per type (1-200, default 50)

    Returns:
        SearchResponse with results grouped by type and total counts
    """
    if not state.search_service:
        raise HTTPException(status_code=503, detail="Search service not available")

    return await state.search_service.search(request)


@router.get("/activity/recent", response_model=ActivityResponse)
async def get_recent_activity(
    types: str | None = None,
    limit: int = 50
) -> ActivityResponse:
    """
    Get recent activity across all entity types.

    Returns a timeline of recent changes (creates, updates, deletes)
    aggregated from all WIP services.

    Args:
        types: Comma-separated list of entity types (optional)
               Valid types: terminology, term, template, document
        limit: Maximum number of activities to return (1-200, default 50)

    Returns:
        ActivityResponse with list of activities sorted by timestamp (newest first)
    """
    if not state.search_service:
        raise HTTPException(status_code=503, detail="Search service not available")

    type_list = types.split(",") if types else None
    if limit < 1 or limit > 200:
        limit = 50

    return await state.search_service.get_recent_activity(types=type_list, limit=limit)


@router.get("/references/term/{term_id}/documents", response_model=TermDocumentsResponse)
async def get_term_documents(
    term_id: str,
    limit: int = 100
) -> TermDocumentsResponse:
    """
    Get documents that reference a specific term.

    Searches the PostgreSQL reporting database for documents with
    term_references containing the given term_id.

    Args:
        term_id: Term ID to search for (UUID or value code, e.g., 019abc42-...)
        limit: Maximum number of documents to return (1-1000, default 100)

    Returns:
        TermDocumentsResponse with list of referencing documents
    """
    if not state.search_service:
        raise HTTPException(status_code=503, detail="Search service not available")

    if limit < 1 or limit > 1000:
        limit = 100

    return await state.search_service.get_term_documents(term_id=term_id, limit=limit)


@router.get("/entity/{entity_type}/{entity_id}/references", response_model=EntityReferencesResponse)
async def get_entity_references(
    entity_type: str,
    entity_id: str
) -> EntityReferencesResponse:
    """
    Get an entity's details and validate all its references.

    Returns the entity with a list of all outgoing references and their
    validation status (valid, broken, or inactive).

    Args:
        entity_type: Entity type (document, template, terminology, term)
        entity_id: Entity ID

    Returns:
        EntityReferencesResponse with entity details and reference validation
    """
    if not state.search_service:
        raise HTTPException(status_code=503, detail="Search service not available")

    valid_types = ["document", "template", "terminology", "term"]
    if entity_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid entity type: {entity_type}. Must be one of: {valid_types}"
        )

    return await state.search_service.get_entity_references(
        entity_type=entity_type,
        entity_id=entity_id
    )


@router.get("/entity/{entity_type}/{entity_id}/referenced-by", response_model=ReferencedByResponse)
async def get_referenced_by(
    entity_type: str,
    entity_id: str,
    limit: int = Query(100, ge=1, le=500, description="Max results to return")
) -> ReferencedByResponse:
    """
    Find all entities that reference the given entity.

    Returns a list of entities (documents, templates) that have references
    pointing to the target entity.

    - **Template**: documents using it, templates extending it, templates with template_ref
    - **Terminology**: templates with terminology_ref
    - **Term**: documents with term_references
    - **Document**: (not referenced by other entities)

    Args:
        entity_type: Entity type (document, template, terminology, term)
        entity_id: Entity ID
        limit: Max results to return (default 100, max 500)

    Returns:
        ReferencedByResponse with list of referencing entities
    """
    if not state.search_service:
        raise HTTPException(status_code=503, detail="Search service not available")

    valid_types = ["document", "template", "terminology", "term"]
    if entity_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid entity type: {entity_type}. Must be one of: {valid_types}"
        )

    return await state.search_service.get_referenced_by(
        entity_type=entity_type,
        entity_id=entity_id,
        limit=limit
    )


# =============================================================================
# QUERY ENDPOINTS (for cross-template joins)
# =============================================================================


@router.get("/tables")
async def list_tables(
    table_name: str | None = Query(default=None, description="Return full column detail for a specific table"),
):
    """List available reporting tables.

    Without table_name: returns table names, row counts, and column counts (summary).
    With table_name: returns full column detail (name, type, nullable) for that table.
    """
    if not state.postgres_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not connected")

    allowed_prefixes = ("doc_",)
    allowed_exact = {"terminologies", "terms", "term_relationships"}

    async with state.postgres_pool.acquire() as conn:
        # Get all base tables in public schema
        raw_tables = await conn.fetch(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """
        )

        tables = []
        for row in raw_tables:
            tname = row["table_name"]
            if not (tname.startswith(allowed_prefixes) or tname in allowed_exact):
                continue

            # If filtering by table_name, skip non-matching tables
            if table_name and tname != table_name:
                continue

            # Get columns
            columns = await conn.fetch(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = $1
                ORDER BY ordinal_position
                """,
                tname,
            )

            # Get row count
            count = await conn.fetchval(
                f'SELECT COUNT(*) FROM "{tname}"'
            )

            entry: dict = {
                "name": tname,
                "row_count": count,
            }

            if table_name:
                # Detail mode: include full column info
                entry["columns"] = [
                    {
                        "name": c["column_name"],
                        "type": c["data_type"],
                        "nullable": c["is_nullable"] == "YES",
                    }
                    for c in columns
                ]
            else:
                # Summary mode: just column count
                entry["column_count"] = len(columns)

            tables.append(entry)

        if table_name and not tables:
            raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found")

    return {"tables": tables}


class ReportQuery(BaseModel):
    """Request model for ad-hoc reporting queries."""

    sql: str
    params: list[Any] = []
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    max_rows: int = Field(default=1000, ge=1, le=50000)


# Pattern matching dangerous SQL keywords at word boundaries
_DANGEROUS_SQL_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


@router.post("/query")
async def execute_query(body: ReportQuery):
    """Execute a read-only SQL query against the reporting database."""
    if not state.postgres_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not connected")

    # Safety: reject write/DDL statements
    if _DANGEROUS_SQL_RE.search(body.sql):
        raise HTTPException(
            status_code=400,
            detail="Only read-only queries are allowed. "
            "INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE, GRANT, and REVOKE are prohibited.",
        )

    # Build wrapped query with row limit
    wrapped_sql = f"SELECT * FROM ({body.sql}) _q LIMIT {body.max_rows + 1}"

    # Build positional parameter references
    params = body.params

    try:
        async with state.postgres_pool.acquire() as conn:
            # Set statement timeout and read-only transaction
            await conn.execute(
                f"SET statement_timeout = {body.timeout_seconds * 1000}"
            )
            await conn.execute("SET default_transaction_read_only = on")

            rows = await conn.fetch(wrapped_sql, *params)

            # Detect truncation
            truncated = len(rows) > body.max_rows
            if truncated:
                rows = rows[: body.max_rows]

            # Extract column names from first row (or empty)
            columns = list(rows[0].keys()) if rows else []

            return {
                "columns": columns,
                "rows": [dict(r) for r in rows],
                "row_count": len(rows),
                "truncated": truncated,
            }

    except asyncpg.QueryCanceledError as e:
        raise HTTPException(
            status_code=408,
            detail=f"Query timed out after {body.timeout_seconds} seconds",
        ) from e
    except asyncpg.PostgresSyntaxError as e:
        raise HTTPException(status_code=400, detail=f"SQL syntax error: {e}") from e
    except asyncpg.PostgresError as e:
        raise HTTPException(status_code=400, detail=f"Query error: {e}") from e


# =============================================================================
# CSV EXPORT
# =============================================================================

# Allowed tables for CSV export (same whitelist as /tables)
_EXPORT_ALLOWED_PREFIXES = ("doc_",)
_EXPORT_ALLOWED_EXACT = {"terminologies", "terms", "term_relationships"}


def _is_allowed_table(name: str) -> bool:
    return name.startswith(_EXPORT_ALLOWED_PREFIXES) or name in _EXPORT_ALLOWED_EXACT


class CsvExportQuery(BaseModel):
    """Request model for query-based CSV export."""

    sql: str
    params: list[Any] = []
    timeout_seconds: int = Field(default=120, ge=1, le=600)
    filename: str = Field(default="export.csv", pattern=r"^[\w\-. ]+\.csv$")


async def _stream_csv(conn: asyncpg.Connection, sql: str, params: list[Any]):
    """Yield CSV rows from a PostgreSQL query using a cursor."""
    stmt = await conn.prepare(sql)
    columns = [attr.name for attr in stmt.get_attributes()]

    # Header row
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    yield buf.getvalue()

    # Data rows — fetch in batches to limit memory
    async with conn.transaction():
        async for record in stmt.cursor(*params):
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow([record[col] for col in columns])
            yield buf.getvalue()


@router.get("/export/csv")
async def export_table_csv(
    table: str = Query(description="Table name to export (e.g. doc_patient)"),
    timeout_seconds: int = Query(default=120, ge=1, le=600),
    filename: str | None = Query(default=None, description="Download filename"),
):
    """Export a full reporting table as streaming CSV."""
    if not state.postgres_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not connected")

    if not _is_allowed_table(table):
        raise HTTPException(
            status_code=400,
            detail=f"Table '{table}' is not available for export. "
            "Only doc_* tables and metadata tables (terminologies, terms, term_relationships) are allowed.",
        )

    download_name = filename or f"{table}.csv"

    async def generate():
        async with state.postgres_pool.acquire() as conn:
            await conn.execute(f"SET statement_timeout = {timeout_seconds * 1000}")
            await conn.execute("SET default_transaction_read_only = on")
            async for chunk in _stream_csv(conn, f'SELECT * FROM "{table}"', []):
                yield chunk

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{download_name}"'},
    )


@router.post("/export/csv")
async def export_query_csv(body: CsvExportQuery):
    """Export a SQL query result as streaming CSV."""
    if not state.postgres_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not connected")

    if _DANGEROUS_SQL_RE.search(body.sql):
        raise HTTPException(
            status_code=400,
            detail="Only read-only queries are allowed. "
            "INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE, GRANT, and REVOKE are prohibited.",
        )

    async def generate():
        async with state.postgres_pool.acquire() as conn:
            await conn.execute(
                f"SET statement_timeout = {body.timeout_seconds * 1000}"
            )
            await conn.execute("SET default_transaction_read_only = on")
            async for chunk in _stream_csv(conn, body.sql, body.params):
                yield chunk

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{body.filename}"'},
    )


# =============================================================================
# NAMESPACE DELETION
# =============================================================================


@router.delete("/namespace/{prefix}")
async def delete_namespace(prefix: str):
    """Delete all reporting data for a namespace.

    Removes rows from all doc_* tables, metadata tables (terminologies, terms,
    term_relationships, templates), and sync status where namespace matches.

    Called by Registry during namespace deletion.
    """
    if not state.postgres_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not connected")

    if not prefix or not prefix.strip():
        raise HTTPException(status_code=400, detail="Namespace prefix is required")

    total_deleted = 0

    async with state.postgres_pool.acquire() as conn:
        # Find all doc_* tables
        doc_tables = await conn.fetch(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_type = 'BASE TABLE'
              AND table_name LIKE 'doc_%'
            ORDER BY table_name
            """
        )

        # Delete from each doc_* table
        for row in doc_tables:
            table_name = row["table_name"]
            result = await conn.execute(
                f'DELETE FROM "{table_name}" WHERE namespace = $1', prefix
            )
            count = int(result.split()[-1])  # "DELETE N"
            if count > 0:
                logger.info(
                    f"Deleted {count} rows from {table_name} for namespace {prefix}"
                )
            total_deleted += count

        # Delete from metadata tables
        for table_name in ("terminologies", "templates", "terms", "term_relationships"):
            try:
                result = await conn.execute(
                    f'DELETE FROM "{table_name}" WHERE namespace = $1', prefix
                )
                count = int(result.split()[-1])
                if count > 0:
                    logger.info(
                        f"Deleted {count} rows from {table_name} for namespace {prefix}"
                    )
                total_deleted += count
            except asyncpg.UndefinedTableError:
                pass  # Table doesn't exist yet — nothing to clean

    logger.info(
        f"Namespace {prefix} cleanup complete: {total_deleted} total rows deleted"
    )
    return {"total_deleted": total_deleted}


@router.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": settings.service_name,
        "version": __version__,
        "docs": "/api/reporting-sync/docs",
        "health": "/api/reporting-sync/health",
        "status": "/api/reporting-sync/status",
        "integrity": "/api/reporting-sync/health/integrity",
        "search": "/api/reporting-sync/search",
        "activity": "/api/reporting-sync/activity/recent",
    }


app.include_router(router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "reporting_sync.main:app",
        host="0.0.0.0",
        port=8005,
        reload=settings.debug,
    )
