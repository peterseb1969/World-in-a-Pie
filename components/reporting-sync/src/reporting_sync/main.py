"""
WIP Reporting Sync Service

FastAPI application providing health endpoints and sync management.
The actual sync work is done by the worker module.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import nats
from fastapi import FastAPI, HTTPException
from nats.js import JetStreamContext

from . import __version__
from .config import settings
from .models import (
    HealthResponse,
    SyncStatus,
    BatchSyncJob,
    BatchSyncRequest,
    BatchSyncResponse,
    BatchSyncStatus,
)
from .worker import run_sync_worker
from .batch_sync import BatchSyncService

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
    batch_sync_service: BatchSyncService | None = None
    sync_status: SyncStatus = SyncStatus(
        running=False,
        connected_to_nats=False,
        connected_to_postgres=False,
    )


state = AppState()


async def connect_nats() -> tuple[nats.NATS, JetStreamContext]:
    """Connect to NATS and get JetStream context."""
    logger.info(f"Connecting to NATS at {settings.nats_url}...")
    nc = await nats.connect(settings.nats_url)
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
                template_code TEXT NOT NULL,
                template_version INTEGER NOT NULL,
                migration_sql TEXT NOT NULL,
                applied_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                PRIMARY KEY (template_code, template_version)
            )
        """)

        # Create sync status tracking table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS _wip_sync_status (
                template_code TEXT PRIMARY KEY,
                last_sync_at TIMESTAMP WITH TIME ZONE,
                documents_synced BIGINT DEFAULT 0,
                last_error TEXT,
                last_error_at TIMESTAMP WITH TIME ZONE
            )
        """)

        logger.info("PostgreSQL schema initialized")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - startup and shutdown."""
    # Startup
    logger.info(f"Starting {settings.service_name} v{__version__}...")

    try:
        # Connect to NATS
        state.nats_client, state.jetstream = await connect_nats()
        state.sync_status.connected_to_nats = True
    except Exception as e:
        logger.error(f"Failed to connect to NATS: {e}")
        state.sync_status.connected_to_nats = False

    try:
        # Connect to PostgreSQL
        state.postgres_pool = await connect_postgres()
        state.sync_status.connected_to_postgres = True

        # Initialize schema
        await init_postgres_schema(state.postgres_pool)
    except Exception as e:
        logger.error(f"Failed to connect to PostgreSQL: {e}")
        state.sync_status.connected_to_postgres = False

    # Initialize batch sync service
    if state.postgres_pool:
        state.batch_sync_service = BatchSyncService(state.postgres_pool)
        logger.info("Batch sync service initialized")

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

    logger.info(f"{settings.service_name} started successfully")

    yield

    # Shutdown
    logger.info(f"Shutting down {settings.service_name}...")

    # Cancel sync task
    if state.sync_task:
        state.sync_task.cancel()
        try:
            await state.sync_task
        except asyncio.CancelledError:
            pass

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


@app.get("/status", response_model=SyncStatus)
async def get_sync_status() -> SyncStatus:
    """Get current sync worker status."""
    return state.sync_status


@app.get("/schema/{template_code}")
async def get_schema(template_code: str) -> dict[str, Any]:
    """Get the PostgreSQL schema for a template."""
    if not state.postgres_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not connected")

    table_name = f"doc_{template_code.lower()}"

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
            "template_code": template_code,
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


@app.post("/sync/batch/{template_code}", response_model=BatchSyncResponse)
async def trigger_batch_sync(
    template_code: str,
    force: bool = False,
    page_size: int = 100,
) -> BatchSyncResponse:
    """
    Trigger a batch sync for a specific template.

    This fetches all documents for the template from Document Store
    and syncs them to PostgreSQL.

    Args:
        template_code: Template code to sync
        force: Force re-sync even if table already has data
        page_size: Number of documents to fetch per page (10-1000)
    """
    if not state.batch_sync_service:
        raise HTTPException(status_code=503, detail="Batch sync service not available")

    if page_size < 10 or page_size > 1000:
        raise HTTPException(status_code=400, detail="page_size must be between 10 and 1000")

    job = await state.batch_sync_service.start_batch_sync(
        template_code=template_code,
        force=force,
        page_size=page_size,
    )

    return BatchSyncResponse(
        job_id=job.job_id,
        template_code=job.template_code,
        status=job.status,
        message=f"Batch sync started for {template_code}",
    )


@app.post("/sync/batch", response_model=list[BatchSyncResponse])
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
            template_code=job.template_code,
            status=job.status,
            message=f"Batch sync started for {job.template_code}",
        )
        for job in jobs
    ]


@app.get("/sync/batch/jobs", response_model=list[BatchSyncJob])
async def list_batch_jobs() -> list[BatchSyncJob]:
    """List all batch sync jobs."""
    if not state.batch_sync_service:
        raise HTTPException(status_code=503, detail="Batch sync service not available")

    return state.batch_sync_service.list_jobs()


@app.get("/sync/batch/jobs/{job_id}", response_model=BatchSyncJob)
async def get_batch_job(job_id: str) -> BatchSyncJob:
    """Get a specific batch sync job by ID."""
    if not state.batch_sync_service:
        raise HTTPException(status_code=503, detail="Batch sync service not available")

    job = state.batch_sync_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return job


@app.delete("/sync/batch/jobs/{job_id}")
async def cancel_batch_job(job_id: str) -> dict[str, Any]:
    """Cancel a running batch sync job."""
    if not state.batch_sync_service:
        raise HTTPException(status_code=503, detail="Batch sync service not available")

    cancelled = await state.batch_sync_service.cancel_job(job_id)
    if cancelled:
        return {"status": "cancelled", "job_id": job_id}
    else:
        return {"status": "not_running", "job_id": job_id}


@app.delete("/sync/batch/jobs")
async def clear_completed_jobs() -> dict[str, Any]:
    """Clear all completed/failed/cancelled jobs from memory."""
    if not state.batch_sync_service:
        raise HTTPException(status_code=503, detail="Batch sync service not available")

    count = state.batch_sync_service.clear_completed_jobs()
    return {"cleared": count}


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": settings.service_name,
        "version": __version__,
        "docs": "/docs",
        "health": "/health",
        "status": "/status",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "reporting_sync.main:app",
        host="0.0.0.0",
        port=8005,
        reload=settings.debug,
    )
