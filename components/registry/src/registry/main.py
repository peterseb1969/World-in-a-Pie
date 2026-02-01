"""
WIP Registry Service - Main Application

A federated identity management service for the World In a Pie system.
Provides composite key registration, synonym management, and cross-namespace search.
"""

import os
from contextlib import asynccontextmanager

from beanie import init_beanie
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient

from .models.namespace import Namespace, IdGeneratorType
from .models.entry import RegistryEntry
from .api import api_router
from .services.auth import AuthService
from .services.id_generator import IdGeneratorService


# Application configuration
class Settings:
    """Application settings loaded from environment."""

    MONGO_URI: str = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    DATABASE_NAME: str = os.getenv("DATABASE_NAME", "wip_registry")
    MASTER_API_KEY: str | None = os.getenv("MASTER_API_KEY")
    AUTH_ENABLED: bool = os.getenv("AUTH_ENABLED", "true").lower() == "true"
    CORS_ORIGINS: list[str] = os.getenv("CORS_ORIGINS", "*").split(",")


settings = Settings()


async def initialize_prefixed_counters():
    """
    Initialize prefixed ID counters from existing entries in the database.

    This ensures that after a restart, new IDs don't collide with existing ones.
    """
    # Find all namespaces with prefixed generators
    namespaces = await Namespace.find(
        {"id_generator.type": IdGeneratorType.PREFIXED}
    ).to_list()

    for ns in namespaces:
        prefix = ns.id_generator.prefix or ""
        namespace_id = ns.namespace_id
        counter_key = f"{namespace_id}:{prefix}"

        # Find the highest entry_id for this namespace
        # Use find with sort and limit instead of aggregate
        result = await RegistryEntry.find(
            {"primary_namespace": namespace_id}
        ).sort("-entry_id").limit(1).to_list()

        if result:
            # Extract the number from the entry_id (e.g., "TPL-000026" -> 26)
            entry_id = result[0].entry_id
            try:
                # Remove prefix and parse number
                number_str = entry_id[len(prefix):]
                max_number = int(number_str)
                IdGeneratorService._counters[counter_key] = max_number
                print(f"Initialized counter for {namespace_id}: {entry_id} (counter={max_number})")
            except (ValueError, IndexError) as e:
                print(f"Warning: Could not parse entry_id {entry_id} for {namespace_id}: {e}")
        else:
            print(f"No existing entries for {namespace_id}, counter starts at 0")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup and shutdown."""
    # Startup
    print("Starting WIP Registry Service...")

    # Initialize MongoDB connection
    print(f"Connecting to MongoDB at {settings.MONGO_URI}...")
    client = AsyncIOMotorClient(settings.MONGO_URI)

    # Initialize Beanie ODM with document models
    await init_beanie(
        database=client[settings.DATABASE_NAME],
        document_models=[Namespace, RegistryEntry]
    )
    print("MongoDB connection and Beanie initialization successful.")

    # Store client in app state
    app.state.mongodb_client = client

    # Initialize auth service
    if settings.MASTER_API_KEY:
        AuthService.initialize(master_key=settings.MASTER_API_KEY)
        print("Auth service initialized with master key.")
    else:
        print("WARNING: No MASTER_API_KEY set. Auth may not work correctly.")

    # Initialize prefixed ID counters from existing entries
    await initialize_prefixed_counters()

    yield

    # Shutdown
    print("Shutting down WIP Registry Service...")
    app.state.mongodb_client.close()
    print("MongoDB connection closed.")


# Create FastAPI application
app = FastAPI(
    title="WIP Registry Service",
    description="""
## World In a Pie - Federated Identity Registry

The Registry service provides centralized identity management for the WIP ecosystem.

### Key Features

- **Namespace Management**: Logical partitions for ID isolation
- **Composite Key Registration**: Register any combination of fields as an identity
- **Synonym Support**: Multiple keys can resolve to the same entity
- **ID-as-Synonym (Merge)**: Resolve duplicate registrations
- **Cross-Namespace Search**: Find entities across all namespaces
- **Pluggable ID Generation**: UUID4, UUID7, NanoID, Prefixed, or Custom

### Authentication

All endpoints require API key authentication via the `X-API-Key` header.

Admin operations (namespace management) require elevated privileges.
    """,
    version="0.2.0",
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
        "service": "WIP Registry",
        "version": "0.2.0",
        "documentation": "/docs",
        "health": "/health",
    }


# Health check endpoint
@app.get("/health", tags=["Health"])
async def health_check():
    """
    Health check endpoint.

    Verifies MongoDB connectivity and returns service status.
    """
    try:
        # Ping MongoDB
        await app.state.mongodb_client.admin.command('ping')
        return {
            "status": "healthy",
            "database": "connected",
            "auth_enabled": settings.AUTH_ENABLED,
        }
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "unhealthy",
                "database": "disconnected",
                "error": str(e),
            }
        )


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
