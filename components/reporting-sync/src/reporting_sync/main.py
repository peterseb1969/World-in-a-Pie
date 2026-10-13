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
from typing import Any, Literal

import asyncpg
import httpx
import nats
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from nats.js import JetStreamContext
from pydantic import BaseModel, Field

from wip_auth import (
    UserIdentity,
    build_metadata,
    check_namespace_permission,
    declare_api_key_security,
    get_identity_string,
    require_api_key,
    require_groups,
    resolve_accessible_namespaces,
    setup_auth,
    setup_key_sync,
)
from wip_auth.ratelimit import setup_rate_limiting
from wip_auth.security import check_production_security
from wip_auth.startup import retry_async

from . import __version__
from .batch_sync import BatchSyncService
from .bookkeeping import repair_bookkeeping_location
from .config import settings
from .metrics import metrics
from .models import (
    AlertConfig,
    AlertsResponse,
    BatchSyncJob,
    BatchSyncResponse,
    BatchSyncStatus,
    ConsumerInfo,
    HealthResponse,
    MetricsResponse,
    StrictModel,
    SyncStatus,
)
from .parity import NamespaceParityResult, check_namespace_parity
from .pg_roles import RoleSpec, is_privileged, provision_roles
from .schema_manager import SchemaManager
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
    # Owner pool: the sync pipeline, DDL and bookkeeping. Reader pool:
    # caller-driven SQL (raw query, exports, view validation, search).
    postgres_pool: asyncpg.Pool | None = None
    read_pool: asyncpg.Pool | None = None
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
                    assert state.postgres_pool is not None  # narrowed by postgres_ok check above
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
                "wip.term_relations.>",
            ],
            retention="limits",
            max_msgs=1_000_000,
            max_bytes=1024 * 1024 * 1024,  # 1GB
        )
        logger.info(f"Stream {settings.nats_stream_name} created")

    logger.info("Connected to NATS with JetStream")
    return nc, js


class ServiceRolesError(RuntimeError):
    """The service roles are missing, unprovisionable or privileged.

    Never retried and never worked around by falling back to the bootstrap
    superuser: the service stops serving PostgreSQL and reports unhealthy.
    """


async def connect_postgres() -> tuple[asyncpg.Pool, asyncpg.Pool]:
    """Provision the service roles, then open the owner and reader pools.

    The bootstrap login (POSTGRES_USER) is used on one connection that is
    closed before any pool opens: it repairs the bookkeeping location (C7),
    then creates or normalises the two service roles (pg_roles.py). A
    bootstrap login without superuser rights cannot provision; the roles
    must then already exist. Both pools are checked to be unprivileged.
    """
    if not (settings.postgres_owner_password and settings.postgres_read_password):
        raise ServiceRolesError(
            "POSTGRES_SYNC_OWNER_PASSWORD / POSTGRES_READ_PASSWORD are not set — "
            "re-render the install so the deployer generates them"
        )
    endpoint = {
        "host": settings.postgres_host,
        "port": settings.postgres_port,
        "database": settings.postgres_db,
    }
    logger.info(
        f"Connecting to PostgreSQL at {settings.postgres_host}:{settings.postgres_port}..."
    )

    bootstrap = await asyncpg.connect(
        **endpoint, user=settings.postgres_user, password=settings.postgres_password
    )
    try:
        await repair_bookkeeping_location(bootstrap)
        if await bootstrap.fetchval(
            "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
        ):
            await provision_roles(bootstrap, RoleSpec(
                owner=settings.postgres_owner_user,
                owner_password=settings.postgres_owner_password,
                reader=settings.postgres_read_user,
                reader_password=settings.postgres_read_password,
            ))
        else:
            logger.warning(
                "Bootstrap login %s is not a superuser: skipping role "
                "provisioning; %s and %s must already exist",
                settings.postgres_user,
                settings.postgres_owner_user,
                settings.postgres_read_user,
            )
    finally:
        await bootstrap.close()

    owner = await asyncpg.create_pool(
        **endpoint,
        user=settings.postgres_owner_user,
        password=settings.postgres_owner_password,
        min_size=settings.postgres_pool_min,
        max_size=settings.postgres_pool_max,
    )
    try:
        reader = await asyncpg.create_pool(
            **endpoint,
            user=settings.postgres_read_user,
            password=settings.postgres_read_password,
            min_size=1,
            max_size=settings.postgres_read_pool_max,
        )
    except BaseException:
        await owner.close()
        raise
    for label, pool in (("owner", owner), ("reader", reader)):
        async with pool.acquire() as conn:
            privileged = await is_privileged(conn)
        if privileged:
            await owner.close()
            await reader.close()
            raise ServiceRolesError(
                f"the {label} pool's role is a superuser or can create roles; "
                "refusing to serve requests with it"
            )
    logger.info(
        "Connected to PostgreSQL as %s (owner) and %s (reader)",
        settings.postgres_owner_user, settings.postgres_read_user,
    )
    return owner, reader


