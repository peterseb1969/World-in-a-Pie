"""
WIP Def-Store Service - Main Application

A terminology and ontology management service for the World In a Pie system.
Provides controlled vocabulary management with import/export capabilities.
"""

import asyncio
import contextlib
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient

from wip_auth import (
    RejectUnknownQueryParamsMiddleware,
    check_production_security,
    init_beanie_with_retry,
    retry_async,
    setup_auth,
    setup_key_sync,
    setup_rate_limiting,
)

from .api import api_router
from .models.audit_log import TermAuditLog
from .models.term import Term
from .models.term_relationship import TermRelationship
from .models.terminology import Terminology
from .services.nats_client import close_nats_client, configure_nats_client
from .services.registry_client import configure_registry_client, get_registry_client
from .services.system_terminologies import ensure_system_terminologies


# Application configuration
class Settings:
    """Application settings loaded from environment."""

    MONGO_URI: str = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    DATABASE_NAME: str = os.getenv("DATABASE_NAME", "wip_def_store")
    API_KEY: str = os.getenv("API_KEY", "dev_master_key_for_testing")
    REGISTRY_URL: str = os.getenv("REGISTRY_URL", "http://localhost:8001")
    REGISTRY_API_KEY: str = os.getenv("REGISTRY_API_KEY") or os.getenv("API_KEY") or "dev_master_key_for_testing"
    NATS_URL: str = os.getenv("NATS_URL", "")
    CORS_ORIGINS: list[str] = os.getenv("CORS_ORIGINS", "https://localhost:8443").split(",")


settings = Settings()


