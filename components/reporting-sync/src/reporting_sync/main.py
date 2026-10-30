"""
WIP Reporting Sync Service

FastAPI application providing health endpoints and sync management.
The actual sync work is done by the worker module.
"""

import asyncio
import contextlib
import csv
import dataclasses
import io
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Literal

import asyncpg
import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from wip_auth import (
    UserIdentity,
    build_metadata,
    check_namespace_permission,
    declare_api_key_security,
    get_identity_string,
    install_liveness,
    require_api_key,
    require_groups,
    resolve_accessible_namespaces,
    setup_auth,
    setup_key_sync,
)
from wip_auth.events import WIP_EVENTS_STREAM, EventBus, StreamSpec
from wip_auth.ratelimit import setup_rate_limiting
from wip_auth.registry_client import RegistryError
from wip_auth.security import check_production_security
from wip_auth.startup import retry_until_done

from . import __version__
from .batch_sync import BatchSyncService
from .bookkeeping import compact_migration_journal, repair_bookkeeping_location
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
from .parity_monitor import ParityMonitor, ParityMonitorStatus
from .pg_roles import RoleSpec, is_privileged, provision_roles
from .schema_manager import (
    REGISTERED_VIEW_OPTIONS,
    NamespaceDeletedError,
    SchemaManager,
    registry_namespace_status,
)
from .scoped_reads import ScopedReads, ScopedReadsUnavailable
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

    # The shared self-healing NATS connection: it connects in the background,
    # reconnects without limit, converges WIP_EVENTS, and supervises the sync
    # worker (restarting it whenever its consumer loop dies).
    bus: EventBus = EventBus(None, name="reporting-sync")
    # Owner pool: the sync pipeline, DDL and bookkeeping. Reader pool:
    # caller-driven SQL from privileged keys (raw query, exports, view
    # validation, search). Scoped reads: caller-driven SQL from every other
    # key, on connections that can read only the caller's namespaces.
    postgres_pool: asyncpg.Pool | None = None
    read_pool: asyncpg.Pool | None = None
    scoped_reads: ScopedReads | None = None
    sync_task: asyncio.Task | None = None
    alert_check_task: asyncio.Task | None = None
    initial_sync_task: asyncio.Task | None = None
    postgres_task: asyncio.Task | None = None
    batch_sync_service: BatchSyncService | None = None
    parity_monitor: ParityMonitor | None = None
    parity_task: asyncio.Task | None = None
    search_service: SearchService | None = None
    sync_status: SyncStatus = SyncStatus(
        running=False,
        connected_to_nats=False,
        connected_to_postgres=False,
    )


    @property
    def nats_client(self) -> Any:
        """The live NATS client, or None while not connected."""
        return self.bus.client

    @property
    def jetstream(self) -> Any:
        """The live JetStream context, or None while not connected."""
        return self.bus.jetstream


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