async def init_postgres_schema(pool: asyncpg.Pool) -> None:
    """Initialize PostgreSQL schema (migration tracking table, etc.).

    Every bookkeeping table is schema-qualified with ``public``: an
    unqualified name resolves through search_path ("$user", public), and a
    namespace schema named like the login role would capture it (see
    bookkeeping.py, which moves an existing install's copies back first).
    """
    async with pool.acquire() as conn:
        # Create schema migrations tracking table. Keyed on
        # (namespace, template_value, version): template_value is unique only
        # per namespace (CASE-628), so the namespace is part of the key.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS public._wip_schema_migrations (
                namespace VARCHAR(255) NOT NULL DEFAULT 'wip',
                template_value TEXT NOT NULL,
                template_version INTEGER NOT NULL,
                migration_sql TEXT NOT NULL,
                applied_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                PRIMARY KEY (namespace, template_value, template_version)
            )
        """)

        # Create sync status tracking table. Keyed on (namespace,
        # template_value) — template_value is unique only per namespace
        # (CASE-628).
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS public._wip_sync_status (
                namespace VARCHAR(255) NOT NULL DEFAULT 'wip',
                template_value TEXT NOT NULL,
                last_sync_at TIMESTAMP WITH TIME ZONE,
                documents_synced BIGINT DEFAULT 0,
                last_error TEXT,
                last_error_at TIMESTAMP WITH TIME ZONE,
                PRIMARY KEY (namespace, template_value)
            )
        """)

        # Registered views bookkeeping. Apps register named SQL views at
        # bootstrap; reporting-sync recreates them best-effort at startup
        # and after entity-view rebuilds. After a full schema reset the
        # authoritative recovery is the app's bootstrap re-registration.
        # Keyed on (namespace, view_name) — view names are unique per
        # namespace schema.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS public._wip_views (
                namespace VARCHAR(255) NOT NULL,
                view_name TEXT NOT NULL,
                view_sql TEXT NOT NULL,
                registered_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                registered_by TEXT,
                PRIMARY KEY (namespace, view_name)
            )
        """)

        logger.info("PostgreSQL schema initialized")


async def _recreate_registered_views(pool: asyncpg.Pool) -> None:
    """Best-effort recreation of registered views at startup.

    Views registered via POST /namespace/{ns}/views persist in _wip_views.
    On a plain restart the views still exist in PostgreSQL and this pass is
    a cheap no-op; after a schema reset it recreates what can be recreated
    NOW. A view whose source tables have not been re-synced yet fails, is
    logged, and comes back after the next reporting rebuild touches its
    namespace — or via the app's bootstrap re-registration, which remains
    the authoritative recovery after a full reset. Never blocks startup.
    """
    sm = SchemaManager(pool)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT namespace FROM public._wip_views ORDER BY namespace"
        )

    present = 0
    deferred = 0
    for row in rows:
        namespace = row["namespace"]
        try:
            await sm.ensure_schema(namespace)
            ok, failed = await sm.recreate_registered_views(namespace)
        except Exception as e:
            logger.warning(
                f"Registered-view recreation failed for namespace {namespace!r}: {e}"
            )
            continue
        present += ok
        deferred += failed

    if present or deferred:
        logger.info(
            f"Registered views at startup: {present} present/recreated, "
            f"{deferred} deferred until their tables re-sync"
        )


async def _initial_metadata_sync(batch_sync_service: BatchSyncService) -> None:
    """Background: wait for def-store to be healthy, then batch-sync metadata.

    The batch syncs (`batch_sync_terminologies`, `batch_sync_terms`,
    `batch_sync_term_relations`) swallow connection errors internally and
    return empty results — a single attempt against a not-yet-ready
    def-store silently yields 0-row tables. Polling def-store's /health
    first and only then calling the batch syncs makes them succeed on the
    first attempt rather than silently fail.
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
            "Initial metadata sync skipped; trigger "
            "POST /api/reporting-sync/sync/batch/terminologies?namespace=<ns>, "
            "POST /api/reporting-sync/sync/batch/terms?namespace=<ns>, "
            "POST /api/reporting-sync/sync/batch/term_relations?namespace=<ns>, and "
            "POST /api/reporting-sync/sync/batch/templates?namespace=<ns> "
            "manually once the services are reachable."
        )
        return

    try:
        t_result = await batch_sync_service.batch_sync_terminologies()
        logger.info(f"Initial terminology sync: {t_result}")
        t_result = await batch_sync_service.batch_sync_terms()
        logger.info(f"Initial term sync: {t_result}")
        # Term relations must be part of the startup backfill: they only
        # otherwise sync on live NATS events, so a rebuilt reporting database
        # (fresh Postgres + re-sync) would silently lose all historical
        # relations until the next relation write.
        t_result = await batch_sync_service.batch_sync_term_relations()
        logger.info(f"Initial term-relation sync: {t_result}")
        # Templates metadata has the same rebuild-loss shape as term
        # relations: live sync happens only on template events, so a rebuilt
        # reporting database holds no template metadata until each
        # template's next write. Backfill it here too.
        t_result = await batch_sync_service.batch_sync_templates()
        logger.info(f"Initial template metadata sync: {t_result}")
    except Exception as e:
        logger.error(f"Initial metadata sync failed: {e}")


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
        pool, read_pool = await retry_async(
            connect_postgres,
            retry_on=(ConnectionRefusedError, OSError),
            description="PostgreSQL connect",
        )
        state.postgres_pool = pool
        state.read_pool = read_pool
        state.sync_status.connected_to_postgres = True
        state.sync_status.postgres_service_roles_ok = True

        # Initialize schema (bookkeeping tables only). Per-namespace metadata
        # and document tables are created lazily inside each namespace's schema
        # on first sync (CASE-628) — there is no global pre-creation.
        await init_postgres_schema(pool)
        await _recreate_registered_views(pool)
    except ServiceRolesError as e:
        logger.critical(f"PostgreSQL service roles unusable: {e}")
        state.sync_status.connected_to_postgres = False
        state.sync_status.postgres_service_roles_ok = False
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
    state.search_service = SearchService(state.read_pool)
    logger.info("Search service initialized")

    # Start the sync worker task if both connections are up
    if state.sync_status.connected_to_nats and state.sync_status.connected_to_postgres:
        assert state.nats_client is not None  # narrowed by connected_to_nats
        assert state.jetstream is not None  # narrowed by connected_to_nats
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

    # Pick up runtime API keys (created via the Registry) the same way the
    # other services do; REGISTRY_URL / REGISTRY_API_KEY come from the env.
    key_sync = await setup_key_sync(_providers)
    if key_sync:
        logger.info("Key sync started (polling Registry for runtime API keys)")

    logger.info(f"{settings.service_name} started successfully")

    yield

    # Shutdown
    logger.info(f"Shutting down {settings.service_name}...")

    if key_sync:
        await key_sync.stop()

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
    if state.read_pool:
        await state.read_pool.close()
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
    docs_url="/docs",
)

# Authentication (reads WIP_AUTH_* env vars, falls back to API_KEY). The
# middleware only IDENTIFIES the caller; enforcement is the router-level
# require_api_key below. Probes live on the app, outside the router, and the
# prefixed health route is exempted here so a stale or missing key on a
# monitor does not turn into a 401.
_providers = setup_auth(app, public_paths=["/api/reporting-sync/health"])

# Setup rate limiting (reads WIP_RATE_LIMIT, default 40000/minute)
setup_rate_limiting(app)

router = APIRouter(
    prefix="/api/reporting-sync", dependencies=[Depends(require_api_key)]
)

# Operator and raw-SQL routes. wip-services is listed explicitly because it is
# not a superadmin group: service keys hold no namespace grants, so these
# routes are gated by group, never by namespace. Raw SQL (query, query-shaped
# export, registered-view definitions) can reach any schema when it
# schema-qualifies a name, so no namespace check could contain it.
_PRIVILEGED = Depends(require_groups(["wip-admins", "wip-services"]))


async def _effective_namespace(
    identity: UserIdentity, requested: str | None
) -> str | None:
    """The single namespace a listing runs in, or None for "all namespaces".

    reporting-sync reads the stores with its own service key, so a listing
    must be scoped here or it returns every namespace's rows. The rule is
    the platform's multi-namespace-key convention:

    - an explicit namespace must be readable (404 when invisible);
    - a superadmin without one keeps the all-namespaces listing;
    - a key that can read exactly one namespace gets it implicitly;
    - a key that can read several must say which (400), and one that
      can read none is refused (403).
    """
    if requested:
        await check_namespace_permission(identity, requested, "read")
        return requested
    accessible = await resolve_accessible_namespaces(identity)
    if accessible is None:
        return None
    if len(accessible) == 1:
        return accessible[0]
    if not accessible:
        raise HTTPException(status_code=403, detail="No accessible namespaces")
    raise HTTPException(
        status_code=400,
        detail=(
            "namespace is required: this key can read several namespaces "
            f"({', '.join(sorted(accessible))})"
        ),
    )


@app.get("/health", response_model=HealthResponse)
async def health_check(response: Response) -> HealthResponse:
    """Health check endpoint.

    Unusable service roles answer 503, not "degraded" 200: they are a
    deployment fault that container health must surface, not a transient
    outage the service waits out.
    """
    nats_ok = state.nats_client is not None and state.nats_client.is_connected
    postgres_ok = state.postgres_pool is not None

    # Quick postgres check
    if postgres_ok:
        try:
            assert state.postgres_pool is not None  # narrowed by postgres_ok check above
            async with state.postgres_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
        except Exception:
            postgres_ok = False

    if state.sync_status.postgres_service_roles_ok is False:
        status = "unhealthy"
        response.status_code = 503
    elif nats_ok and postgres_ok:
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


# Also expose /health under the api-prefix so external callers through
# the ingress can reach it. Root /health stays for direct container probes.
# Registered on the app, not the router: every router route requires a key.
app.add_api_route(
    "/api/reporting-sync/health",
    health_check,
    methods=["GET"],
    response_model=HealthResponse,
)


