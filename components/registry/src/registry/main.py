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

from wip_auth import APIKeyProvider, APIKeyRecord, check_production_security, setup_auth, setup_rate_limiting

from .api import api_router
from .api.api_keys import configure_api_key_management
from .models.api_key import StoredAPIKey
from .models.deletion_journal import DeletionJournal
from .models.entry import RegistryEntry
from .models.grant import NamespaceGrant
from .models.id_counter import IdCounter
from .models.namespace import Namespace
from .services.auth import AuthService


# Application configuration
class Settings:
    """Application settings loaded from environment."""

    MONGO_URI: str = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    DATABASE_NAME: str = os.getenv("DATABASE_NAME", "wip_registry")
    MASTER_API_KEY: str | None = os.getenv("MASTER_API_KEY")
    AUTH_ENABLED: bool = os.getenv("AUTH_ENABLED", "true").lower() == "true"
    CORS_ORIGINS: list[str] = os.getenv("CORS_ORIGINS", "https://localhost:8443").split(",")


settings = Settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup and shutdown."""
    # Startup — security check first
    check_production_security()
    print("Starting WIP Registry Service...")

    # Initialize MongoDB connection
    print(f"Connecting to MongoDB at {settings.MONGO_URI}...")
    client = AsyncIOMotorClient(settings.MONGO_URI)

    # Initialize Beanie ODM with document models
    await init_beanie(
        database=client[settings.DATABASE_NAME],
        document_models=[Namespace, RegistryEntry, IdCounter, NamespaceGrant, DeletionJournal, StoredAPIKey]
    )
    print("MongoDB connection and Beanie initialization successful.")

    # Store client in app state
    app.state.mongodb_client = client

    # Ensure the 'wip' system namespace exists with default grants
    from .models.grant import NamespaceGrant
    existing_wip = await Namespace.find_one({"prefix": "wip"})
    if not existing_wip:
        wip_ns = Namespace(
            prefix="wip",
            description="Default World In a Pie namespace",
            isolation_mode="open",
            id_config={},
        )
        await wip_ns.create()
        print("Created 'wip' system namespace.")
    else:
        print("'wip' system namespace exists.")

    # Ensure default grants exist for the wip namespace
    default_grants = [
        ("wip-admins", "group", "admin"),
        ("wip-editors", "group", "write"),
        ("wip-viewers", "group", "read"),
    ]
    for subject, subject_type, permission in default_grants:
        existing_grant = await NamespaceGrant.find_one({
            "namespace": "wip",
            "subject": subject,
            "subject_type": subject_type,
        })
        if not existing_grant:
            await NamespaceGrant(
                namespace="wip",
                subject=subject,
                subject_type=subject_type,
                permission=permission,
                granted_by="system:bootstrap",
            ).create()
            print(f"  Created grant: {subject} ({permission}) on 'wip'")

    # Recover any incomplete namespace deletions
    from .api.namespace_deletion import get_deletion_service
    try:
        await get_deletion_service().recover_incomplete_deletions()
    except Exception as e:
        print(f"WARNING: Failed to recover incomplete deletions: {e}")

    # Initialize auth service
    if settings.MASTER_API_KEY:
        AuthService.initialize(master_key=settings.MASTER_API_KEY)
        print("Auth service initialized with master key.")
    else:
        print("WARNING: No MASTER_API_KEY set. Auth may not work correctly.")

    # Wire runtime API key management
    api_key_provider = None
    for p in providers:
        if isinstance(p, APIKeyProvider):
            api_key_provider = p
            break

    if api_key_provider:
        # Identify config-file key names (these cannot be modified via API)
        config_key_names = {k.name for k in api_key_provider._keys}

        # Load runtime keys from MongoDB and add to provider
        runtime_docs = await StoredAPIKey.find(StoredAPIKey.enabled == True).to_list()  # noqa: E712
        for doc in runtime_docs:
            api_key_provider.add_key(APIKeyRecord(
                name=doc.name,
                key_hash=doc.key_hash,
                owner=doc.owner,
                groups=doc.groups,
                description=doc.description,
                created_at=doc.created_at,
                expires_at=doc.expires_at,
                enabled=doc.enabled,
                namespaces=doc.namespaces,
            ))
        print(f"Loaded {len(runtime_docs)} runtime API key(s) from MongoDB.")

        configure_api_key_management(api_key_provider, config_key_names)
    else:
        print("WARNING: No APIKeyProvider found — runtime key management disabled.")

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

- **Namespaces**: Manage entity namespaces with configurable ID algorithms
- **Composite Key Registration**: Register any combination of fields as an identity
- **Synonym Support**: Multiple keys can resolve to the same entity
- **ID-as-Synonym (Merge)**: Resolve duplicate registrations
- **Cross-Namespace Search**: Find entities across all namespaces
- **ID Provisioning**: Registry generates IDs per namespace config
- **ID Reservation**: Clients provide IDs, registry validates format
- **Pluggable ID Generation**: UUID4, UUID7, NanoID, Prefixed, or Pattern-based

### Authentication

All endpoints require API key authentication via the `X-API-Key` header.

Admin operations (namespace management) require elevated privileges.
    """,
    version="0.4.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Setup authentication (reads from WIP_AUTH_* env vars)
providers = setup_auth(app)
print(f"Auth setup complete. Providers: {[type(p).__name__ for p in providers]}")

# Setup rate limiting (reads WIP_RATE_LIMIT, default 40000/minute)
setup_rate_limiting(app)

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
        "service": "WIP Registry",
        "version": "0.4.0",
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
        import logging
        logging.getLogger("registry.health").error("Health check failed: %s", e)
        raise HTTPException(
            status_code=503,
            detail={
                "status": "unhealthy",
                "database": "disconnected",
                "error": "database connection failed",
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
