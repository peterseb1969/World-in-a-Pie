"""
WIP Template Store Service - Main Application

A template schema management service for the World In a Pie system.
Provides document schema definitions with field validation and inheritance.
"""

import asyncio
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
from .models.template import Template
from .services.def_store_client import DefStoreError, configure_def_store_client, get_def_store_client
from .services.errors import TemplateUnresolvable
from .services.integrity_service import IntegrityCheckResult, check_all_templates
from .services.nats_client import close_nats_client, configure_nats_client, nats_stats
from .services.registry_client import configure_registry_client, get_registry_client


# Application configuration
class Settings:
    """Application settings loaded from environment."""

    MONGO_URI: str = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    DATABASE_NAME: str = os.getenv("DATABASE_NAME", "wip_template_store")
    API_KEY: str = os.getenv("API_KEY", "dev_master_key_for_testing")
    REGISTRY_URL: str = os.getenv("REGISTRY_URL", "http://localhost:8001")
    REGISTRY_API_KEY: str = os.getenv("REGISTRY_API_KEY") or os.getenv("API_KEY") or "dev_master_key_for_testing"
    DEF_STORE_URL: str = os.getenv("DEF_STORE_URL", "http://localhost:8002")
    DEF_STORE_API_KEY: str = os.getenv("DEF_STORE_API_KEY") or os.getenv("API_KEY") or "dev_master_key_for_testing"
    NATS_URL: str = os.getenv("NATS_URL", "")  # Empty = disabled
    CORS_ORIGINS: list[str] = os.getenv("CORS_ORIGINS", "https://localhost:8443").split(",")


settings = Settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup and shutdown."""
    # Startup — security check first
    check_production_security()
    print("Starting WIP Template Store Service...")

    # Initialize MongoDB connection
    print(f"Connecting to MongoDB at {settings.MONGO_URI} as {mongo_identity()}...")
    client: AsyncIOMotorClient = AsyncIOMotorClient(settings.MONGO_URI, **mongo_client_options())

    # Initialize Beanie ODM with retry — tolerates MongoDB not being ready
    # yet on fresh k8s boot, node drain, pod reschedule.
    await init_beanie_with_retry(
        database=client[settings.DATABASE_NAME],
        document_models=[Template],
        description=f"MongoDB init ({settings.DATABASE_NAME})",
    )
    print("MongoDB connection and Beanie initialization successful.")

    # Store client in app state
    app.state.mongodb_client = client

    # Configure Registry client
    configure_registry_client(
        base_url=settings.REGISTRY_URL,
        api_key=settings.REGISTRY_API_KEY
    )
    print(f"Registry client configured for {settings.REGISTRY_URL}")

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
    else:
        print("NATS_URL not configured. Template event publishing disabled.")

    # Run startup integrity check (non-blocking, log warnings only)
    import logging
    logger = logging.getLogger("template_store.startup")
    try:
        logger.info("Running startup integrity check...")
        result = await check_all_templates(status_filter="active", limit=500)
        checked = f"{result.summary.total_templates} templates checked" + (
            ", more not checked (limit 500)" if result.summary.truncated else ""
        )
        if result.status == "healthy":
            logger.info(f"Integrity check: OK ({checked})")
        elif result.status == "unknown":
            logger.warning(
                f"Integrity check: UNKNOWN ({checked}; {result.summary.checks_skipped} "
                "reference(s) could not be checked, Def-Store did not answer)"
            )
        else:
            logger.warning(f"Integrity check found {len(result.issues)} issues:")
            # Group issues by type
            issue_counts: dict[str, int] = {}
            for issue in result.issues:
                issue_counts[issue.type] = issue_counts.get(issue.type, 0) + 1
            for issue_type, count in issue_counts.items():
                logger.warning(f"  - {issue_type}: {count}")
            # Log first few specific issues
            for issue in result.issues[:5]:
                logger.warning(f"  [{issue.template_value}] {issue.field_path or 'extends'}: {issue.message}")
            if len(result.issues) > 5:
                logger.warning(f"  ... and {len(result.issues) - 5} more issues")
    except Exception as e:
        logger.warning(f"Startup integrity check failed: {e}")

    # Versions stored before referent_identity_paths existed get it now. A
    # nested identity path cannot be hashed without it; nothing else changes.
    try:
        from .services.referent_paths import backfill as backfill_referent_paths
        stamped = await backfill_referent_paths()
        if stamped:
            logger.info(f"referent_identity_paths: filled in on {stamped} template version(s)")
    except Exception as e:
        logger.warning(f"referent_identity_paths backfill failed: {e}")

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
    print("Shutting down WIP Template Store Service...")
    if key_sync:
        await key_sync.stop()

    # Close NATS connection
    await close_nats_client()

    app.state.mongodb_client.close()
    print("MongoDB connection closed.")


# Create FastAPI application
app = FastAPI(
    title="WIP Template Store Service",
    description="""
## World In a Pie - Template Schema Management

The Template Store service manages document schemas (templates) for the WIP ecosystem.

### Key Features

- **Template Management**: Create and manage document schemas
- **Field Definitions**: Define fields with types, validation, and terminology references
- **Template Inheritance**: Templates can extend other templates
- **Cross-field Validation**: Define rules that validate across multiple fields
- **Reference Validation**: Validate terminology and template references