@router.get("/status", response_model=SyncStatus)
async def get_sync_status(
    identity: UserIdentity = Depends(require_api_key),
) -> SyncStatus:
    """Get current sync worker status.

    Refreshes the connection flags from live state on every call.
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

    # Any authenticated caller may poll status (@wip/client's awaitSync does),
    # but dropped events name a namespace and an entity: show a caller only
    # the drops in namespaces it can read. None means unrestricted.
    accessible = await resolve_accessible_namespaces(identity)
    if accessible is None:
        return state.sync_status
    visible = set(accessible)
    return state.sync_status.model_copy(update={
        "recent_drops": [
            d for d in state.sync_status.recent_drops if d.namespace in visible
        ],
    })


async def _postgres_live_check() -> bool:
    """Probe the PostgreSQL pool with a 1-second-timeout SELECT 1."""
    if not state.postgres_pool:
        return False
    try:
        assert state.postgres_pool is not None  # narrowed by postgres_ok check above
        async with state.postgres_pool.acquire() as conn:
            await asyncio.wait_for(conn.fetchval("SELECT 1"), timeout=1.0)
        return True
    except Exception:
        return False


@router.get("/parity", response_model=NamespaceParityResult)
async def reporting_parity(
    namespace: str = Query(..., description="Namespace to verify"),
    include_counts: bool = Query(
        True,
        description=(
            "Include expected-vs-actual row counts (mongo-derived, same "
            "query the batch sync consumes). Pass false for the cheap "
            "structure-only form used between restore phases."
        ),
    ),
    identity: UserIdentity = Depends(require_api_key),
) -> NamespaceParityResult:
    """Does postgres reflect what sync should have built for this namespace?

    Per sync-enabled template: table present in the namespace's schema,
    columns matching the schema manager's own derivation, bookkeeping row
    recorded, and (optionally) row-count parity against the active-document
    total. Also reports namespace-level state — schema presence, table
    count, and whether the bookkeeping tables are namespace-keyed at all.

    The one-request answer to "why does the reporting layer look empty":
    a stale/fossil schema shows structural issues, a dead sync worker or
    blocked doc-type sync shows count mismatches, and a pre-namespace-keying
    database is named explicitly with remediation.
    """
    await check_namespace_permission(identity, namespace, "read")
    if not state.postgres_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not connected")
    return await check_namespace_parity(
        state.postgres_pool, namespace, include_counts=include_counts
    )


# =============================================================================
# MONITORING & METRICS ENDPOINTS
# =============================================================================


@router.get("/metrics", dependencies=[_PRIVILEGED], response_model=MetricsResponse)
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
            assert state.postgres_pool is not None  # narrowed by postgres_ok check above
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


@router.get("/metrics/consumer", dependencies=[_PRIVILEGED], response_model=ConsumerInfo | None)
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


@router.get("/alerts", dependencies=[_PRIVILEGED], response_model=AlertsResponse)
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


@router.put("/alerts/config", dependencies=[_PRIVILEGED], response_model=AlertConfig)
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


@router.post("/alerts/test", dependencies=[_PRIVILEGED])
async def test_alerts() -> dict[str, Any]:
    """
    Manually trigger an alert check and return results.

    Useful for testing alert configuration without waiting for the check interval.
    """
    nats_ok = state.nats_client is not None and state.nats_client.is_connected
    postgres_ok = state.postgres_pool is not None

    if postgres_ok:
        try:
            assert state.postgres_pool is not None  # narrowed by postgres_ok check above
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


@router.get("/table-name")
async def resolve_table_name(
    namespace: str,
    template_value: str,
    identity: UserIdentity = Depends(require_api_key),
) -> dict[str, Any]:
    """Resolve the default reporting relation for a (namespace, template_value).

    Post per-version split, the bare name ``doc_<value>`` is the entity
    VIEW (identity-core, or the template's opt-in cross-version view) over
    the physical per-version tables ``doc_<value>__v<N>``. Consumers
    building raw ``run_report_query`` SQL should resolve here rather than
    construct names: query the bare name unless you need one version's
    exact shape — ``version_tables`` lists what physically exists.

    ``kind`` is ``view`` (post-split), ``table`` (a legacy pre-split
    physical table still shadowing the view — remediate via batch sync
    rebuild), or ``absent``. ``exists`` stays: true when the bare name
    resolves to anything queryable.

    (A template's optional ``reporting.table_name`` cosmetic override is not
    reflected here yet — this returns the default ``doc_<value>`` form.)
    """
    await check_namespace_permission(identity, namespace, "read")
    if not state.postgres_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not connected")
    sm = SchemaManager(state.postgres_pool)
    try:
        schema = sm.schema_for(namespace)
        table_name = sm.get_table_name(template_value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    kind = await sm.relation_kind(schema, table_name)
    version_tables = await sm.list_version_tables(namespace, template_value)

    return {
        "namespace": namespace,
        "template_value": template_value,
        "schema": schema,
        "table_name": table_name,
        "qualified_name": sm.qualified_name(namespace, template_value),
        "exists": kind is not None,
        "kind": kind or "absent",
        "version_tables": {v: t for v, t in sorted(version_tables.items())},
        "entities_view": sm.entities_view_name(template_value),
    }


@router.get("/schema/{template_value}")
async def get_schema(
    template_value: str,
    namespace: str,
    version: int | None = None,
    identity: UserIdentity = Depends(require_api_key),
) -> dict[str, Any]:
    """Get the PostgreSQL columns for a template's reporting relation.

    ``namespace`` is required (CASE-628): the relation lives in that
    namespace's schema, and the same template_value can exist in several
    namespaces. Without ``version`` this describes the bare-name entity
    view (the default query surface); with ``version`` it describes that
    version's physical table ``doc_<value>__v<N>``.
    """
    await check_namespace_permission(identity, namespace, "read")
    if not state.postgres_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not connected")

    sm = SchemaManager(state.postgres_pool)
    try:
        schema = sm.schema_for(namespace)
        table_name = sm.get_table_name(template_value, version=version)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    async with state.postgres_pool.acquire() as conn:
        # Check if table exists
        exists = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = $1
                AND table_name = $2
            )
            """,
            schema,
            table_name,
        )

        if not exists:
            raise HTTPException(
                status_code=404,
                detail=f'Table "{schema}"."{table_name}" does not exist',
            )

        # Get column information
        columns = await conn.fetch(
            """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = $1 AND table_name = $2
            ORDER BY ordinal_position
            """,
            schema,
            table_name,
        )

        # Get row count
        row_count = await conn.fetchval(
            f'SELECT COUNT(*) FROM "{schema}"."{table_name}"'
        )

        return {
            "namespace": namespace,
            "template_value": template_value,
            "schema": schema,
            "table_name": table_name,
            "qualified_name": f'"{schema}"."{table_name}"',
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


@router.post("/sync/batch/terminologies", dependencies=[_PRIVILEGED])
async def trigger_terminology_sync(
    namespace: str,
    page_size: int = 100,
) -> dict[str, Any]:
    """
    Batch sync all terminologies from Def-Store to PostgreSQL.

    Args:
        namespace: Namespace to sync (required — no default)
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


@router.post("/sync/batch/terms", dependencies=[_PRIVILEGED])
async def trigger_term_sync(
    namespace: str,
    page_size: int = 100,
) -> dict[str, Any]:
    """
    Batch sync all terms from Def-Store to PostgreSQL.

    Iterates through all terminologies and fetches their terms.

    Args:
        namespace: Namespace to sync (required — no default)
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


@router.post("/sync/batch/term_relations", dependencies=[_PRIVILEGED])
async def trigger_term_relation_sync(
    namespace: str,
    page_size: int = 100,
) -> dict[str, Any]:
    """
    Batch sync all term-relations from Def-Store to PostgreSQL.

    Fetches all active term-relations via the Def-Store ontology API
    and upserts them into the term_relations table.

    Args:
        namespace: Namespace to sync (required — no default)
        page_size: Page size for API fetches (default: 100)
    """
    if not state.batch_sync_service:
        raise HTTPException(status_code=503, detail="Batch sync service not available")

    result = await state.batch_sync_service.batch_sync_term_relations(
        namespace=namespace,
        page_size=page_size,
    )

    return {
        "status": "completed",
        "table": "term_relations",
        **result,
    }


@router.post("/sync/batch/templates", dependencies=[_PRIVILEGED])
async def trigger_template_metadata_sync(
    namespace: str,
    page_size: int = 100,
) -> dict[str, Any]:
    """
    Batch sync all template metadata from Template-Store to PostgreSQL.

    Rebuild/backfill path for the templates metadata table — the live path
    only writes on template events, so a rebuilt reporting database would
    otherwise hold no template metadata until each template's next write.

    Args:
        namespace: Namespace to sync (required — no default)
        page_size: Page size for API fetches (default: 100)
    """
    if not state.batch_sync_service:
        raise HTTPException(status_code=503, detail="Batch sync service not available")

    result = await state.batch_sync_service.batch_sync_templates(
        namespace=namespace,
        page_size=page_size,
    )

    return {
        "status": "completed",
        "table": "templates",
        **result,
    }


def _batch_sync_response(job, force_requested: bool = False) -> BatchSyncResponse:
    """Response line for one job: distinguishes started / already-running /
    failed so a deduplicated trigger is visible to the caller. A force
    trigger that landed on an active job must say so explicitly — silently
    joining the existing (non-dropping) run would make force a conditional
    no-op, which is the false model the flag had before it got semantics."""
    if job.status == BatchSyncStatus.FAILED:
        message = job.error_message or f"Batch sync failed for {job.template_value}"
    elif job.deduplicated:
        if force_requested:
            message = (
                f"Batch sync already active for {job.template_value} "
                f"(job {job.job_id}) — force NOT applied; cancel the job "
                f"and re-trigger"
            )
        else:
            message = (
                f"Batch sync already running for {job.template_value} — "
                f"returning existing job"
            )
    elif force_requested:
        message = (
            f"Force rebuild started for {job.template_value} — existing "
            f"reporting relations are dropped and rebuilt from source"
        )
    else:
        message = f"Batch sync started for {job.template_value}"
    return BatchSyncResponse(
        job_id=job.job_id,
        template_value=job.template_value,
        namespace=job.namespace,
        status=job.status,
        message=message,
    )


@router.post("/sync/batch", dependencies=[_PRIVILEGED], response_model=list[BatchSyncResponse])
async def trigger_batch_sync_all(
    force: bool = False,
    page_size: int = 1000,
    namespace: str | None = None,
) -> list[BatchSyncResponse]:
    """
    Trigger batch sync for all templates.

    This fetches all templates and syncs their documents to PostgreSQL.
    Templates with sync_enabled=false are skipped.

    Args:
        force: Drop each in-scope template's existing reporting relations
            (version tables, entity views, any legacy pre-split table)
            before its sync runs — rebuild from source. Requires an
            explicit namespace. Mid-rebuild, SQL readers see
            relation-does-not-exist for the affected templates until their
            jobs complete. A template with an already-active sync is NOT
            force-rebuilt (its per-item message says so) — cancel the job
            and re-trigger.
        page_size: Page size for document fetches
        namespace: Scope every job to this namespace's documents (the
            template list stays instance-wide — documents may be based on
            templates owned by other namespaces). Omit to sync everything.

    Returns promptly: jobs run in the background; a template whose sync is
    already active gets its existing job back instead of a duplicate.
    """
    if not state.batch_sync_service:
        raise HTTPException(status_code=503, detail="Batch sync service not available")

    if force and namespace is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "force=true requires an explicit namespace: the "
                "drop-and-rebuild is namespace-scoped (reporting table "
                "names derive from the template value, so an instance-wide "
                "drop could destroy same-valued foreign templates' tables)"
            ),
        )

    jobs = await state.batch_sync_service.start_batch_sync_all(
        force=force,
        page_size=page_size,
        namespace=namespace,
    )

    return [_batch_sync_response(job, force_requested=force) for job in jobs]


