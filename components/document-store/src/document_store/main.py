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

from .models.document import Document
from .api import api_router
from .services.registry_client import configure_registry_client, get_registry_client
from .services.template_store_client import configure_template_store_client, get_template_store_client
from .services.def_store_client import configure_def_store_client, get_def_store_client


# Application configuration
class Settings:
    """Application settings loaded from environment."""

    MONGO_URI: str = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    DATABASE_NAME: str = os.getenv("DATABASE_NAME", "wip_document_store")
    API_KEY: str = os.getenv("API_KEY", "dev_master_key_for_testing")
    REGISTRY_URL: str = os.getenv("REGISTRY_URL", "http://localhost:8001")
    REGISTRY_API_KEY: str = os.getenv("REGISTRY_API_KEY", "dev_master_key_for_testing")
    TEMPLATE_STORE_URL: str = os.getenv("TEMPLATE_STORE_URL", "http://localhost:8003")
    TEMPLATE_STORE_API_KEY: str = os.getenv("TEMPLATE_STORE_API_KEY", "dev_master_key_for_testing")
    DEF_STORE_URL: str = os.getenv("DEF_STORE_URL", "http://localhost:8002")
    DEF_STORE_API_KEY: str = os.getenv("DEF_STORE_API_KEY", "dev_master_key_for_testing")
    CORS_ORIGINS: list[str] = os.getenv("CORS_ORIGINS", "*").split(",")


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
        document_models=[Document]
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

    yield

    # Shutdown
    print("Shutting down WIP Document Store Service...")
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
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

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
        "version": "0.1.0",
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
        mongo_status = f"error: {str(e)}"

    # Check Registry
    registry_client = get_registry_client()
    registry_status = "connected" if await registry_client.health_check() else "disconnected"

    # Check Template Store
    template_store_client = get_template_store_client()
    template_store_status = "connected" if await template_store_client.health_check() else "disconnected"

    # Check Def-Store
    def_store_client = get_def_store_client()
    def_store_status = "connected" if await def_store_client.health_check() else "disconnected"

    status = "healthy" if mongo_status == "connected" else "unhealthy"

    return {
        "status": status,
        "database": mongo_status,
        "registry": registry_status,
        "template_store": template_store_status,
        "def_store": def_store_status,
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
