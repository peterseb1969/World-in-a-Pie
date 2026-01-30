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

from .models.template import Template
from .api import api_router
from .services.registry_client import configure_registry_client, get_registry_client
from .services.def_store_client import configure_def_store_client, get_def_store_client
from .services.nats_client import configure_nats_client, close_nats_client, health_check as nats_health_check


# Application configuration
class Settings:
    """Application settings loaded from environment."""

    MONGO_URI: str = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    DATABASE_NAME: str = os.getenv("DATABASE_NAME", "wip_template_store")
    API_KEY: str = os.getenv("API_KEY", "dev_master_key_for_testing")
    REGISTRY_URL: str = os.getenv("REGISTRY_URL", "http://localhost:8001")
    REGISTRY_API_KEY: str = os.getenv("REGISTRY_API_KEY", "dev_master_key_for_testing")
    DEF_STORE_URL: str = os.getenv("DEF_STORE_URL", "http://localhost:8002")
    DEF_STORE_API_KEY: str = os.getenv("DEF_STORE_API_KEY", "dev_master_key_for_testing")
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

- **Registry Service**: Templates are registered to get unique IDs (TPL-XXXXXX)
- **Def-Store Service**: Terminology references can be validated
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
        "service": "WIP Template Store",
        "version": "0.1.0",
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
        mongo_status = f"error: {str(e)}"

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