@router.get("/sync/batch/jobs", dependencies=[_PRIVILEGED], response_model=list[BatchSyncJob])
async def list_batch_jobs() -> list[BatchSyncJob]:
    """List all batch sync jobs."""
    if not state.batch_sync_service:
        raise HTTPException(status_code=503, detail="Batch sync service not available")

    return state.batch_sync_service.list_jobs()


@router.get("/sync/batch/jobs/{job_id}", dependencies=[_PRIVILEGED], response_model=BatchSyncJob)
async def get_batch_job(job_id: str) -> BatchSyncJob:
    """Get a specific batch sync job by ID."""
    if not state.batch_sync_service:
        raise HTTPException(status_code=503, detail="Batch sync service not available")

    job = state.batch_sync_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return job


@router.post("/sync/batch/{template_value}", dependencies=[_PRIVILEGED], response_model=BatchSyncResponse)
async def trigger_batch_sync(
    template_value: str,
    force: bool = False,
    page_size: int = 1000,
    namespace: str | None = None,
) -> BatchSyncResponse:
    """
    Trigger a batch sync for a specific template.

    This fetches all documents for the template from Document Store
    and syncs them to PostgreSQL.

    Args:
        template_value: Template code to sync
        force: Drop the template's existing reporting relations in the
            target namespace (version tables, entity views, any legacy
            pre-split table) before syncing — rebuild from source. This is
            the recovery path for mis-shaped DDL that upserts cannot heal.
            Requires an explicit namespace. Mid-rebuild, SQL readers see
            relation-does-not-exist for this template until the job
            completes.
        page_size: Number of documents to fetch per page (10-1000)
        namespace: Disambiguates the template lookup (a value is unique
            only within a namespace) AND scopes the sync to that
            namespace's documents. Omit for the value's single owner and
            all namespaces' documents.

    If a sync for this template is already active with an overlapping
    scope, the existing job is returned instead of starting a second
    concurrent writer — and force is NOT applied (the response message
    says so). Cancel the job, then re-trigger.
    """
    if not state.batch_sync_service:
        raise HTTPException(status_code=503, detail="Batch sync service not available")

    if page_size < 10 or page_size > 1000:
        raise HTTPException(status_code=400, detail="page_size must be between 10 and 1000")

    if force and namespace is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "force=true requires an explicit namespace: the "
                "drop-and-rebuild is namespace-scoped (reporting table "
                "names derive from the template value, so an instance-wide "
                "drop could destroy same-valued foreign templates' tables)"
            ),
        )

    job = await state.batch_sync_service.start_batch_sync(
        template_value=template_value,
        force=force,
        page_size=page_size,
        namespace=namespace,
    )

    return _batch_sync_response(job, force_requested=force)


@router.delete("/sync/batch/jobs/{job_id}", dependencies=[_PRIVILEGED])
async def cancel_batch_job(job_id: str) -> dict[str, Any]:
    """Cancel a running batch sync job."""
    if not state.batch_sync_service:
        raise HTTPException(status_code=503, detail="Batch sync service not available")

    cancelled = await state.batch_sync_service.cancel_job(job_id)
    if cancelled:
        return {"status": "cancelled", "job_id": job_id}
    else:
        return {"status": "not_running", "job_id": job_id}


@router.delete("/sync/batch/jobs", dependencies=[_PRIVILEGED])
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
    entity_value: str | None = Field(default=None, description="Value of the entity (if applicable)")
    field_path: str | None = Field(default=None, description="Field path")
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


@router.get("/health/integrity", dependencies=[_PRIVILEGED], response_model=AggregatedIntegrityResult)
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
        template_limit: Maximum templates to check (default 0 = unbounded / full scan)
        document_limit: Maximum documents to check (default 0 = unbounded / full scan)
        check_term_refs: Whether to check term references in documents (default true)
        recent_first: Scan the most-recently-updated documents first (default false)

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
            params: dict[str, Any] = {"limit": template_limit}
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
async def unified_search(
    request: SearchRequest,
    namespace: str | None = Query(
        default=None,
        description=(
            "Scope results to this namespace. Accepted as a URL query param "
            "(matching the /sync routes); overrides request.namespace when "
            "both are given. The body field still works."
        ),
    ),
    status: str | None = Query(
        default=None, description="Filter by status. Overrides request.status."
    ),
    template: str | None = Query(
        default=None,
        description="Restrict document search to one template (by value). Overrides request.template.",
    ),
    identity: UserIdentity = Depends(require_api_key),
) -> SearchResponse:
    """Unified search across all WIP entity types with per-type pagination.

    Searches terminologies, terms, templates, documents, and files in
    parallel. Each entity type returns its own pagination envelope
    — see `wip://conventions` for the platform pagination
    contract.

    Args:
        request: Search parameters
            - query: Search string (required)
            - types: Entity types to search (optional, defaults to all)
            - status: Filter by status (optional)
            - page: Page number (default 1)
            - page_size: Items per type (default 50, max 100)
            - limit: DEPRECATED alias for page_size when page=1
        namespace/status/template: optional URL query params — these
            filters previously lived ONLY on the request body, so a
            `?namespace=X` URL param was silently dropped by FastAPI and the
            search ran un-scoped (global, across every namespace's doc_* rows).
            Accepting them as query params (consistent with the /sync routes)
            makes URL-scoped search work; an explicit query param wins over the
            same field in the body (the URL is the per-request override).

    Returns:
        SearchResponse with per-type paginated buckets keyed by entity type.
    """
    overrides: dict[str, Any] = {}
    if namespace is not None:
        overrides["namespace"] = namespace
    if status is not None:
        overrides["status"] = status
    if template is not None:
        overrides["template"] = template
    if overrides:
        request = request.model_copy(update=overrides)

    # Authorize the namespace the search will actually use — the URL
    # override or the body field — and scope an unqualified search.
    effective = await _effective_namespace(identity, request.namespace)
    if effective != request.namespace:
        request = request.model_copy(update={"namespace": effective})

    if not state.search_service:
        raise HTTPException(status_code=503, detail="Search service not available")

    return await state.search_service.search(request)


