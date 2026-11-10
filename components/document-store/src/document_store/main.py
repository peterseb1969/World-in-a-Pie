"""
WIP Document Store Service - Main Application

A document storage and validation service for the World In a Pie system.
Validates documents against templates and manages document versioning.
"""

import asyncio
import contextlib
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorClient

from wip_auth import (
    PRIVILEGED_GROUPS,
    HealthCache,
    RejectUnknownQueryParamsMiddleware,
    build_metadata,
    check_production_security,
    declare_api_key_security,
    init_beanie_with_retry,
    install_liveness,
    require_groups,
    setup_auth,
    setup_key_sync,
    setup_rate_limiting,
)
from wip_auth.mongo import mongo_client_options, mongo_identity, mongo_ping

from . import __version__
from .api import api_router
from .api.auth import require_api_key
from .models.backup_job import BackupJob
from .models.document import Document
from .models.file import File
from .models.restore_provenance import RestoreProvenance
from .services.def_store_client import configure_def_store_client, get_def_store_client
from .services.file_storage_client import (
    FileStorageUnavailableError,
    configure_file_storage_client,
    get_file_storage_client,
    is_file_storage_enabled,
)
from .services.integrity_service import IntegrityCheckResult, check_all_documents
from .services.nats_client import (
    close_nats_client,
    configure_nats_client,
    nats_stats,
    start_backpressure_monitor,
)
from .services.registry_client import configure_registry_client, get_registry_client
from .services.template_store_client import configure_template_store_client, get_template_store_client


# Application configuration
class Settings:
    """Application settings loaded from environment."""

    MONGO_URI: str = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    DATABASE_NAME: str = os.getenv("DATABASE_NAME", "wip_document_store")
    API_KEY: str = os.getenv("API_KEY", "dev_master_key_for_testing")
    REGISTRY_URL: str = os.getenv("REGISTRY_URL", "http://localhost:8001")
    REGISTRY_API_KEY: str = os.getenv("REGISTRY_API_KEY") or os.getenv("API_KEY") or "dev_master_key_for_testing"
    TEMPLATE_STORE_URL: str = os.getenv("TEMPLATE_STORE_URL", "http://localhost:8003")
    TEMPLATE_STORE_API_KEY: str = os.getenv("TEMPLATE_STORE_API_KEY") or os.getenv("API_KEY") or "dev_master_key_for_testing"
    DEF_STORE_URL: str = os.getenv("DEF_STORE_URL", "http://localhost:8002")
    DEF_STORE_API_KEY: str = os.getenv("DEF_STORE_API_KEY") or os.getenv("API_KEY") or "dev_master_key_for_testing"
    NATS_URL: str = os.getenv("NATS_URL", "")  # Empty = disabled
    CORS_ORIGINS: list[str] = os.getenv("CORS_ORIGINS", "https://localhost:8443").split(",")
    MAX_UPLOAD_SIZE: int = int(os.getenv("WIP_MAX_UPLOAD_SIZE", str(100 * 1024 * 1024)))  # 100MB default
    # File storage settings (S3-compatible object store)
    FILE_STORAGE_ENABLED: bool = os.getenv("WIP_FILE_STORAGE_ENABLED", "false").lower() == "true"
    FILE_STORAGE_ENDPOINT: str = os.getenv("WIP_FILE_STORAGE_ENDPOINT", "http://localhost:9000")
    FILE_STORAGE_ACCESS_KEY: str = os.getenv("WIP_FILE_STORAGE_ACCESS_KEY", "")
    FILE_STORAGE_SECRET_KEY: str = os.getenv("WIP_FILE_STORAGE_SECRET_KEY", "")
    FILE_STORAGE_BUCKET: str = os.getenv("WIP_FILE_STORAGE_BUCKET", "wip-attachments")


settings = Settings()


SUPERSEDED_DOCUMENT_INDEXES = (
    # {namespace, template_id, status, created_at desc}: replaced by
    # ns_template_status_time_doc_idx, which adds document_id (the list
    # sort's tiebreaker) and so serves every query the old index did.
    "ns_template_status_time_idx",
)