async def _bootstrap_system_terminologies() -> None:
    """Background bootstrap of system terminologies.

    Runs as a background task — waits for Registry to be healthy, then
    creates the built-in system terminologies (_TIME_UNITS,
    _ONTOLOGY_RELATIONSHIP_TYPES). Off the critical path so def-store's
    HTTP listener doesn't block on Registry startup. If Registry never
    becomes healthy, logs a warning and gives up; the terminologies can
    be recreated by restarting def-store or hitting ensure_system_terminologies
    via a future admin endpoint.
    """
    registry_client = get_registry_client()

    async def _verify_registry_ready() -> None:
        if not await registry_client.health_check():
            raise ConnectionError("Registry health check returned non-200")

    try:
        await retry_async(
            _verify_registry_ready,
            retry_on=(ConnectionError,),
            description="Registry health check (bootstrap)",
        )
        print("[bootstrap] Registry service is healthy.")
    except TimeoutError as e:
        print(f"[bootstrap] WARNING: Registry never became healthy: {e}")
        print("[bootstrap] System terminologies not bootstrapped.")
        return

    print("[bootstrap] Ensuring system terminologies exist...")
    try:
        result = await ensure_system_terminologies()
        if result["errors"]:
            for err in result["errors"]:
                print(f"[bootstrap]   WARNING: {err}")
        print(
            f"[bootstrap] System terminologies: {result['terminologies_created']} created, "
            f"{result['terminologies_existed']} existed, "
            f"{result['terms_created']} terms created, "
            f"{result['terms_existed']} terms existed"
        )
    except Exception as e:
        print(f"[bootstrap] WARNING: Failed to bootstrap system terminologies: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup and shutdown."""
    # Startup — security check first
    check_production_security()
    print("Starting WIP Def-Store Service...")

    # Initialize MongoDB connection
    print(f"Connecting to MongoDB at {settings.MONGO_URI}...")
    client = AsyncIOMotorClient(settings.MONGO_URI)

    # Initialize Beanie ODM with retry — tolerates MongoDB not being ready
    # yet on fresh k8s boot, node drain, pod reschedule.
    await init_beanie_with_retry(
        database=client[settings.DATABASE_NAME],
        document_models=[Terminology, Term, TermAuditLog, TermRelationship],
        description=f"MongoDB init ({settings.DATABASE_NAME})",
    )
    print("MongoDB connection and Beanie initialization successful.")

    # Store client in app state
    app.state.mongodb_client = client

    # Configure Registry client (no network call — just stores URL/key).
    configure_registry_client(
        base_url=settings.REGISTRY_URL,
        api_key=settings.REGISTRY_API_KEY
    )
    print(f"Registry client configured for {settings.REGISTRY_URL}")

    # Configure NATS client (optional — for event publishing to reporting-sync)
    if settings.NATS_URL:
        nats_ok = await configure_nats_client(settings.NATS_URL)
        print(f"NATS client: {'connected' if nats_ok else 'failed'} ({settings.NATS_URL})")
    else:
        print("NATS URL not configured, event publishing disabled")

    # Background bootstrap: wait for Registry to be healthy, then run
    # ensure_system_terminologies. Off the critical path so the HTTP
    # listener becomes Ready as soon as Mongo init completes. Callers
    # (e.g., reporting-sync's startup metadata sync) can then reach
    # def-store immediately; system-terminology creation catches up
    # once Registry is available.
    app.state.bootstrap_task = asyncio.create_task(
        _bootstrap_system_terminologies()
    )
    print("System-terminology bootstrap scheduled (background).")

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
    print("Shutting down WIP Def-Store Service...")

    # Cancel the background bootstrap task if it's still running
    bootstrap_task = getattr(app.state, "bootstrap_task", None)
    if bootstrap_task and not bootstrap_task.done():
        bootstrap_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await bootstrap_task

    if key_sync:
        await key_sync.stop()
    await close_nats_client()
    app.state.mongodb_client.close()
    print("MongoDB connection closed.")


# Create FastAPI application
app = FastAPI(
    title="WIP Def-Store Service",
    description="""
## World In a Pie - Terminology & Ontology Management

The Def-Store service manages controlled vocabularies (terminologies) for the WIP ecosystem.

### Key Features

- **Terminology Management**: Create and manage controlled vocabularies
- **Term Management**: Add, update, deprecate terms within terminologies
- **Validation API**: Validate values against terminologies
- **Import/Export**: JSON and CSV support for bulk operations
- **Multi-language**: Translation support for internationalization
- **Hierarchical Terms**: Support for parent-child relationships
- **Namespace Isolation**: Multi-tenant data isolation

### Authentication

All endpoints require API key authentication via the `X-API-Key` header.

### Integration with Registry

Terminologies and terms are registered with the WIP Registry service to get
unique, system-wide identifiers. IDs are configurable per namespace (default: UUID7).
    """,
    version="0.2.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Setup authentication (reads from WIP_AUTH_* env vars, falls back to API_KEY)
_providers = setup_auth(app)

# Setup rate limiting (reads WIP_RATE_LIMIT, default 40000/minute)
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


# Root endpoint
@app.get("/", tags=["Health"])
async def root():
    """Root endpoint with service information."""
    return {
        "service": "WIP Def-Store",
        "version": "0.2.0",
        "documentation": "/docs",
        "health": "/health",
    }


# Health check endpoint
@app.get("/health", tags=["Health"])
async def health_check():
    """
    Health check endpoint.

    Verifies MongoDB and Registry connectivity.
    """
    try:
        # Ping MongoDB
        await app.state.mongodb_client.admin.command('ping')
        mongo_status = "connected"
    except Exception as e:
        import logging
        logging.getLogger("def_store.health").error("Health check failed: %s", e)
        mongo_status = "error"

    # Check Registry
    registry_client = get_registry_client()
    registry_status = "connected" if await registry_client.health_check() else "disconnected"

    status = "healthy" if mongo_status == "connected" else "unhealthy"

    return {
        "status": status,
        "database": mongo_status,
        "registry": registry_status,
    }


# Ready check endpoint (for Kubernetes)
@app.get("/ready", tags=["Health"])
async def ready_check():
    """
    Readiness check endpoint.

    Returns 200 when the service is ready to accept traffic.
    """
    try:
        await app.state.mongodb_client.admin.command('ping')
        return {"ready": True}
    except Exception:
        raise HTTPException(status_code=503, detail={"ready": False}) from None