@router.get("/activity/recent", response_model=ActivityResponse)
async def get_recent_activity(
    types: str | None = None,
    limit: int = 50,
    namespace: str | None = Query(
        default=None, description="Restrict activity to this namespace"
    ),
    identity: UserIdentity = Depends(require_api_key),
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
    effective = await _effective_namespace(identity, namespace)
    if not state.search_service:
        raise HTTPException(status_code=503, detail="Search service not available")

    type_list = types.split(",") if types else None
    if limit < 1 or limit > 200:
        limit = 50

    return await state.search_service.get_recent_activity(
        types=type_list, limit=limit, namespace=effective
    )


@router.get("/references/term/{term_id}/documents", response_model=TermDocumentsResponse)
async def get_term_documents(
    term_id: str,
    limit: int = 100,
    namespace: str | None = Query(
        default=None, description="Restrict to documents in this namespace"
    ),
    identity: UserIdentity = Depends(require_api_key),
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
    effective = await _effective_namespace(identity, namespace)
    if not state.search_service:
        raise HTTPException(status_code=503, detail="Search service not available")

    if limit < 1 or limit > 1000:
        limit = 100

    return await state.search_service.get_term_documents(
        term_id=term_id, limit=limit, namespace=effective
    )


@router.get("/entity/{entity_type}/{entity_id}/references", response_model=EntityReferencesResponse)
async def get_entity_references(
    entity_type: str,
    entity_id: str,
    identity: UserIdentity = Depends(require_api_key),
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

    result = await state.search_service.get_entity_references(
        entity_type=entity_type,
        entity_id=entity_id
    )
    # The entity is fetched with the service key, so authorize on the
    # namespace it actually lives in. A caller without read on it is told
    # the entity is not found; an entity whose namespace is unknown is only
    # shown to unrestricted callers. Its outgoing references need no
    # separate check: the entity's own data already carries the values it
    # submitted for them.
    if result.entity is not None:
        accessible = await resolve_accessible_namespaces(identity)
        if accessible is not None:
            owner = result.entity.namespace
            if owner is None:
                raise HTTPException(status_code=404, detail="Entity not found")
            await check_namespace_permission(identity, owner, "read")
    return result


@router.get("/entity/{entity_type}/{entity_id}/referenced-by", response_model=ReferencedByResponse)
async def get_referenced_by(
    entity_type: str,
    entity_id: str,
    limit: int = Query(100, ge=1, le=500, description="Max results to return"),
    namespace: str | None = Query(
        default=None, description="Restrict to referencing entities in this namespace"
    ),
    identity: UserIdentity = Depends(require_api_key),
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
    effective = await _effective_namespace(identity, namespace)
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
        limit=limit,
        namespace=effective,
    )


# =============================================================================
# QUERY ENDPOINTS (for cross-template joins)
# =============================================================================


# PostgreSQL schemas that are never WIP namespaces.
_SYSTEM_SCHEMAS = ("public", "pg_catalog", "information_schema", "pg_toast")


@router.get("/tables")
async def list_tables(
    namespace: str | None = Query(default=None, description="Restrict to one namespace's schema"),
    table_name: str | None = Query(default=None, description="Return full column detail for a specific (bare) table name"),
    identity: UserIdentity = Depends(require_api_key),
):
    """List reporting relations across namespace schemas — entity-first.

    Each WIP namespace is its own PostgreSQL schema. Post per-version
    split, one template ("entity") owns several relations: the physical
    per-version tables ``doc_<value>__v<N>``, the always-present
    identity-core view ``doc_<value>__entities``, and the bare-name view
    ``doc_<value>`` (the default query surface). The response groups them
    under ``entities`` so agent-written SQL never silently misses sibling
    version tables; ``tables`` keeps the flat relation list (fixed
    metadata tables + every doc relation) for consumers that want it.
    Filter by ``namespace`` and/or bare ``table_name`` (any relation name;
    detail mode returns its columns).
    """
    namespace = await _effective_namespace(identity, namespace)
    if not state.postgres_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not connected")

    allowed_prefixes = ("doc_",)
    allowed_exact = {"terminologies", "terms", "term_relations", "templates"}
    version_re = re.compile(r"^(doc_.+)__v(\d+)$")

    async with state.postgres_pool.acquire() as conn:
        # Tables AND views across every non-system schema (each schema is a
        # namespace) — the entity views are first-class discovery citizens.
        raw_tables = await conn.fetch(
            f"""
            SELECT table_schema, table_name, table_type
            FROM information_schema.tables
            WHERE table_schema NOT IN {_SYSTEM_SCHEMAS}
              AND table_type IN ('BASE TABLE', 'VIEW')
            ORDER BY table_schema, table_name
            """
        )

        tables = []
        # (schema, entity_base) → grouped entry
        entities: dict[tuple[str, str], dict] = {}

        def _entity(schema: str, base: str) -> dict:
            key = (schema, base)
            if key not in entities:
                entities[key] = {
                    "namespace": schema,
                    "entity": base[len("doc_"):],
                    "default_view": base,
                    "default_view_present": False,
                    "entities_view": f"{base}__entities",
                    "legacy_table": False,
                    "versions": [],
                    "row_count": 0,
                }
            return entities[key]

        for row in raw_tables:
            schema = row["table_schema"]
            tname = row["table_name"]
            is_view = row["table_type"] == "VIEW"
            if not (tname.startswith(allowed_prefixes) or tname in allowed_exact):
                continue
            if namespace and schema != namespace:
                continue
            if table_name and tname != table_name:
                continue

            columns = await conn.fetch(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = $1
                  AND table_name = $2
                ORDER BY ordinal_position
                """,
                schema,
                tname,
            )

            count = await conn.fetchval(f'SELECT COUNT(*) FROM "{schema}"."{tname}"')

            entry: dict = {
                "namespace": schema,
                "name": tname,
                "kind": "view" if is_view else "table",
                "template_value": tname[len("doc_"):] if tname.startswith("doc_") else None,
                "qualified_name": f'"{schema}"."{tname}"',
                "row_count": count,
            }

            if table_name:
                entry["columns"] = [
                    {
                        "name": c["column_name"],
                        "type": c["data_type"],
                        "nullable": c["is_nullable"] == "YES",
                    }
                    for c in columns
                ]
            else:
                entry["column_count"] = len(columns)

            tables.append(entry)

            # Entity grouping (skip in single-relation detail mode).
            if table_name or not tname.startswith("doc_"):
                continue
            m = version_re.match(tname)
            if m and not is_view:
                ent = _entity(schema, m.group(1))
                ent["versions"].append(
                    {"version": int(m.group(2)), "table": tname, "row_count": count}
                )
                ent["row_count"] += count or 0
            elif tname.endswith("__entities") and is_view:
                _entity(schema, tname[: -len("__entities")])
            elif is_view:
                _entity(schema, tname)["default_view_present"] = True
            else:
                # A bare-name BASE TABLE: the legacy pre-split layout.
                ent = _entity(schema, tname)
                ent["legacy_table"] = True
                ent["row_count"] += count or 0

        if table_name and not tables:
            raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found")

    for ent in entities.values():
        ent["versions"].sort(key=lambda v: v["version"])

    result: dict = {"tables": tables}
    if not table_name:
        result["entities"] = sorted(
            entities.values(), key=lambda e: (e["namespace"], e["entity"])
        )
    return result


class ReportQuery(StrictModel):
    """Request model for ad-hoc reporting queries."""

    sql: str
    params: list[Any] = Field(default_factory=list)
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    max_rows: int = Field(default=1000, ge=1, le=50000)
    namespace: str | None = Field(
        default=None,
        description=(
            "When set, the query runs with search_path = this namespace's schema "
            "(CASE-628), so unqualified table names like `doc_patient` resolve "
            "there. Omit for cross-namespace queries and schema-qualify instead."
        ),
    )


# Pattern matching dangerous SQL keywords at word boundaries
_DANGEROUS_SQL_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


@router.post("/query", dependencies=[_PRIVILEGED])
async def execute_query(body: ReportQuery):
    """Execute a read-only SQL query against the reporting database."""
    if not state.read_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not connected")

    # Safety: reject write/DDL statements
    if _DANGEROUS_SQL_RE.search(body.sql):
        raise HTTPException(
            status_code=400,
            detail="Only read-only queries are allowed. "
            "INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE, GRANT, and REVOKE are prohibited.",
        )

    # Resolve the namespace search_path schema (CASE-628), validating it as a
    # safe identifier before interpolation.
    search_path_schema: str | None = None
    if body.namespace:
        try:
            search_path_schema = SchemaManager(state.postgres_pool).schema_for(body.namespace)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    # Build wrapped query with row limit
    wrapped_sql = f"SELECT * FROM ({body.sql}) _q LIMIT {body.max_rows + 1}"

    # Build positional parameter references
    params = body.params

    async def _run(conn: asyncpg.Connection) -> tuple[list[str], list]:
        # Explicit prepare, two reasons:
        # - the statement's own result description supplies the column names,
        #   so an empty result still reports its columns (they used to come
        #   from row 1, making "no rows" indistinguishable from "no columns");
        # - explicit prepare bypasses asyncpg's per-connection statement
        #   cache, so a registered view rebuilt between calls (replace is
        #   DROP + CREATE) cannot leave this path a stale cached plan. A
        #   cached plan error cannot self-heal here: asyncpg only re-prepares
        #   transparently OUTSIDE explicit transactions, and the namespaced
        #   branch below runs inside one for its SET LOCAL.
        stmt = await conn.prepare(wrapped_sql)
        columns = [a.name for a in stmt.get_attributes()]
        return columns, list(await stmt.fetch(*params))

    try:
        assert state.read_pool is not None  # narrowed above
        for attempt in (1, 2):
            try:
                async with state.read_pool.acquire() as conn:
                    # Set statement timeout and read-only transaction
                    await conn.execute(
                        f"SET statement_timeout = {body.timeout_seconds * 1000}"
                    )
                    await conn.execute("SET default_transaction_read_only = on")

                    if search_path_schema:
                        # SET LOCAL inside a transaction auto-resets on
                        # commit, so the search_path never leaks back to the
                        # pooled connection.
                        async with conn.transaction():
                            await conn.execute(
                                f'SET LOCAL search_path = "{search_path_schema}", public'
                            )
                            columns, rows = await _run(conn)
                    else:
                        columns, rows = await _run(conn)
                break
            except asyncpg.InvalidCachedStatementError:
                # Residual race only (a rebuild landing between this
                # request's prepare and fetch): the error itself purged the
                # stale plan, so one retry re-prepares and succeeds; a second
                # failure is a real error and propagates.
                if attempt == 2:
                    raise

        # Detect truncation
        truncated = len(rows) > body.max_rows
        if truncated:
            rows = rows[: body.max_rows]

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
_EXPORT_ALLOWED_EXACT = {"terminologies", "terms", "term_relations", "templates"}


def _is_allowed_table(name: str) -> bool:
    return name.startswith(_EXPORT_ALLOWED_PREFIXES) or name in _EXPORT_ALLOWED_EXACT


class CsvExportQuery(StrictModel):
    """Request model for query-based CSV export."""

    sql: str
    params: list[Any] = Field(default_factory=list)
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
    namespace: str = Query(description="Namespace (PostgreSQL schema) the table lives in"),
    table: str = Query(description="Bare table name to export (e.g. doc_patient)"),
    timeout_seconds: int = Query(default=120, ge=1, le=600),
    filename: str | None = Query(default=None, description="Download filename"),
    identity: UserIdentity = Depends(require_api_key),
):
    """Export a full reporting table as streaming CSV.

    ``namespace`` is required (CASE-628): the table lives in that namespace's
    schema.
    """
    await check_namespace_permission(identity, namespace, "read")
    if not state.read_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not connected")

    if not _is_allowed_table(table):
        raise HTTPException(
            status_code=400,
            detail=f"Table '{table}' is not available for export. "
            "Only doc_* tables and metadata tables (terminologies, terms, term_relations, templates) are allowed.",
        )

    # Validate both identifiers before interpolation.
    try:
        schema = SchemaManager(state.postgres_pool).schema_for(namespace)
        SchemaManager._safe_ident(table)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    qualified = f'"{schema}"."{table}"'
    download_name = filename or f"{namespace}_{table}.csv"

    async def generate():
        assert state.read_pool is not None
        async with state.read_pool.acquire() as conn:
            await conn.execute(f"SET statement_timeout = {timeout_seconds * 1000}")
            await conn.execute("SET default_transaction_read_only = on")
            async for chunk in _stream_csv(conn, f'SELECT * FROM {qualified}', []):
                yield chunk

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{download_name}"'},
    )


@router.post("/export/csv", dependencies=[_PRIVILEGED])
async def export_query_csv(body: CsvExportQuery):
    """Export a SQL query result as streaming CSV."""
    if not state.read_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not connected")

    if _DANGEROUS_SQL_RE.search(body.sql):
        raise HTTPException(
            status_code=400,
            detail="Only read-only queries are allowed. "
            "INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE, GRANT, and REVOKE are prohibited.",
        )

    async def generate():
        assert state.read_pool is not None
        async with state.read_pool.acquire() as conn:
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


@router.delete("/namespace/{prefix}", dependencies=[_PRIVILEGED])
async def delete_namespace(prefix: str):
    """Delete all reporting data for a namespace.

    CASE-628: each namespace is a PostgreSQL schema, so removal is a single
    ``DROP SCHEMA "<prefix>" CASCADE`` — every doc_* and metadata table inside
    it (and their indexes) goes atomically. The namespace's rows in the shared
    bookkeeping tables are cleared too.

    Called by Registry during namespace deletion.
    """
    if not state.postgres_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not connected")

    if not prefix or not prefix.strip():
        raise HTTPException(status_code=400, detail="Namespace prefix is required")

    # Validate the prefix as a safe SQL identifier before interpolating it into
    # DROP SCHEMA (identifiers cannot be parameterised).
    sm = SchemaManager(state.postgres_pool)
    try:
        schema = sm.schema_for(prefix)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # total_deleted MUST be a real integer: Registry records it as the
    # deletion journal's `postgres_rows` audit field, which is int-typed —
    # a null here poisoned the journal and turned every otherwise-successful
    # namespace DELETE into a validation error. drop_namespace_schema counts
    # the schema's rows before dropping it.
    total_deleted = await sm.drop_namespace_schema(prefix)

    async with state.postgres_pool.acquire() as conn:
        # Clear the namespace's rows from the shared bookkeeping tables (all
        # in public, keyed by namespace — see init_postgres_schema).
        # asyncpg returns a "DELETE <n>" status string; fold those rows into
        # the audit total.
        for table in ("_wip_schema_migrations", "_wip_sync_status", "_wip_views"):
            status = await conn.execute(
                f"DELETE FROM public.{table} WHERE namespace = $1", prefix
            )
            total_deleted += int(status.rsplit(" ", 1)[-1])

    logger.info(f"Namespace {prefix} reporting schema dropped ({schema})")
    return {"namespace": prefix, "dropped_schema": schema, "total_deleted": total_deleted}


# =============================================================================
# NAMESPACE VIEWS
# =============================================================================

class RegisterViewRequest(StrictModel):
    name: str = Field(..., description="View name (becomes a schema-qualified SQL view)")
    sql: str = Field(..., description="SELECT statement the view wraps")
    registered_by: str | None = Field(None, description="Caller identity for audit")


class ViewRowsFilter(StrictModel):
    """One structured predicate on a view column. No caller SQL anywhere:
    the column must be one of the view's actual columns, the operator comes
    from this closed set, and the value is bound as a statement parameter."""

    column: str
    op: Literal[
        "eq", "ne", "lt", "lte", "gt", "gte", "like", "ilike", "in",
        "is_null", "not_null",
    ] = "eq"
    value: Any = Field(
        None,
        description="Comparison value; a list for 'in'; omitted for the null checks",
    )


class ViewRowsSort(StrictModel):
    column: str
    direction: Literal["asc", "desc"] = "asc"


class ViewRowsRequest(StrictModel):
    """Structured read of a registered view — the namespace-scoped
    alternative to raw POST /query."""

    filters: list[ViewRowsFilter] = Field(default_factory=list)
    sort: list[ViewRowsSort] = Field(default_factory=list)
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)
    timeout_seconds: int = Field(default=30, ge=1, le=300)


_VIEW_FILTER_SQL = {
    "eq": "=", "ne": "<>", "lt": "<", "lte": "<=", "gt": ">", "gte": ">=",
    "like": "LIKE", "ilike": "ILIKE",
}


# dependent_objects_still_exist — a no-CASCADE DROP hit a dependent relation.
_PG_DEPENDENT_OBJECTS = "2BP01"


@router.post("/namespace/{prefix}/views")
async def register_view(
    prefix: str,
    body: RegisterViewRequest,
    response: Response,
    identity: UserIdentity = Depends(require_api_key),
):
    """Register a named SQL view in a namespace's reporting schema.

    The view is created immediately and persisted in _wip_views, from which
    it is recreated (best-effort) at startup and after reporting rebuilds;
    bootstrap re-registration remains the authoritative recovery after a
    full reset. Re-registering an existing name replaces the definition via
    DROP + CREATE in one transaction — the column set may change freely —
    and returns 200; a first registration returns 201.

    Authorization is two-tier. wip-admins / wip-services register freely.
    A key with ADMIN on the namespace may also register here — but its
    view must be contained: every relation the created view depends on
    must live in this namespace's own schema. The dependency check reads
    pg_depend on the view Postgres actually created (no SQL parsing), so
    a schema-qualified reference to another namespace's schema, to public
    bookkeeping, or to pg_catalog rolls the registration back with 403.
    Views ARE the structured surface namespace keys read (the /rows
    endpoint), so an uncontained view registered by a namespace admin
    would be a cross-tenant read path; a privileged key registering an
    uncontained view is a deliberate decision about what the namespace
    exposes, and reading it with namespace read is the same trust model
    as any other relation in the schema.

    The SQL runs with search_path set to this namespace's schema plus
    public, so unqualified names (``doc_<value>``, ``term_relations``, …)
    resolve here. Other reporting schemas are reachable only when
    schema-qualified — the same posture as POST /query, the surface that
    reads the view. The definition must be a single read-only SELECT:
    write/DDL keywords are rejected, and validation EXPLAINs the statement
    as a prepared subselect inside a read-only transaction, so a second
    ';'-separated statement is a hard parse error rather than something
    that executes. Platform relation names (``doc_*``, ``*__v<N>``,
    ``*__entities``, the metadata tables, ``_wip_*``) are reserved, and a
    name occupied by a relation this endpoint does not own is a 409.
    """
    privileged = identity.has_group("wip-admins") or identity.has_group("wip-services")
    if not privileged:
        await check_namespace_permission(identity, prefix, "admin")

    if not state.postgres_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not connected")

    sm = SchemaManager(state.postgres_pool)
    try:
        safe_name = sm._safe_ident(body.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if SchemaManager.is_reserved_relation_name(body.name):
        raise HTTPException(
            status_code=400,
            detail="View name collides with the platform's reporting relations "
                   "(doc_*, *__v<N>, *__entities, metadata tables, _wip_*). "
                   "Pick a distinct name, e.g. a v_ prefix.",
        )

    # Trailing semicolons would break the subselect wrap below; strip them
    # so "SELECT 1;" registers cleanly while "SELECT 1; DROP x" still fails.
    view_sql = body.sql.strip().rstrip(";").strip()
    if not view_sql:
        raise HTTPException(status_code=400, detail="View SQL is empty")

    # Reject DDL/write keywords in the view SQL
    if _DANGEROUS_SQL_RE.search(view_sql):
        raise HTTPException(
            status_code=400,
            detail="View SQL must be a read-only SELECT. "
                   "INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE, GRANT, and REVOKE are prohibited.",
        )

    try:
        # The schema may not exist yet when an app bootstraps views before
        # its first sync; creating it is harmless and namespace deletion
        # cleans it up.
        schema = await sm.ensure_schema(prefix)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    async with state.postgres_pool.acquire() as conn:
        already_registered = bool(await conn.fetchval(
            "SELECT TRUE FROM public._wip_views WHERE namespace = $1 AND view_name = $2",
            prefix, body.name,
        ))
        if not already_registered:
            # The name must be free: replacing a relation this endpoint does
            # not own (an entity view, a metadata table, a manual view)
            # would hijack it.
            occupied = await conn.fetchval(
                """
                SELECT TRUE FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = $1 AND c.relname = $2
                """,
                schema, body.name,
            )
            if occupied:
                raise HTTPException(
                    status_code=409,
                    detail=f"Relation {body.name!r} already exists in schema "
                           f"{schema!r} and is not a registered view",
                )

        # Validate before touching anything: a PREPARED statement (fetch)
        # admits exactly one SQL command — a ';'-separated second statement
        # fails at parse instead of executing — and the subselect wrap plus
        # the read-only transaction keep planning side-effect-free.
        # Planned as the reader role: a definition may only reference what
        # the read path can see.
        if not state.read_pool:
            raise HTTPException(status_code=503, detail="PostgreSQL not connected")
        try:
            async with state.read_pool.acquire() as read_conn, read_conn.transaction(
                readonly=True
            ):
                await read_conn.execute(f'SET LOCAL search_path = "{schema}", public')
                await read_conn.fetch(
                    f"EXPLAIN SELECT * FROM ({view_sql}) _wip_view_check"
                )
        except asyncpg.PostgresError as e:
            raise HTTPException(
                status_code=400, detail=f"View SQL is invalid: {e}"
            ) from e

        # Create (or replace) the view and persist it, atomically.
        try:
            async with conn.transaction():
                await conn.execute(f'SET LOCAL search_path = "{schema}", public')
                if already_registered:
                    # DROP + CREATE rather than CREATE OR REPLACE: Postgres
                    # rejects OR REPLACE when the column set changes, and
                    # evolving a view's columns is the normal workflow here.
                    await conn.execute(
                        f'DROP VIEW IF EXISTS "{schema}"."{safe_name}"'
                    )
                await conn.execute(
                    f'CREATE VIEW "{schema}"."{safe_name}" AS {view_sql}'
                )
                if not privileged:
                    # Containment for namespace-admin registrations: ask
                    # Postgres (pg_depend via the view's rewrite rule) which
                    # relations the view it just created actually depends
                    # on. Any dependency outside the namespace's own schema
                    # — another namespace, public bookkeeping, pg_catalog —
                    # rolls the whole registration back. This is exact and
                    # privilege-independent, unlike parsing the SQL.
                    # (Function dependencies are not checked: nothing a
                    # caller can reach creates functions, so a view cannot
                    # smuggle cross-schema reads through one.)
                    foreign = await conn.fetch(
                        """
                        SELECT DISTINCT dn.nspname AS schema, dc.relname AS relation
                        FROM pg_depend d
                        JOIN pg_rewrite r ON r.oid = d.objid
                        JOIN pg_class v ON v.oid = r.ev_class
                        JOIN pg_namespace vn ON vn.oid = v.relnamespace
                        JOIN pg_class dc ON dc.oid = d.refobjid
                        JOIN pg_namespace dn ON dn.oid = dc.relnamespace
                        WHERE d.classid = 'pg_rewrite'::regclass
                          AND d.refclassid = 'pg_class'::regclass
                          AND vn.nspname = $1 AND v.relname = $2
                          AND dc.oid <> v.oid
                          AND dn.nspname <> $1
                        """,
                        schema, safe_name,
                    )
                    if foreign:
                        names = ", ".join(
                            f"{r['schema']}.{r['relation']}" for r in foreign
                        )
                        raise HTTPException(
                            status_code=403,
                            detail=(
                                "View references relations outside this "
                                f"namespace's schema ({names}). A namespace-"
                                "admin key may only register views contained "
                                "in the namespace's own schema; cross-schema "
                                "views require a wip-admins or wip-services "
                                "key."
                            ),
                        )
                await conn.execute(
                    """
                    INSERT INTO public._wip_views (namespace, view_name, view_sql, registered_by)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (namespace, view_name) DO UPDATE
                        SET view_sql = EXCLUDED.view_sql,
                            registered_at = NOW(),
                            registered_by = EXCLUDED.registered_by
                    """,
                    prefix, body.name, view_sql,
                    body.registered_by or get_identity_string(),
                )
        except asyncpg.PostgresError as e:
            status = (
                409 if getattr(e, "sqlstate", None) == _PG_DEPENDENT_OBJECTS
                else 400
            )
            raise HTTPException(
                status_code=status, detail=f"View registration failed: {e}"
            ) from e

    response.status_code = 200 if already_registered else 201
    logger.info(f"Registered view {prefix}.{body.name}")
    return {
        "namespace": prefix,
        "name": body.name,
        "schema_qualified": f'"{schema}"."{safe_name}"',
        "created": not already_registered,
    }


@router.get("/namespace/{prefix}/views")
async def list_views(
    prefix: str,
    identity: UserIdentity = Depends(require_api_key),
):
    """List all registered views for a namespace."""
    await check_namespace_permission(identity, prefix, "read")
    if not state.postgres_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not connected")

    async with state.postgres_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT namespace, view_name, view_sql, registered_at, registered_by "
            "FROM public._wip_views WHERE namespace = $1 ORDER BY view_name",
            prefix,
        )

    return {
        "namespace": prefix,
        "views": [
            {
                "namespace": r["namespace"],
                "name": r["view_name"],
                "sql": r["view_sql"],
                "registered_at": r["registered_at"],
                "registered_by": r["registered_by"],
            }
            for r in rows
        ],
    }


@router.post("/namespace/{prefix}/views/{view_name}/rows")
async def read_view_rows(
    prefix: str,
    view_name: str,
    body: ViewRowsRequest | None = None,
    identity: UserIdentity = Depends(require_api_key),
):
    """Read a registered view's rows with structured filters — the
    namespace-scoped read path for views.

    Raw SQL (POST /query) stays wip-admins / wip-services: it can name any
    schema. This endpoint takes no caller SQL at all — the view by name, a
    closed operator set validated against the view's actual columns, bound
    parameters, sort, limit and offset — so READ on the namespace is a
    sufficient and checkable gate, and it runs on the read-only reporting
    role. Only views registered in this namespace are readable here; the
    response matches POST /query (columns, rows as objects keyed by column,
    row_count, truncated). An empty body reads the first page unfiltered.
    """
    await check_namespace_permission(identity, prefix, "read")
    body = body or ViewRowsRequest()

    if not state.postgres_pool or not state.read_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not connected")

    sm = SchemaManager(state.postgres_pool)
    try:
        schema = sm.schema_for(prefix)
        safe_name = sm._safe_ident(view_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Only registered views are readable here — this is the views read
    # surface, not a general table reader (export/csv covers tables).
    async with state.postgres_pool.acquire() as conn:
        registered = bool(await conn.fetchval(
            "SELECT TRUE FROM public._wip_views WHERE namespace = $1 AND view_name = $2",
            prefix, view_name,
        ))
    if not registered:
        raise HTTPException(
            status_code=404,
            detail=f"View {view_name!r} not found in namespace {prefix!r}",
        )

    def _quoted(column: str) -> str:
        return '"' + column.replace('"', '""') + '"'

    async def _run(conn: asyncpg.Connection) -> tuple[list[str], list]:
        # Explicit (uncached) prepare, same reasoning as POST /query: the
        # view's own result description supplies the columns — also for the
        # column validation below — and a replace (DROP + CREATE) between
        # requests cannot leave a stale cached plan.
        base = f'SELECT * FROM "{schema}"."{safe_name}"'
        probe = await conn.prepare(base)
        columns = [a.name for a in probe.get_attributes()]
        valid = set(columns)

        where: list[str] = []
        params: list[Any] = []
        requested = [f.column for f in body.filters] + [s.column for s in body.sort]
        unknown = [c for c in requested if c not in valid]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown column(s) {unknown} — the view has {columns}",
            )
        for f in body.filters:
            if f.op == "is_null":
                where.append(f"{_quoted(f.column)} IS NULL")
            elif f.op == "not_null":
                where.append(f"{_quoted(f.column)} IS NOT NULL")
            elif f.op == "in":
                if not isinstance(f.value, list):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Filter op 'in' on {f.column!r} needs a list value",
                    )
                params.append(f.value)
                where.append(f"{_quoted(f.column)} = ANY(${len(params)})")
            else:
                params.append(f.value)
                where.append(
                    f"{_quoted(f.column)} {_VIEW_FILTER_SQL[f.op]} ${len(params)}"
                )

        sql = base
        if where:
            sql += " WHERE " + " AND ".join(where)
        if body.sort:
            sql += " ORDER BY " + ", ".join(
                f"{_quoted(s.column)} {s.direction.upper()}" for s in body.sort
            )
        # limit + 1 so truncation is detectable without a second count query.
        sql += f" LIMIT {body.limit + 1} OFFSET {body.offset}"

        stmt = await conn.prepare(sql)
        return columns, list(await stmt.fetch(*params))

    try:
        for attempt in (1, 2):
            try:
                async with state.read_pool.acquire() as conn:
                    await conn.execute(
                        f"SET statement_timeout = {body.timeout_seconds * 1000}"
                    )
                    await conn.execute("SET default_transaction_read_only = on")
                    columns, rows = await _run(conn)
                break
            except asyncpg.InvalidCachedStatementError:
                # Residual race only (replace landing between prepare and
                # fetch) — the error purged the plan; one retry re-prepares.
                if attempt == 2:
                    raise
    except asyncpg.QueryCanceledError as e:
        raise HTTPException(
            status_code=408,
            detail=f"Query timed out after {body.timeout_seconds} seconds",
        ) from e
    except asyncpg.PostgresError as e:
        raise HTTPException(status_code=400, detail=f"View read failed: {e}") from e

    truncated = len(rows) > body.limit
    if truncated:
        rows = rows[: body.limit]
    return {
        "columns": columns,
        "rows": [dict(r) for r in rows],
        "row_count": len(rows),
        "truncated": truncated,
    }


@router.delete("/namespace/{prefix}/views/{view_name}")
async def delete_view(
    prefix: str,
    view_name: str,
    identity: UserIdentity = Depends(require_api_key),
):
    """Drop a registered view and its registration, atomically.

    Same two-tier authorization as registration: wip-admins / wip-services,
    or ADMIN on the namespace — dropping a view in your own namespace
    exposes nothing, so no containment question arises.

    The DROP runs first, inside the same transaction as the bookkeeping
    delete: if the view cannot be dropped (another registered view depends
    on it — no CASCADE), the registration survives and the caller gets a
    409 naming the dependency, instead of an orphaned live view that
    recreation no longer knows about.
    """
    if not (identity.has_group("wip-admins") or identity.has_group("wip-services")):
        await check_namespace_permission(identity, prefix, "admin")
    if not state.postgres_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not connected")

    sm = SchemaManager(state.postgres_pool)
    try:
        schema = sm.schema_for(prefix)
        safe_name = sm._safe_ident(view_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    async with state.postgres_pool.acquire() as conn:
        registered = bool(await conn.fetchval(
            "SELECT TRUE FROM public._wip_views WHERE namespace = $1 AND view_name = $2",
            prefix, view_name,
        ))
        if not registered:
            raise HTTPException(
                status_code=404,
                detail=f"View {view_name!r} not found in namespace {prefix!r}",
            )
        try:
            async with conn.transaction():
                await conn.execute(f'DROP VIEW IF EXISTS "{schema}"."{safe_name}"')
                await conn.execute(
                    "DELETE FROM public._wip_views WHERE namespace = $1 AND view_name = $2",
                    prefix, view_name,
                )
        except asyncpg.PostgresError as e:
            status = (
                409 if getattr(e, "sqlstate", None) == _PG_DEPENDENT_OBJECTS
                else 400
            )
            raise HTTPException(
                status_code=status, detail=f"View drop failed: {e}"
            ) from e

    logger.info(f"Deleted view {prefix}.{view_name}")
    # `name`, not `view_name`: the responses speak the request vocabulary
    # uniformly across register/list/delete, so typed clients never freeze
    # an asymmetry. The _wip_views columns remain internal.
    return {"namespace": prefix, "name": view_name, "dropped": True}


# On the app, not the router: a public build-provenance probe, like the other
# services' prefixed roots.
@app.get("/api/reporting-sync/")
async def root():
    """Root endpoint."""
    return {
        "service": settings.service_name,
        "version": __version__,
        # CASE-526: uniform build-provenance block (sha/built_at/image_tag).
        "build": build_metadata(__version__),
        # Docs are served at the unprefixed FastAPI default, reachable only
        # when addressing the service directly — the platform-wide pattern
        # for all five services. The Caddy route for this service preserves
        # the /api/reporting-sync prefix, so a prefixed docs path would 404.
        "docs": "/docs",
        "health": "/api/reporting-sync/health",
        "status": "/api/reporting-sync/status",
        "integrity": "/api/reporting-sync/health/integrity",
        "search": "/api/reporting-sync/search",
        "activity": "/api/reporting-sync/activity/recent",
    }


app.include_router(router)

# Declare the X-API-Key requirement in the OpenAPI contract — runtime
# Depends() enforcement emits nothing into the schema, and a client
# generated from an auth-silent schema would not send a key.
declare_api_key_security(app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "reporting_sync.main:app",
        host="0.0.0.0",
        port=8005,
        reload=settings.debug,
    )
