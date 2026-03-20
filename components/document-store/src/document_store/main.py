"""
WIP Document Store Service - Main Application

A document storage and validation service for the World In a Pie system.
Validates documents against templates and manages document versioning.
"""

import os
from contextlib import asynccontextmanager

from beanie import init_beanie
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient

from wip_auth import RejectUnknownQueryParamsMiddleware, setup_auth

from .api import api_router
from .models.document import Document
from .models.file import File
from .services.def_store_client import configure_def_store_client, get_def_store_client
from .services.file_storage_client import (
    configure_file_storage_client,
    get_file_storage_client,
    is_file_storage_enabled,
)
from .services.integrity_service import IntegrityCheckResult, check_all_documents
from .services.nats_client import (
    close_nats_client,
    configure_nats_client,
    start_backpressure_monitor,
)
from .services.nats_client import (
    health_check as nats_health_check,
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
    CORS_ORIGINS: list[str] = os.getenv("CORS_ORIGINS", "*").split(",")
    # File storage settings (MinIO/S3)
    FILE_STORAGE_ENABLED: bool = os.getenv("WIP_FILE_STORAGE_ENABLED", "false").lower() == "true"
    FILE_STORAGE_ENDPOINT: str = os.getenv("WIP_FILE_STORAGE_ENDPOINT", "http://localhost:9000")
    FILE_STORAGE_ACCESS_KEY: str = os.getenv("WIP_FILE_STORAGE_ACCESS_KEY", "wip-minio-root")
    FILE_STORAGE_SECRET_KEY: str = os.getenv("WIP_FILE_STORAGE_SECRET_KEY", "wip-minio-password")
    FILE_STORAGE_BUCKET: str = os.getenv("WIP_FILE_STORAGE_BUCKET", "wip-attachments")


settings = Settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup and shutdown."""
    # Startup
    print("Starting WIP Document Store Service...")

    # Initialize MongoDB connection
    print(f"Connecting to MongoDB at {settings.MONGO_URI}...")
    client = AsyncIOMotorClient(settings.MONGO_URI)

    # Initialize Beanie ODM with document models
    await init_beanie(
        database=client[settings.DATABASE_NAME],
        document_models=[Document, File]
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

    # Configure NATS client (optional - for reporting sync)
    if settings.NATS_URL:
        nats_connected = await configure_nats_client(settings.NATS_URL)
        if nats_connected:
            print(f"NATS client connected to {settings.NATS_URL}")
            await start_backpressure_monitor(settings.NATS_URL)
        else:
            print("WARNING: NATS not available. Document events will not be published.")
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
            print("File storage (MinIO) is healthy.")
            # Ensure bucket exists
            try:
                await file_storage_client.ensure_bucket_exists()
                print(f"File storage bucket '{settings.FILE_STORAGE_BUCKET}' is ready.")
            except Exception as e:
                print(f"WARNING: Failed to ensure bucket exists: {e}")
        else:
            print("WARNING: File storage (MinIO) is not reachable. File uploads will not work.")
    else:
        print("File storage not enabled. Set WIP_FILE_STORAGE_ENABLED=true to enable.")

    # Run startup integrity check (non-blocking, log warnings only)
    # Only check template refs, skip term refs for faster startup
    import logging
    logger = logging.getLogger("document_store.startup")
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
            logger.warning(f"Integrity check found {len(result.issues)} issues:")
            # Group issues by type
            issue_counts = {}
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

    yield

    # Shutdown
    print("Shutting down WIP Document Store Service...")

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
        "service": "WIP Document Store",
        "version": "0.2.0",
        "documentation": "/docs",
        "health": "/health",
    }


# Health check endpoint
@app.get("/health", tags=["Health"])
async def health_check():
    """
    Health check endpoint.

    Verifies MongoDB, Registry, Template Store, and Def-Store connectivity.
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

    # Check Template Store
    template_store_client = get_template_store_client()
    template_store_status = "connected" if await template_store_client.health_check() else "disconnected"

    # Check Def-Store
    def_store_client = get_def_store_client()
    def_store_status = "connected" if await def_store_client.health_check() else "disconnected"

    # Check NATS (optional)
    nats_status = "connected" if await nats_health_check() else "disabled"

    # Check file storage (optional)
    if is_file_storage_enabled():
        file_storage_client = get_file_storage_client()
        file_storage_status = "connected" if await file_storage_client.health_check() else "error"
    else:
        file_storage_status = "disabled"

    status = "healthy" if mongo_status == "connected" else "unhealthy"

    return {
        "status": status,
        "database": mongo_status,
        "registry": registry_status,
        "template_store": template_store_status,
        "def_store": def_store_status,
        "nats": nats_status,
        "file_storage": file_storage_status,
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


# Debug endpoints for performance analysis
@app.get("/debug/timing", tags=["Debug"])
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


@app.post("/debug/timing/reset", tags=["Debug"])
async def reset_timing_stats():
    """Reset all timing statistics (creation and validation)."""
    from .services.document_service import DocumentService
    from .services.validation_service import ValidationService
    ValidationService.reset_timing_stats()
    DocumentService.reset_creation_timing_stats()
    return {"status": "reset"}


@app.get("/debug/cache", tags=["Debug"])
async def get_cache_stats():
    """
    Get cache statistics for Template Store and Def-Store clients.

    Shows cache size, hit rate, and configuration.
    - Template cache: Permanent (template_id is immutable)
    - Terminology cache: Caches complete terminologies for local term validation
    """
    template_client = get_template_store_client()
    def_store_client = get_def_store_client()

    template_stats = template_client.get_cache_stats()
    def_store_stats = def_store_client.get_cache_stats()

    # Calculate template hit rate
    template_total = template_stats["template_cache_hits"] + template_stats["template_cache_misses"]
    template_hit_rate = (template_stats["template_cache_hits"] / template_total * 100) if template_total > 0 else 0

    # Calculate terminology hit rate
    terminology_total = def_store_stats["terminology_cache_hits"] + def_store_stats["terminology_cache_misses"]
    terminology_hit_rate = (def_store_stats["terminology_cache_hits"] / terminology_total * 100) if terminology_total > 0 else 0

    return {
        "template_cache": {
            **template_stats,
            "hit_rate_percent": round(template_hit_rate, 1),
        },
        "terminology_cache": {
            **def_store_stats,
            "hit_rate_percent": round(terminology_hit_rate, 1),
        },
    }


@app.post("/debug/cache/clear", tags=["Debug"])
async def clear_caches():
    """Clear all caches (template and term validation)."""
    template_client = get_template_store_client()
    def_store_client = get_def_store_client()

    template_client.clear_cache()
    def_store_client.clear_cache()

    return {"status": "cleared"}


# Integrity check endpoint
@app.get("/health/integrity", tags=["Health"], response_model=IntegrityCheckResult)
async def integrity_check(
    status: str = None,
    template_id: str = None,
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
        status: Filter documents by status ('active', 'inactive', 'archived')
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
