"""
WIP Template Store Service - Main Application

A template schema management service for the World In a Pie system.
Provides document schema definitions with field validation and inheritance.
"""

import os
from contextlib import asynccontextmanager

from beanie import init_beanie
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient

from wip_auth import RejectUnknownQueryParamsMiddleware, setup_auth

from .api import api_router
from .models.template import Template
from .services.def_store_client import configure_def_store_client, get_def_store_client
from .services.integrity_service import IntegrityCheckResult, check_all_templates
from .services.nats_client import close_nats_client, configure_nats_client
from .services.nats_client import health_check as nats_health_check
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
    CORS_ORIGINS: list[str] = os.getenv("CORS_ORIGINS", "*").split(",")


settings = Settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup and shutdown."""
    # Startup
    print("Starting WIP Template Store Service...")

    # Initialize MongoDB connection
    print(f"Connecting to MongoDB at {settings.MONGO_URI}...")
    client = AsyncIOMotorClient(settings.MONGO_URI)

    # Initialize Beanie ODM with document models
    await init_beanie(
        database=client[settings.DATABASE_NAME],
        document_models=[Template]
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

    # Configure NATS client (optional - for reporting sync)
    if settings.NATS_URL:
        nats_connected = await configure_nats_client(settings.NATS_URL)
        if nats_connected:
            print(f"NATS client connected to {settings.NATS_URL}")
        else:
            print("WARNING: NATS not available. Template events will not be published.")
    else:
        print("NATS_URL not configured. Template event publishing disabled.")

    # Run startup integrity check (non-blocking, log warnings only)
    import logging
    logger = logging.getLogger("template_store.startup")
    try:
        logger.info("Running startup integrity check...")
        result = await check_all_templates(status_filter="active", limit=500)
        if result.status == "healthy":
            logger.info(f"Integrity check: OK ({result.summary.total_templates} templates checked)")
        else:
            logger.warning(f"Integrity check found {len(result.issues)} issues:")
            # Group issues by type
            issue_counts = {}
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

    yield

    # Shutdown
    print("Shutting down WIP Template Store Service...")

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

All endpoints require API key authentication via the `X-API-Key` header.

### Integration

- **Registry Service**: Templates are registered to get unique IDs (UUID by default, configurable per namespace)
- **Def-Store Service**: Terminology references can be validated
- **Namespace Isolation**: Multi-tenant data isolation
    """,
    version="0.2.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Setup authentication (reads from WIP_AUTH_* env vars, falls back to API_KEY)
setup_auth(app)

# Reject unknown query parameters (returns 422 for undeclared params)
app.add_middleware(RejectUnknownQueryParamsMiddleware)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API router
app.include_router(api_router)


# Root endpoint
@app.get("/", tags=["Health"])
async def root():
    """Root endpoint with service information."""
    return {
        "service": "WIP Template Store",
        "version": "0.2.0",
        "documentation": "/docs",
        "health": "/health",
    }


# Health check endpoint
@app.get("/health", tags=["Health"])
async def health_check():
    """
    Health check endpoint.

    Verifies MongoDB, Registry, and Def-Store connectivity.
    """
    try:
        # Ping MongoDB
        await app.state.mongodb_client.admin.command('ping')
        mongo_status = "connected"
    except Exception as e:
        mongo_status = f"error: {e!s}"

    # Check Registry
    registry_client = get_registry_client()
    registry_status = "connected" if await registry_client.health_check() else "disconnected"

    # Check Def-Store
    def_store_client = get_def_store_client()
    def_store_status = "connected" if await def_store_client.health_check() else "disconnected"

    # Check NATS (optional)
    nats_status = "connected" if await nats_health_check() else "disabled"

    status = "healthy" if mongo_status == "connected" else "unhealthy"

    return {
        "status": status,
        "database": mongo_status,
        "registry": registry_status,
        "def_store": def_store_status,
        "nats": nats_status,
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
        raise HTTPException(status_code=503, detail={"ready": False})


# Integrity check endpoint
@app.get("/health/integrity", tags=["Health"], response_model=IntegrityCheckResult)
async def integrity_check(
    status: str = None,
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
        IntegrityCheckResult with status, summary, and list of issues
    """
    return await check_all_templates(status_filter=status, limit=limit)