### Authentication

Every API endpoint requires authentication: an API key in the `X-API-Key`
header, or an OIDC bearer token where OIDC is configured. Anonymous by
design: the service root (`/`, `/api/template-store/`) and the health probes
(`/health`, `/api/template-store/health`, `/ready`, `/livez`).

### Integration

- **Registry Service**: Templates are registered to get unique IDs (UUID by default, configurable per namespace)
- **Def-Store Service**: Terminology references can be validated
- **Namespace Isolation**: Multi-tenant data isolation
    """,
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Setup authentication (reads from WIP_AUTH_* env vars, falls back to API_KEY).
# public_paths exempts the api-prefixed /health route from auth so external
# monitors and stale-key clients can probe it without a 401 (CASE-60).
_providers = setup_auth(app, public_paths=["/api/template-store/health"])
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


async def _def_store_unavailable(_request: Request, exc: DefStoreError) -> JSONResponse:
    """Def-Store did not answer and nothing could answer for it: the request
    fails visibly (503) rather than as a 500 or with a guessed answer."""
    return JSONResponse(status_code=503, content={"detail": f"Def-Store did not answer: {exc}"})


app.add_exception_handler(DefStoreError, _def_store_unavailable)  # type: ignore[arg-type]


@app.exception_handler(TemplateUnresolvable)
async def _template_unresolvable(_request: Request, exc: TemplateUnresolvable) -> JSONResponse:
    """A resolving read of a template whose inheritance chain is broken.

    409, not 404 (the template exists) and not 500 (the service is fine):
    the stored template is in a state that cannot be served resolved.
    """
    return JSONResponse(
        status_code=409,
        content={"detail": str(exc), "error_code": exc.code, "details": exc.details},
    )

# Declare the X-API-Key requirement in the OpenAPI contract — runtime
# Depends() enforcement emits nothing into the schema, and a client
# generated from an auth-silent schema would not send a key.
declare_api_key_security(app)


# Root endpoint. The prefixed alias matters: Caddy preserves the
# /api/template-store prefix on the way to this app, so the bare "/" is
# reachable only container-direct — without the alias, router-side consumers
# (the console's build-provenance probe at /api/template-store/) get a 404
# and the dashboard shows no build info for this service.
@app.get("/", tags=["Health"])
@app.get("/api/template-store/", include_in_schema=False, tags=["Health"])
async def root():
    """Root endpoint with service information."""
    return {
        "service": "WIP Template Store",
        "version": __version__,
        # Uniform build-provenance block (sha/built_at/image_tag).
        "build": build_metadata(__version__),
        "documentation": "/docs",
        "health": "/health",
    }


# Dependency probes are cached for the container-lifecycle probe path, which
# is hit every 10s (readiness) and 30s (liveness) but only ever has its HTTP
# status code read. The api-prefixed route below asks for a fresh value, so a
# human reading the body never sees a stale dependency.
_health_cache: HealthCache[dict[str, str]] = HealthCache()


async def _mongo_status() -> str:
    try:
        await mongo_ping(app.state.mongodb_client)
        return "connected"
    except Exception as e:
        import logging
        logging.getLogger("template_store.health").error("Health check failed: %s", e)
        return "error"


async def _probe_dependencies() -> dict[str, str]:
    """Derive the dependency half of the health response.

    Concurrent, not sequential: these checks are independent, and run one
    after another their timeouts ADD — two independent 5s DNS stalls once
    produced a single 10.272s response, against a 5s probe budget.
    """
    mongo, registry_ok, def_store_ok = await asyncio.gather(
        _mongo_status(),
        get_registry_client().health_check(),
        get_def_store_client().health_check(),
    )
    return {
        "database": mongo,
        "registry": "connected" if registry_ok else "disconnected",
        "def_store": "connected" if def_store_ok else "disconnected",
    }


# Health check endpoint
@app.get("/health", tags=["Health"])
async def health_check(fresh: bool = False):
    """
    Health check endpoint.

    Verifies MongoDB, Registry, and Def-Store connectivity. Unhealthy (no
    MongoDB) answers 503 with the same body: a probe that reads only the
    status code (the image's HEALTHCHECK, curl -f) must not see 200.
    Orchestrators probe /ready and /livez instead.
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
    "/api/template-store/health", health_check_fresh, methods=["GET"], tags=["Health"]
)


@app.get(
    "/api/template-store/health/events",
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
    except Exception as e:
        raise HTTPException(status_code=503, detail={"ready": False}) from e


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
    limit: int = 1000
):
    """
    Check referential integrity of template references.

    Scans all templates for:
    - Orphaned terminology references (referenced terminology not found)
    - Orphaned template references (extends, template_ref not found)
    - Inactive references (referenced entity is inactive)

    Args:
        status: Filter templates by status ('draft', 'active', 'inactive')
        limit: Maximum number of templates to check (default 1000)

    Returns:
        IntegrityCheckResult with status, summary, and list of issues. When a
        reference could not be checked (Def-Store did not answer) the answer
        is 503 with the same body, its checks_skipped listing them: a scan
        that could not look is not a healthy one.
    """
    result = await check_all_templates(status_filter=status, limit=limit)
    if result.checks_skipped:
        return JSONResponse(status_code=503, content=result.model_dump(mode="json"))
    return result