def _events_stream() -> StreamSpec:
    """The canonical platform event stream, under this install's configured name."""
    return dataclasses.replace(WIP_EVENTS_STREAM, name=settings.nats_stream_name)


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
        try:
            await compact_migration_journal(bootstrap)
        except asyncpg.PostgresError as e:
            # Advisory bookkeeping: a login without rights on the journal must
            # not stop the service from starting.
            logger.warning("Migration journal compaction skipped: %s", e)
        if await bootstrap.fetchval(
            "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
        ):
            await provision_roles(bootstrap, RoleSpec(
                owner=settings.postgres_owner_user,
                owner_password=settings.postgres_owner_password,
                reader=settings.postgres_read_user,
                reader_password=settings.postgres_read_password,
                manager=settings.postgres_role_manager_user,
                manager_password=settings.postgres_role_manager_password,
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


async def _retire_archived_status(pool: asyncpg.Pool) -> None:
    """Rewrite reporting rows of the removed `archived` document status.

    The status was removed: an archived document was a soft-deleted one
    under another name, and document-store rewrites its own rows to
    `inactive` at startup. Here the matching rows become `deleted`, the
    status a soft delete writes. Every physical document table (views are
    derived) that has a `status` column, in every schema. Idempotent and
    best-effort: a failure is logged and never blocks startup.
    """
    try:
        async with pool.acquire() as conn:
            tables = await conn.fetch(
                """
                SELECT c.table_schema, c.table_name
                FROM information_schema.columns c
                JOIN information_schema.tables t
                  ON t.table_schema = c.table_schema AND t.table_name = c.table_name
                WHERE c.column_name = 'status'
                  AND t.table_type = 'BASE TABLE'
                  AND c.table_name LIKE 'doc\\_%'
                """
            )
            retired = 0
            for row in tables:
                schema = row["table_schema"].replace('"', '""')
                table = row["table_name"].replace('"', '""')
                result = await conn.execute(
                    f'UPDATE "{schema}"."{table}" SET status = $1 WHERE status = $2',  # nosec B608  # identifiers from information_schema, quoted; values bound
                    "deleted", "archived",
                )
                retired += int(result.split()[-1]) if result.startswith("UPDATE") else 0
    except Exception as e:
        logger.warning(f"Retiring the 'archived' reporting status failed: {e}")
        return
    if retired:
        logger.info(f"Retired the 'archived' status on {retired} reporting row(s) (now 'deleted')")


# Backoff between attempts of one backfill step: 5 s doubling to 60 s, with no
# overall deadline — a step that cannot complete keeps trying until it does.
_BACKFILL_FIRST_DELAY = 5.0
_BACKFILL_MAX_DELAY = 60.0
_backfill_sleep = asyncio.sleep  # replaced in tests


async def _source_healthy(base_url: str) -> bool:
    """Whether a source service answers /health with 200."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            return (await client.get(f"{base_url}/health")).status_code == 200
    except httpx.HTTPError:
        return False


async def _backfill_until_complete(
    label: str, source_url: str, sync: Callable[[], Awaitable[dict[str, Any]]]
) -> None:
    """Run one backfill step until its sync reports a complete listing.

    A health check alone is not enough: a source can answer /health 200 while
    its listing still fails (a database not yet usable behind it). So the
    step retries until the batch sync itself says `complete`, not merely
    until the source looks up. Batch syncs are idempotent upserts, so a
    repeat is harmless.
    """
    delay = _BACKFILL_FIRST_DELAY
    attempt = 0
    while True:
        attempt += 1
        if not await _source_healthy(source_url):
            outcome = f"{source_url} not healthy"
        else:
            try:
                result = await sync()
            except Exception as e:  # a step must never kill the backfill
                result = {"complete": False, "error": str(e)}
            if result.get("complete", True):
                logger.info(f"Initial {label} sync complete (attempt {attempt}): {result}")
                return
            outcome = f"listing incomplete: {result}"
        logger.warning(
            f"Initial {label} sync not complete (attempt {attempt}, {outcome}); "
            f"retrying in {delay:.0f}s"
        )
        await _backfill_sleep(delay)
        delay = min(delay * 2, _BACKFILL_MAX_DELAY)


async def _initial_metadata_sync(batch_sync_service: BatchSyncService) -> None:
    """Background: backfill reporting metadata until every step is complete.

    Terminologies, terms, term relations and template metadata reach
    PostgreSQL live only through events, so a rebuilt or freshly started
    reporting database holds none of what existed before until this backfill
    runs. Each step waits on its OWN source — template metadata comes from
    template-store, not def-store — and retries until its listing completes:
    services start in parallel, and a source can be up yet unable to list for
    a while (a database restarting behind it). The def-store steps run in
    order, as terms and relations build on terminologies; the template-store
    step runs alongside, so either source being down never blocks the other.
    """
    async def def_store_chain() -> None:
        await _backfill_until_complete(
            "terminology", settings.def_store_url, batch_sync_service.batch_sync_terminologies
        )
        await _backfill_until_complete(
            "term", settings.def_store_url, batch_sync_service.batch_sync_terms
        )
        await _backfill_until_complete(
            "term-relation", settings.def_store_url, batch_sync_service.batch_sync_term_relations
        )

    await asyncio.gather(
        def_store_chain(),
        _backfill_until_complete(
            "template metadata", settings.template_store_url,
            batch_sync_service.batch_sync_templates,
        ),
    )


_postgres_sleep = asyncio.sleep  # replaced in tests


async def _bring_up_postgres() -> None:
    """Background: connect to PostgreSQL, then start everything that needs it.

    Retries without a deadline (any connect failure, including PostgreSQL's
    "the database system is starting up", counts as not yet). Unusable
    service roles are a deployment fault, not an outage: they stop the loop
    and /health answers 503 until the install is fixed.
    """
    async def attempt() -> bool:
        try:
            pool, read_pool = await connect_postgres()
        except ServiceRolesError as e:
            logger.critical(f"PostgreSQL service roles unusable: {e}")
            state.sync_status.connected_to_postgres = False
            state.sync_status.postgres_service_roles_ok = False
            return True
        try:
            # Bookkeeping tables only. Per-namespace metadata and document
            # tables are created lazily inside each namespace's schema on
            # first sync — there is no global pre-creation.
            await init_postgres_schema(pool)
            await _recreate_registered_views(pool)
            await _retire_archived_status(pool)
        except BaseException:
            await read_pool.close()
            await pool.close()
            raise
        _start_postgres_work(pool, read_pool)
        return True

    await retry_until_done(attempt, description="PostgreSQL connect", sleep=_postgres_sleep)


def _start_postgres_work(pool: asyncpg.Pool, read_pool: asyncpg.Pool) -> None:
    """Record the pools and start the work that needs PostgreSQL."""
    state.postgres_pool = pool
    state.read_pool = read_pool
    state.sync_status.connected_to_postgres = True
    state.sync_status.postgres_service_roles_ok = True
    state.search_service = SearchService(read_pool)
    if settings.postgres_role_manager_password:
        state.scoped_reads = ScopedReads(
            endpoint={
                "host": settings.postgres_host,
                "port": settings.postgres_port,
                "database": settings.postgres_db,
            },
            owner_pool=pool,
            manager_user=settings.postgres_role_manager_user,
            manager_password=settings.postgres_role_manager_password,
            max_connections=settings.postgres_read_pool_max,
        )
    else:
        logger.warning(
            "POSTGRES_ROLE_MANAGER_PASSWORD is not set: SQL from non-privileged "
            "keys (query, query export, view rows) answers 503 until the "
            "install is re-rendered"
        )
    logger.info("PostgreSQL connected")

    state.batch_sync_service = BatchSyncService(pool)
    # Initial metadata sync: each step waits on its own source and retries
    # until its listing completes (see _initial_metadata_sync).
    state.initial_sync_task = asyncio.create_task(
        _initial_metadata_sync(state.batch_sync_service)
    )
    logger.info("Initial metadata sync scheduled (background)")

    # Standing parity check: finds reporting gaps after the fact (a restore
    # that stopped waiting, a missed event, lost views) and repairs them.
    state.parity_monitor = ParityMonitor(pool, state.batch_sync_service)
    state.parity_task = asyncio.create_task(state.parity_monitor.run_forever())

    # The bus runs the sync worker as soon as NATS is connected and restarts
    # it whenever its consumer loop dies (e.g. the stream was missing, or a
    # NATS outage outlived the subscription).
    state.sync_task = state.bus.supervise(
        "sync-worker",
        lambda js: run_sync_worker(state.bus.client, js, pool, state.sync_status),
    )
    logger.info("Sync worker scheduled (runs while NATS is connected)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - startup and shutdown."""
    # Startup — security check first
    check_production_security()
    logger.info(f"Starting {settings.service_name} v{__version__}...")

    # NATS connects in the background and heals itself, so a NATS that is
    # late (fresh k8s boot, node drain) or away for a while never blocks or
    # disables the service. /status and /health read the live state.
    state.bus = EventBus(settings.nats_url, name="reporting-sync", streams=[_events_stream()])
    await state.bus.start()

    # Search works with or without PostgreSQL; it gets the reader pool once
    # PostgreSQL is up.
    state.search_service = SearchService(state.read_pool)

    # PostgreSQL connects in the background and keeps trying until it is up:
    # a PostgreSQL that is late — or still "starting up" — must not leave the
    # service degraded until someone restarts it. Everything that needs it
    # (schema, batch sync, backfill, the sync worker) starts once it is up.
    state.postgres_task = asyncio.create_task(_bring_up_postgres())

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

    # Cancel the PostgreSQL connect task (if still trying)
    if state.postgres_task and not state.postgres_task.done():
        state.postgres_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await state.postgres_task

    if state.parity_task and not state.parity_task.done():
        state.parity_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await state.parity_task

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

    await state.bus.stop()
    logger.info("NATS event bus stopped")

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
# Process-alive probe (no I/O) — the k8s liveness and startup probes' target,
# kept apart from the dependency-checking health and readiness endpoints.
install_liveness(app)

# Setup rate limiting (reads WIP_RATE_LIMIT, default 40000/minute)
setup_rate_limiting(app)

router = APIRouter(
    prefix="/api/reporting-sync", dependencies=[Depends(require_api_key)]
)

# Operator and raw-SQL routes. wip-services is listed explicitly because it is
# not a superadmin group: service keys hold no namespace grants, so these
# routes are gated by group, never by namespace. Raw SQL from other keys
# (query, query-shaped export) is not gated here: it runs on a connection
# that can read only the caller's namespaces (_caller_read_connection).
_PRIVILEGED = Depends(require_groups(["wip-admins", "wip-services"]))


def _is_privileged(identity: UserIdentity) -> bool:
    return identity.has_group("wip-admins") or identity.has_group("wip-services")


# Every relation a view depends on outside its own schema, read from the
# view's rewrite rule in pg_depend: exact and independent of parsing the SQL.
# It does not see what a view's functions read (query_to_xml reads whatever
# its string names), so it is an early, readable refusal, not the boundary:
# the boundary is that non-privileged readers read views on their own scoped
# connection, and registered views are security_invoker.
_VIEW_FOREIGN_DEPENDENCIES_SQL = """
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
"""


def _is_namespace_schema(schema: str) -> bool:
    """A namespace's reporting schema is named after the namespace; the rest
    (public bookkeeping, the catalogues) holds platform tables, not any
    namespace's data."""
    return not (
        schema == "public" or schema == "information_schema" or schema.startswith("pg_")
    )


async def _unreadable_view_dependencies(
    identity: UserIdentity, foreign: list[Any],
) -> list[Any]:
    """The foreign dependencies of a view this caller may not read through it.

    The caller's own permissions must cover every namespace the view draws from:
    read on each foreign namespace schema, and a privileged key for platform
    tables. Raises 503 when a permission cannot be read.
    """
    if _is_privileged(identity):
        return []
    unreadable = []
    for dep in foreign:
        if not _is_namespace_schema(dep["schema"]):
            unreadable.append(dep)
            continue
        try:
            await check_namespace_permission(identity, dep["schema"], "read")
        except HTTPException as e:
            if e.status_code == 503:
                raise
            unreadable.append(dep)
    return unreadable


@contextlib.asynccontextmanager
async def _caller_read_connection(identity: UserIdentity) -> AsyncIterator[asyncpg.Connection]:
    """The connection a caller's SQL runs on.

    Privileged keys use the reader pool, which reads every schema. Every
    other key gets a connection that logs in as a role able to read only
    the namespaces the key can read (scoped_reads.py), so PostgreSQL itself
    refuses anything else — including reads made by functions, which no
    check on the SQL text can see.
    """
    if _is_privileged(identity):
        if not state.read_pool:
            raise HTTPException(status_code=503, detail="PostgreSQL not connected")
        async with state.read_pool.acquire() as conn:
            yield conn
        return
    if state.scoped_reads is None:
        raise HTTPException(
            status_code=503,
            detail="Reporting SQL for namespace-scoped keys is not configured on this install",
        )
    schemas = []
    for namespace in await resolve_accessible_namespaces(identity) or []:
        try:
            schemas.append(SchemaManager._safe_ident(namespace))
        except ValueError:
            continue
    if not schemas:
        raise HTTPException(status_code=403, detail="No accessible namespaces")
    try:
        async with state.scoped_reads.connection(schemas) as conn:
            yield conn
    except ScopedReadsUnavailable as e:
        logger.error("Scoped reporting read unavailable: %s", e)
        raise HTTPException(
            status_code=503, detail="Reporting SQL for namespace-scoped keys is unavailable",
        ) from e


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


@router.get("/health/events", dependencies=[_PRIVILEGED])
async def events_health() -> dict[str, Any]:
    """NATS event-bus state and counters, including the supervised sync
    worker's run state and restart count. Operator-only: its last error can
    name a subject, and subjects carry namespaces and entity IDs."""
    return state.bus.stats()


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

    Beyond document tables: every view registered for the namespace must
    exist (``views``), and with counts the term tables are checked
    (``derived_tables``) — active terms and term relations against
    def-store's active totals, name rows for every named term, and the
    closure's reflexive rows against what its rebuild writes. Failures
    count in ``derived_issues``; ``ok`` holds only when every check passes.

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


@router.get("/parity/status", dependencies=[_PRIVILEGED], response_model=ParityMonitorStatus)
async def reporting_parity_status() -> ParityMonitorStatus:
    """The standing parity check: its last pass per namespace and its
    recent repairs.

    Every PARITY_CHECK_INTERVAL_SECONDS (0 turns it off) the service runs
    the parity check over every active namespace. A gap seen on two
    consecutive passes is repaired with the narrowest sync that closes it —
    a template's batch sync, the term or term-relation sync, a closure
    rebuild, re-applying registered views — at most PARITY_REPAIR_ATTEMPTS
    times. A namespace with an active batch job is skipped for that pass.
    ``not_healing`` lists gaps that outlived their repairs or have no
    automatic repair (a legacy table, old-shape bookkeeping, a check that
    could not run); those are also logged once at error level.
    """
    if not state.parity_monitor:
        raise HTTPException(status_code=503, detail="PostgreSQL not connected")
    return state.parity_monitor.status()


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
            f'SELECT COUNT(*) FROM "{schema}"."{table_name}"'  # nosec B608  # identifiers validated by _safe_ident / ensure_*, values bound
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
        document_status: Filter documents by status ('active', 'inactive')
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
    # shown to unrestricted callers.
    if result.entity is not None:
        accessible = await resolve_accessible_namespaces(identity)
        if accessible is not None:
            owner = result.entity.namespace
            if owner is None:
                raise HTTPException(status_code=404, detail="Entity not found")
            await check_namespace_permission(identity, owner, "read")
            await _hide_unreadable_targets(result.entity, set(accessible))
    return result


async def _hide_unreadable_targets(entity: Any, readable: set[str]) -> None:
    """Report references into namespaces the caller cannot read as broken.

    The entity's own data carries each target's id, so the id stays. Its
    label, value and lifecycle state are read from the target and belong to
    a namespace the caller cannot read: they are dropped and the reference
    reads exactly like one whose target does not exist.
    """
    from wip_auth.registry_client import RegistryClientBase

    ids = sorted({ref.ref_id for ref in entity.references})
    if not ids:
        return
    try:
        placed = await RegistryClientBase().lookup_entry_namespaces(ids, raise_on_error=True)
    except (RegistryError, httpx.HTTPError) as e:
        raise HTTPException(
            status_code=503, detail=f"Cannot check where references point: {e}",
        ) from e
    for ref in entity.references:
        target_ns = placed.get(ref.ref_id)
        if not target_ns or target_ns == entity.namespace or target_ns in readable:
            continue
        if ref.status == "valid":
            entity.valid_refs -= 1
        elif ref.status == "inactive":
            entity.inactive_refs -= 1
        if ref.status != "broken":
            entity.broken_refs += 1
        ref.ref_value = None
        ref.ref_label = None
        ref.status = "broken"
        ref.error = f"{ref.ref_type.capitalize()} not found"


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
    allowed_exact = {"terminologies", "terms", "term_names", "term_relations", "term_closure", "templates"}
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
            """  # nosec B608  # SQL fragments are code constants, values bound
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

            count = await conn.fetchval(f'SELECT COUNT(*) FROM "{schema}"."{tname}"')  # nosec B608  # identifiers read from the PostgreSQL catalog, values bound

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


@router.post("/query")
async def execute_query(
    body: ReportQuery,
    identity: UserIdentity = Depends(require_api_key),
):
    """Execute a read-only SQL query against the reporting database.

    A wip-admins / wip-services key reads every schema. Any other key's
    query runs as a PostgreSQL role that can read only the namespaces the
    key can read; a relation outside them is refused by PostgreSQL.
    """
    if body.namespace and not _is_privileged(identity):
        await check_namespace_permission(identity, body.namespace, "read")
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
    wrapped_sql = f"SELECT * FROM ({body.sql}) _q LIMIT {body.max_rows + 1}"  # nosec B608  # caller SQL, run read-only as a role limited to what the caller may read

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
        for attempt in (1, 2):
            try:
                async with _caller_read_connection(identity) as conn:
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
_EXPORT_ALLOWED_EXACT = {"terminologies", "terms", "term_names", "term_relations", "term_closure", "templates"}


def _is_allowed_table(name: str) -> bool:
    return name.startswith(_EXPORT_ALLOWED_PREFIXES) or name in _EXPORT_ALLOWED_EXACT


class CsvExportQuery(StrictModel):
    """Request model for query-based CSV export."""

    sql: str
    params: list[Any] = Field(default_factory=list)
    timeout_seconds: int = Field(default=120, ge=1, le=600)
    filename: str = Field(default="export.csv", pattern=r"^[\w\-. ]+\.csv$")
    namespace: str | None = Field(
        default=None,
        description=(
            "When set, the query runs with search_path = this namespace's schema, "
            "so unqualified table names like `doc_patient` resolve there (same as "
            "POST /query). Omit for cross-namespace queries and schema-qualify instead."
        ),
    )


def _search_path_schema(namespace: str | None) -> str | None:
    """The namespace's schema name, validated as a safe identifier."""
    if not namespace:
        return None
    try:
        return SchemaManager(state.postgres_pool).schema_for(namespace)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


async def _check_export_sql(
    sql: str, search_path_schema: str | None, *, missing_relation_status: int = 400,
    identity: UserIdentity | None = None,
) -> None:
    """Prepare `sql` on a short-lived connection before the response starts.

    Once a StreamingResponse has sent its status line, a failure can only
    truncate the body — the caller sees 200 and an empty or partial CSV. A
    statement that does not parse, or names an unknown relation or column,
    is caught here instead and becomes an HTTP error. The streaming
    connection prepares again; holding this one across into the generator
    would leak it if the client disconnects before streaming begins.

    With an ``identity``, the statement is prepared on that caller's own
    connection (``_caller_read_connection``); without one, on the reader pool.
    """
    assert state.read_pool is not None
    connection = (
        _caller_read_connection(identity) if identity is not None
        else state.read_pool.acquire()
    )
    try:
        async with connection as conn, conn.transaction(readonly=True):
            if search_path_schema:
                await conn.execute(f'SET LOCAL search_path = "{search_path_schema}", public')
            await conn.prepare(sql)
    except asyncpg.UndefinedTableError as e:
        raise HTTPException(status_code=missing_relation_status, detail=f"Query error: {e}") from e
    except asyncpg.PostgresSyntaxError as e:
        raise HTTPException(status_code=400, detail=f"SQL syntax error: {e}") from e
    except asyncpg.PostgresError as e:
        raise HTTPException(status_code=400, detail=f"Query error: {e}") from e


async def _stream_csv(
    conn: asyncpg.Connection, sql: str, params: list[Any], search_path_schema: str | None = None,
):
    """Yield CSV rows from a PostgreSQL query using a cursor."""
    # One transaction for the whole stream: the cursor needs it, and SET LOCAL
    # keeps the search_path from leaking back into the pooled connection.
    async with conn.transaction():
        if search_path_schema:
            await conn.execute(f'SET LOCAL search_path = "{search_path_schema}", public')
        stmt = await conn.prepare(sql)
        columns = [attr.name for attr in stmt.get_attributes()]

        # Header row
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(columns)
        yield buf.getvalue()

        # Data rows — fetch in batches to limit memory
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
    await _check_export_sql(f"SELECT * FROM {qualified}", None, missing_relation_status=404)  # nosec B608  # identifiers validated by _safe_ident / ensure_*, values bound

    async def generate():
        assert state.read_pool is not None
        async with state.read_pool.acquire() as conn:
            await conn.execute(f"SET statement_timeout = {timeout_seconds * 1000}")
            await conn.execute("SET default_transaction_read_only = on")
            async for chunk in _stream_csv(conn, f'SELECT * FROM {qualified}', []):  # nosec B608  # identifiers validated by _safe_ident / ensure_*, values bound
                yield chunk

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{download_name}"'},
    )


@router.post("/export/csv")
async def export_query_csv(
    body: CsvExportQuery,
    identity: UserIdentity = Depends(require_api_key),
):
    """Export a SQL query result as streaming CSV.

    Scoped like POST /query: a key that is not wip-admins / wip-services
    reads only the namespaces it can read.
    """
    if body.namespace and not _is_privileged(identity):
        await check_namespace_permission(identity, body.namespace, "read")
    if not state.read_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not connected")

    if _DANGEROUS_SQL_RE.search(body.sql):
        raise HTTPException(
            status_code=400,
            detail="Only read-only queries are allowed. "
            "INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE, GRANT, and REVOKE are prohibited.",
        )

    search_path_schema = _search_path_schema(body.namespace)
    await _check_export_sql(body.sql, search_path_schema, identity=identity)

    async def generate():
        async with _caller_read_connection(identity) as conn:
            await conn.execute(
                f"SET statement_timeout = {body.timeout_seconds * 1000}"
            )
            await conn.execute("SET default_transaction_read_only = on")
            async for chunk in _stream_csv(conn, body.sql, body.params, search_path_schema):
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

    total_deleted = await _drop_namespace_reporting(sm, prefix)
    logger.info(f"Namespace {prefix} reporting schema dropped ({schema})")
    return {"namespace": prefix, "dropped_schema": schema, "total_deleted": total_deleted}


async def _drop_namespace_reporting(sm: SchemaManager, prefix: str) -> int:
    """Drop the namespace's schema (leaving its tombstone) and clear its rows
    from the shared bookkeeping tables. Returns the number of rows removed.

    The total MUST be a real integer: Registry records it as the deletion
    journal's `postgres_rows` audit field, which is int-typed — a null here
    poisoned the journal and turned every otherwise-successful namespace
    DELETE into a validation error. drop_namespace_schema counts the schema's
    rows before dropping it.
    """
    assert state.postgres_pool is not None
    total_deleted = await sm.drop_namespace_schema(prefix)
    async with state.postgres_pool.acquire() as conn:
        # The bookkeeping tables all live in public, keyed by namespace (see
        # init_postgres_schema). asyncpg returns a "DELETE <n>" status string;
        # fold those rows into the audit total.
        for table in ("_wip_schema_migrations", "_wip_sync_status", "_wip_views"):
            status = await conn.execute(
                f"DELETE FROM public.{table} WHERE namespace = $1", prefix  # nosec B608  # table names are a fixed tuple of internal tables, values bound
            )
            total_deleted += int(status.rsplit(" ", 1)[-1])
    return total_deleted


# Base tables reporting-sync creates in a namespace schema besides doc_*.
_NAMESPACE_METADATA_TABLES = frozenset({
    "templates", "terminologies", "terms", "term_names", "term_relations", "term_closure",
})


@router.post("/maintenance/orphan-schemas", dependencies=[_PRIVILEGED])
async def clean_orphan_schemas(dry_run: bool = True):
    """Find, and unless dry_run drop, reporting schemas of deleted namespaces.

    Events that arrived after a namespace deletion could re-create its schema
    before reporting-sync recorded deleted namespaces. A schema is an orphan
    when the Registry has no namespace of that name and every base table in
    it is one reporting-sync creates; a schema holding anything else is
    reported under `skipped`, never dropped. If the Registry cannot answer for
    any schema, nothing is dropped. Dropping writes the namespace's tombstone,
    as a namespace deletion does.
    """
    if not state.postgres_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not connected")

    async with state.postgres_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT n.nspname AS schema,
                   array_remove(array_agg(t.table_name::text), NULL) AS tables
            FROM pg_namespace n
            LEFT JOIN information_schema.tables t
              ON t.table_schema = n.nspname AND t.table_type = 'BASE TABLE'
            WHERE n.nspname NOT IN ('public', 'information_schema')
              AND n.nspname NOT LIKE 'pg\\_%'
            GROUP BY n.nspname
            ORDER BY n.nspname
            """
        )

    orphans: list[str] = []
    skipped: dict[str, str] = {}
    for row in rows:
        schema = row["schema"]
        try:
            exists = await registry_namespace_status(schema) is not None
        except RegistryError as e:
            raise HTTPException(
                status_code=502,
                detail=f"Registry could not answer for {schema!r}; nothing was dropped: {e}",
            ) from e
        if exists:
            continue
        foreign = sorted(
            t for t in row["tables"]
            if not t.startswith("doc_") and t not in _NAMESPACE_METADATA_TABLES
        )
        if foreign:
            skipped[schema] = f"holds tables reporting-sync does not create: {', '.join(foreign)}"
        else:
            orphans.append(schema)

    rows_dropped: dict[str, int] = {}
    if not dry_run:
        sm = SchemaManager(state.postgres_pool)
        for schema in orphans:
            rows_dropped[schema] = await _drop_namespace_reporting(sm, schema)
            logger.info(f"Dropped orphan reporting schema {schema!r}")

    return {"dry_run": dry_run, "orphans": orphans, "skipped": skipped, "rows_dropped": rows_dropped}


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
    schema-qualified. The view is created security_invoker, so it reads
    with its reader's privileges. The definition must be a single read-only SELECT:
    write/DDL keywords are rejected, and validation EXPLAINs the statement
    as a prepared subselect inside a read-only transaction, so a second
    ';'-separated statement is a hard parse error rather than something
    that executes. Platform relation names (``doc_*``, ``*__v<N>``,
    ``*__entities``, the metadata tables, ``_wip_*``) are reserved, and a
    name occupied by a relation this endpoint does not own is a 409.
    """
    privileged = _is_privileged(identity)
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
        # cleans it up. A deleted namespace gets no schema back.
        schema = await sm.ensure_schema(prefix)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except NamespaceDeletedError as e:
        raise HTTPException(status_code=404, detail=f"Namespace not found: {prefix}") from e

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
        # Planned on the caller's own connection, which a namespace-scoped
        # key's is limited to its namespaces: planning EVALUATES stable
        # functions while estimating (table_to_xml reads a table), and the
        # error returned below would carry what they read.
        if not state.read_pool:
            raise HTTPException(status_code=503, detail="PostgreSQL not connected")
        try:
            async with _caller_read_connection(identity) as read_conn, read_conn.transaction(
                readonly=True
            ):
                await read_conn.execute(f'SET LOCAL search_path = "{schema}", public')
                await read_conn.fetch(
                    f"EXPLAIN SELECT * FROM ({view_sql}) _wip_view_check"  # nosec B608  # caller SQL, planned read-only as a role limited to what the caller may read
                )
        except asyncpg.InsufficientPrivilegeError as e:
            # The same refusal the dependency check below gives, earlier.
            raise HTTPException(
                status_code=403,
                detail=f"View references relations this key cannot read ({e}).",
            ) from e
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
                    f'CREATE VIEW "{schema}"."{safe_name}" '
                    f"WITH ({REGISTERED_VIEW_OPTIONS}) AS {view_sql}"
                )
                if not privileged:
                    # A namespace-admin registration may draw only on
                    # namespaces its key can read: ask Postgres which
                    # relations the view it just created depends on, and roll
                    # the whole registration back if any lies in a namespace
                    # the key cannot read or in platform tables.
                    foreign = await conn.fetch(
                        _VIEW_FOREIGN_DEPENDENCIES_SQL, schema, safe_name,
                    )
                    unreadable = await _unreadable_view_dependencies(identity, foreign)
                    if unreadable:
                        names = ", ".join(
                            f"{r['schema']}.{r['relation']}" for r in unreadable
                        )
                        raise HTTPException(
                            status_code=403,
                            detail=(
                                "View references relations this key cannot read "
                                f"({names}). A namespace-admin key may register "
                                "views over its own namespace and namespaces it "
                                "can read; views over platform tables require a "
                                "wip-admins or wip-services key."
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

    This endpoint takes no caller SQL at all — the view by name, a closed
    operator set validated against the view's actual columns, bound
    parameters, sort, limit and offset. A non-privileged caller reads on a
    connection that can read only its own namespaces, and the view is
    security_invoker, so what the view's SQL reaches is limited the same
    way. Only views registered in this namespace are readable here; the
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
    not_found = HTTPException(
        status_code=404,
        detail=f"View {view_name!r} not found in namespace {prefix!r}",
    )
    if not registered:
        raise not_found
    # The caller must be able to read every namespace the view draws from,
    # not only the one it is registered in. Otherwise the view stays
    # invisible, as an unregistered one would, instead of failing on a
    # permission error that names the other namespace.
    if not _is_privileged(identity):
        async with state.postgres_pool.acquire() as conn:
            foreign = await conn.fetch(_VIEW_FOREIGN_DEPENDENCIES_SQL, schema, safe_name)
        if await _unreadable_view_dependencies(identity, foreign):
            raise not_found

    def _quoted(column: str) -> str:
        return '"' + column.replace('"', '""') + '"'

    async def _run(conn: asyncpg.Connection) -> tuple[list[str], list]:
        # Explicit (uncached) prepare, same reasoning as POST /query: the
        # view's own result description supplies the columns — also for the
        # column validation below — and a replace (DROP + CREATE) between
        # requests cannot leave a stale cached plan.
        base = f'SELECT * FROM "{schema}"."{safe_name}"'  # nosec B608  # identifiers validated by _safe_ident / ensure_*, values bound
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
                async with _caller_read_connection(identity) as conn:
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
        host="0.0.0.0",  # nosec B104  # a container must listen on every interface to be reachable
        port=8005,
        reload=settings.debug,
    )