async def drop_superseded_document_indexes(db) -> None:
    """Drop document indexes a newer index has replaced.

    Runs after init_beanie has built the replacements, so a document query
    never loses its index. Best-effort: a fresh database never had them.
    """
    for name in SUPERSEDED_DOCUMENT_INDEXES:
        with contextlib.suppress(Exception):
            await db["documents"].drop_index(name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup and shutdown."""
    # Startup — security check first
    check_production_security()
    print("Starting WIP Document Store Service...")

    # Initialize MongoDB connection
    print(f"Connecting to MongoDB at {settings.MONGO_URI} as {mongo_identity()}...")
    client: AsyncIOMotorClient = AsyncIOMotorClient(settings.MONGO_URI, **mongo_client_options())

    # Initialize Beanie ODM with retry — tolerates MongoDB not being ready
    # yet on fresh k8s boot, node drain, pod reschedule.
    await init_beanie_with_retry(
        database=client[settings.DATABASE_NAME],
        document_models=[Document, File, BackupJob, RestoreProvenance],
        description=f"MongoDB init ({settings.DATABASE_NAME})",
    )
    print("MongoDB connection and Beanie initialization successful.")

    await drop_superseded_document_indexes(client[settings.DATABASE_NAME])

    # Store client in app state
    app.state.mongodb_client = client

    # Configure Registry client
    configure_registry_client(
        base_url=settings.REGISTRY_URL,
        api_key=settings.REGISTRY_API_KEY
    )
    print(f"Registry client configured for {settings.REGISTRY_URL}")

    # Configure Template Store client
    configure_template_store_client(
        base_url=settings.TEMPLATE_STORE_URL,
        api_key=settings.TEMPLATE_STORE_API_KEY
    )
    print(f"Template Store client configured for {settings.TEMPLATE_STORE_URL}")

    # Configure Def-Store client
    configure_def_store_client(
        base_url=settings.DEF_STORE_URL,
        api_key=settings.DEF_STORE_API_KEY
    )
    print(f"Def-Store client configured for {settings.DEF_STORE_URL}")

    # Check Registry health
    registry_client = get_registry_client()
    if await registry_client.health_check():
        print("Registry service is healthy.")
    else:
        print("WARNING: Registry service is not reachable. Some features may not work.")

    # Check Template Store health
    template_store_client = get_template_store_client()
    if await template_store_client.health_check():
        print("Template Store service is healthy.")
    else:
        print("WARNING: Template Store service is not reachable. Document validation may not work.")

    # Check Def-Store health
    def_store_client = get_def_store_client()
    if await def_store_client.health_check():
        print("Def-Store service is healthy.")
    else:
        print("WARNING: Def-Store service is not reachable. Terminology validation may not work.")

    # Configure NATS client (optional - for reporting sync). Connects in the
    # background and heals itself; events wait in a retry queue meanwhile.
    if await configure_nats_client(settings.NATS_URL):
        print(f"NATS event bus started for {settings.NATS_URL} (connecting in background)")
        await start_backpressure_monitor()
    else:
        print("NATS_URL not configured. Document event publishing disabled.")

    # Configure file storage client (optional - for binary file storage)
    if settings.FILE_STORAGE_ENABLED:
        configure_file_storage_client(
            endpoint_url=settings.FILE_STORAGE_ENDPOINT,
            access_key=settings.FILE_STORAGE_ACCESS_KEY,
            secret_key=settings.FILE_STORAGE_SECRET_KEY,
            bucket=settings.FILE_STORAGE_BUCKET,
        )
        print(f"File storage client configured for {settings.FILE_STORAGE_ENDPOINT}")

        # Check file storage health
        file_storage_client = get_file_storage_client()
        if await file_storage_client.health_check():
            print("File storage (object store) is healthy.")
            # File rows a fresh restore wrote with the source's storage_key.
            from .services.file_storage_keys import repair_storage_keys
            try:
                repaired, left = await repair_storage_keys(
                    File.get_motor_collection(), file_storage_client
                )
                if repaired or left:
                    print(
                        f"Re-pointed {repaired} file row(s) at their own objects; "
                        f"{len(left)} left as they are (no object under their own id)."
                    )
            except Exception as e:
                print(f"WARNING: file storage-key repair failed: {e}")
            # Ensure bucket exists
            try:
                await file_storage_client.ensure_bucket_exists()
                print(f"File storage bucket '{settings.FILE_STORAGE_BUCKET}' is ready.")
            except Exception as e:
                print(f"WARNING: Failed to ensure bucket exists: {e}")
        else:
            print("WARNING: File storage (object store) is not reachable. File uploads will not work.")
    else:
        print("File storage not enabled. Set WIP_FILE_STORAGE_ENABLED=true to enable.")

    # Reconcile orphaned backup/restore jobs — mark any job still "running"
    # or "pending" as failed. After a pod restart (OOMKill, node drain, crash)
    # no in-process worker will ever complete these; leaving them as "running"
    # makes the UI show a ghost job forever.
    from .models.backup_job import BackupJobStatus
    orphan_query = {"status": {"$in": [BackupJobStatus.RUNNING, BackupJobStatus.PENDING]}}
    orphan_count = await BackupJob.find(orphan_query).count()
    if orphan_count:
        from datetime import UTC, datetime
        await BackupJob.find(orphan_query).update_many({
            "$set": {
                "status": BackupJobStatus.FAILED,
                "error": "Process terminated during execution (pod restart)",
                "completed_at": datetime.now(UTC),
            }
        })
        print(f"Reconciled {orphan_count} orphaned backup/restore job(s) as failed.")

    # The `archived` document status was removed; rows still carrying it are
    # soft-deleted documents and become `inactive`. Before any read: the
    # value no longer parses as a DocumentStatus.
    from .services.archived_status import retire_archived_status
    retired = await retire_archived_status(Document.get_motor_collection())
    if retired:
        print(f"Retired the 'archived' status on {retired} document row(s) (now 'inactive').")

    # Startup integrity check, in the background: it counts every active
    # document and checks up to 500, which on a large instance takes longer
    # than a startup probe allows, and nothing waits on its result (it only
    # logs). Startup goes on; shutdown stops it if it is still running.
    import logging
    logger = logging.getLogger("document_store.startup")

    async def _startup_integrity_check() -> None:
        try:
            logger.info("Running startup integrity check...")
            result = await check_all_documents(
                status_filter="active",
                limit=500,
                check_term_refs=False  # Skip term refs for faster startup
            )
            if result.status == "healthy":
                logger.info(f"Integrity check: OK ({result.summary.total_documents} documents checked)")
            else:
                if result.summary.checks_not_run:
                    logger.warning(
                        "Integrity check %s: checks not run (a service did not answer): %s",
                        result.status, result.summary.checks_not_run,
                    )
                logger.warning(f"Integrity check found {len(result.issues)} issues:")
                # Group issues by type
                issue_counts: dict[str, int] = {}
                for issue in result.issues:
                    issue_counts[issue.type] = issue_counts.get(issue.type, 0) + 1
                for issue_type, count in issue_counts.items():
                    logger.warning(f"  - {issue_type}: {count}")
                # Log first few specific issues
                for issue in result.issues[:5]:
                    doc_id_short = issue.document_id[:12] if len(issue.document_id) > 12 else issue.document_id
                    logger.warning(f"  [{doc_id_short}...] {issue.field_path or 'template_id'}: {issue.message}")
                if len(result.issues) > 5:
                    logger.warning(f"  ... and {len(result.issues) - 5} more issues")
        except Exception as e:
            logger.warning(f"Startup integrity check failed: {e}")

    integrity_task = asyncio.create_task(_startup_integrity_check())

    # Files stored before allowed_templates held canonical template ids still
    # hold values; resolve them through the Registry, in the background (the
    # Registry may still be starting). A failure is logged and retried at the
    # next start: an unconverted file matches no template, so it stays
    # restricted rather than opening up.
    async def _convert_allowed_templates() -> None:
        from .services.allowed_templates_ids import convert_allowed_templates_to_ids
        try:
            summary = await convert_allowed_templates_to_ids()
            if summary["converted"] or summary["unresolved"]:
                logger.info(f"allowed_templates stored as template ids: {summary}")
        except Exception as e:
            logger.warning(f"Converting files' allowed_templates to template ids failed: {e}")

    allowed_templates_task = asyncio.create_task(_convert_allowed_templates())

    # Start key sync (picks up runtime API keys from Registry)
    key_sync = await setup_key_sync(
        _providers,
        registry_url=settings.REGISTRY_URL,
        api_key=settings.REGISTRY_API_KEY,
    )
    if key_sync:
        print("Key sync started (polling Registry for runtime API keys).")

    yield

    # Shutdown
    print("Shutting down WIP Document Store Service...")
    for task in (integrity_task, allowed_templates_task):
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    if key_sync:
        await key_sync.stop()

    # Close NATS connection
    await close_nats_client()

    app.state.mongodb_client.close()
    print("MongoDB connection closed.")


# Create FastAPI application
app = FastAPI(
    title="WIP Document Store Service",
    description="""
## World In a Pie - Document Storage and Validation

The Document Store service manages documents that conform to templates defined in the Template Store.

### Key Features

- **Document Storage**: Store and retrieve documents with template validation
- **Template Validation**: Validate documents against template schemas
- **Versioning**: Automatic document versioning with identity-based upsert
- **Identity Management**: SHA-256 identity hash based on template identity fields
- **Terminology Validation**: Validate term fields against Def-Store terminologies

### Document Lifecycle

1. Submit document with template_id and data
2. Fetch template from Template Store (with inheritance resolved)
3. Validate data against template fields and rules
4. Compute identity hash from identity fields
5. Create new document or new version if identity exists

### Authentication

All endpoints require API key authentication via the `X-API-Key` header.

### Integration

- **Registry Service**: Documents get unique IDs (UUID7)
- **Template Store Service**: Fetch templates for validation
- **Def-Store Service**: Validate term field values
    """,
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Setup authentication (reads from WIP_AUTH_* env vars, falls back to API_KEY).
# public_paths exempts the api-prefixed /health route from auth so external
# monitors and stale-key clients can probe it without a 401 (CASE-60).
_providers = setup_auth(app, public_paths=["/api/document-store/health"])
# Process-alive probe (no I/O) — the k8s liveness and startup probes' target,
# kept apart from the dependency-checking health and readiness endpoints.
install_liveness(app)

# Rate limiting (WIP_RATE_LIMIT) per client address on requests through the
# proxy; internal calls and probes are exempt — see wip_auth.ratelimit. After
# setup_auth so it runs before authentication, before CORS so preflights pass.
setup_rate_limiting(app)

# Reject unknown query parameters (returns 422 for undeclared params)
app.add_middleware(RejectUnknownQueryParamsMiddleware)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "X-API-Key", "Content-Type", "Accept"],
)

# Include API router
app.include_router(api_router)


@app.exception_handler(FileStorageUnavailableError)
async def _object_store_unavailable(_request: Request, exc: FileStorageUnavailableError) -> JSONResponse:
    """An unreachable object store is a dependency outage: 503, retryable.

    Raised from any route that touches storage; without this it escaped to
    the ASGI layer as a bare 500 with no detail.
    """
    import logging
    logging.getLogger("document_store.files").warning("Object store unreachable: %s", exc)
    return JSONResponse(status_code=503, content={"detail": str(exc)})


# Declare the X-API-Key requirement in the OpenAPI contract — the shared
# wip_auth helper replaces this service's original inline override so all
# five service schemas use one implementation and cannot drift.
declare_api_key_security(app)


# Root endpoint. The prefixed alias matters: Caddy preserves the
# /api/document-store prefix on the way to this app, so the bare "/" is
# reachable only container-direct — without the alias, router-side consumers
# (the console's build-provenance probe at /api/document-store/) get a 404
# and the dashboard shows no build info for this service.
@app.get("/", tags=["Health"])
@app.get("/api/document-store/", include_in_schema=False, tags=["Health"])
async def root():
    """Root endpoint with service information."""
    return {
        "service": "WIP Document Store",
        "version": __version__,
        # Uniform build-provenance block (sha/built_at/image_tag).
        "build": build_metadata(__version__),
        "documentation": "/docs",
        "health": "/health",
    }


# /health's dependency probes are cached for its timed callers — the
# image's HEALTHCHECK and peer services checking this one — which read only
# the status code. Orchestrators rendered by wip-deploy probe /ready and
# /livez, not this. The api-prefixed route below asks for a fresh value, so
# a human reading the body never sees a stale dependency.
# This is the widest fan-out in the platform: one uncached call opens eight
# outbound connections, counting the chains its peers run in turn.
_health_cache: HealthCache[dict[str, str]] = HealthCache()


async def _mongo_status() -> str:
    try:
        await mongo_ping(app.state.mongodb_client)
        return "connected"
    except Exception as e:
        import logging
        logging.getLogger("document_store.health").error("Health check failed: %s", e)
        return "error"


async def _file_storage_status() -> str:
    if not is_file_storage_enabled():
        return "disabled"
    return "connected" if await get_file_storage_client().health_check() else "error"


async def _probe_dependencies() -> dict[str, str]:
    """Derive the dependency half of the health response.

    Concurrent, not sequential: these checks are independent, and run one
    after another their timeouts ADD — two independent 5s DNS stalls once
    produced a single 10.272s response, against a 5s probe budget.
    """
    mongo, registry_ok, template_ok, def_ok, storage = await asyncio.gather(
        _mongo_status(),
        get_registry_client().health_check(),
        get_template_store_client().health_check(),
        get_def_store_client().health_check(),
        _file_storage_status(),
    )
    return {
        "database": mongo,
        "registry": "connected" if registry_ok else "disconnected",
        "template_store": "connected" if template_ok else "disconnected",
        "def_store": "connected" if def_ok else "disconnected",
        "file_storage": storage,
    }


# Health check endpoint
@app.get("/health", tags=["Health"])
async def health_check(fresh: bool = False):
    """
    Health check endpoint.

    Verifies MongoDB, Registry, Template Store, and Def-Store connectivity.
    Unhealthy (no MongoDB) answers 503 with the same body: a probe that
    reads only the status code (the image's HEALTHCHECK, curl -f) must not
    see 200. Orchestrators probe /ready and /livez instead.
    """
    deps = await _health_cache.get(_probe_dependencies, force=fresh)

    status = "healthy" if deps["database"] == "connected" else "unhealthy"

    body = {
        "status": status,
        **deps,
        # Live, not cached: the event bus's state is in-process and free to
        # read. One of connected / connecting / reconnecting / not_configured.
        "nats": nats_stats()["state"],
    }
    if status != "healthy":
        return JSONResponse(status_code=503, content=body)
    return body


async def health_check_fresh():
    """Uncached health for callers that read the body (CASE-808)."""
    return await health_check(fresh=True)


# Also expose /health under the api-prefix so external callers through
# Caddy can reach it. Root /health stays for direct container probes.
app.add_api_route(
    "/api/document-store/health", health_check_fresh, methods=["GET"], tags=["Health"]
)


@app.get(
    "/api/document-store/health/events",
    tags=["Health"],
    dependencies=[Depends(require_groups(PRIVILEGED_GROUPS))],
)
async def events_health():
    """NATS event-bus state and counters: published, queued for retry, and
    dropped (by reason). Dropped events are missing from reporting until a
    batch sync repairs them. Operator-only: its last error can name a
    subject, and subjects carry namespaces and entity IDs."""
    return nats_stats()


# Ready check endpoint (for Kubernetes)
@app.get("/ready", tags=["Health"])
async def ready_check():
    """
    Readiness check endpoint.

    Returns 200 when the service is ready to accept traffic.
    """
    try:
        await mongo_ping(app.state.mongodb_client)
        return {"ready": True}
    except Exception:
        raise HTTPException(status_code=503, detail={"ready": False}) from None


# Debug endpoints for performance analysis (gated behind auth — H7)
@app.get("/debug/timing", tags=["Debug"], dependencies=[Depends(require_api_key)])
async def get_timing_stats():
    """
    Get timing statistics for document creation and validation.

    Returns aggregated timing for:

    **Creation stages:**
    - 1_validation: Full validation pipeline
    - 2_find_existing: MongoDB query for existing identity
    - 3_create_new: Create new document (includes 3a + 3b)
    - 3a_registry_id: Registry HTTP call for document ID
    - 3b_mongo_insert: MongoDB insert
    - total: End-to-end creation time

    **Validation stages:**
    - 1_structural: Basic dict validation
    - 2_template_resolution: Fetch template (cached)
    - 3_field_validation: Field type and constraint checking
    - 4_term_validation: Term validation (local with cached terminologies)
    - 5_rule_evaluation: Cross-field rule checking
    - 6_identity_computation: Identity hash computation
    """
    from .services.document_service import DocumentService
    from .services.validation_service import ValidationService

    return {
        "creation": DocumentService.get_creation_timing_stats(),
        "validation": ValidationService.get_timing_stats(),
    }


@app.post("/debug/timing/reset", tags=["Debug"], dependencies=[Depends(require_api_key)])
async def reset_timing_stats():
    """Reset all timing statistics (creation and validation)."""
    from .services.document_service import DocumentService
    from .services.validation_service import ValidationService
    ValidationService.reset_timing_stats()
    DocumentService.reset_creation_timing_stats()
    return {"status": "reset"}


@app.get("/debug/cache", tags=["Debug"], dependencies=[Depends(require_api_key)])
async def get_cache_stats():
    """
    Get cache statistics for the Template Store client, and Def-Store's
    term-resolution counters.

    - Template cache: pinned versions kept, "latest" for a few seconds
    - Def-Store: nothing is cached; terms and terminologies are asked of
      Def-Store on every write
    """
    template_client = get_template_store_client()
    def_store_client = get_def_store_client()

    template_stats = template_client.get_cache_stats()
    def_store_stats = def_store_client.get_cache_stats()

    # Calculate template hit rate
    template_total = template_stats["template_cache_hits"] + template_stats["template_cache_misses"]
    template_hit_rate = (template_stats["template_cache_hits"] / template_total * 100) if template_total > 0 else 0

    return {
        "template_cache": {
            **template_stats,
            "hit_rate_percent": round(template_hit_rate, 1),
        },
        "def_store": def_store_stats,
    }


@app.post("/debug/cache/clear", tags=["Debug"], dependencies=[Depends(require_api_key)])
async def clear_caches():
    """Clear all caches (template and term validation)."""
    template_client = get_template_store_client()
    def_store_client = get_def_store_client()

    template_client.clear_cache()
    def_store_client.clear_cache()

    return {"status": "cleared"}


# Integrity check endpoint. Operator-only: it scans every namespace with
# the service's own access, so a caller without platform privileges must
# not reach it (its one in-platform caller, reporting-sync's aggregator,
# uses the install service key).
@app.get(
    "/health/integrity",
    tags=["Health"],
    response_model=IntegrityCheckResult,
    dependencies=[Depends(require_groups(PRIVILEGED_GROUPS))],
)
async def integrity_check(
    status: str | None = None,
    template_id: str | None = None,
    limit: int = 0,
    check_term_refs: bool = True,
    recent_first: bool = False
):
    """
    Check referential integrity of document references.

    Scans documents for:
    - Orphaned template references (referenced template not found)
    - Orphaned term references (term_references pointing to missing terms)
    - Inactive template references (referenced template is inactive)

    Args:
        status: Filter documents by status ('active', 'inactive')
        template_id: Filter documents by template_id
        limit: Maximum number of documents to check (0 = all, default: all)
        check_term_refs: Whether to check term references (default true, can be slow)
        recent_first: Check most recently created documents first (default false)

    Returns:
        IntegrityCheckResult with status, summary, and list of issues
    """
    return await check_all_documents(
        status_filter=status,
        template_id_filter=template_id,
        limit=limit,
        check_term_refs=check_term_refs,
        recent_first=recent_first
    )
