"""
WIP Registry Service - Main Application

A federated identity management service for the World In a Pie system.
Provides composite key registration, synonym management, and cross-namespace search.
"""

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient

from wip_auth import (
    APIKeyProvider,
    build_metadata,
    check_production_security,
    declare_api_key_security,
    init_beanie_with_retry,
    install_liveness,
    setup_auth,
    setup_rate_limiting,
)
from wip_auth.mongo import mongo_credentials, mongo_identity

from . import __version__
from .api import api_router
from .api.api_keys import configure_api_key_management
from .models.api_key import StoredAPIKey
from .models.attachment import AppAttachment
from .models.composite_key_claim import CompositeKeyClaim
from .models.deletion_journal import DeletionJournal
from .models.entry import RegistryEntry
from .models.grant import NamespaceGrant
from .models.id_counter import IdCounter
from .models.namespace import Namespace
from .models.reservation import RuntimeReservation
from .services import key_import, key_shadowing
from .services.event_bus import start_event_bus, stop_event_bus
from .services.managed import ManagedRecordRefused, managed_record_refused_handler


# Application configuration
class Settings:
    """Application settings loaded from environment."""

    MONGO_URI: str = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    DATABASE_NAME: str = os.getenv("DATABASE_NAME", "wip_registry")
    MASTER_API_KEY: str | None = os.getenv("MASTER_API_KEY")
    AUTH_ENABLED: bool = os.getenv("AUTH_ENABLED", "true").lower() == "true"
    CORS_ORIGINS: list[str] = os.getenv("CORS_ORIGINS", "https://localhost:8443").split(",")
    NATS_URL: str = os.getenv("NATS_URL", "")


settings = Settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup and shutdown."""
    # Startup — security check first
    check_production_security()
    print("Starting WIP Registry Service...")

    # Initialize MongoDB connection
    print(f"Connecting to MongoDB at {settings.MONGO_URI} as {mongo_identity()}...")
    client: AsyncIOMotorClient = AsyncIOMotorClient(settings.MONGO_URI, **mongo_credentials())

    # Initialize Beanie ODM with retry — tolerates MongoDB not being ready
    # yet on fresh k8s boot, node drain, pod reschedule.
    await init_beanie_with_retry(
        database=client[settings.DATABASE_NAME],
        document_models=[Namespace, RegistryEntry, IdCounter, NamespaceGrant, DeletionJournal, StoredAPIKey, CompositeKeyClaim, RuntimeReservation, AppAttachment],
        description=f"MongoDB init ({settings.DATABASE_NAME})",
    )
    print("MongoDB connection and Beanie initialization successful.")

    # Store client in app state
    app.state.mongodb_client = client

    # Ensure the 'wip' system namespace exists with default grants
    existing_wip = await Namespace.find_one({"prefix": "wip"})
    if not existing_wip:
        from .services.namespace_identity import new_namespace_id
        wip_ns = Namespace(
            prefix="wip",
            namespace_id=new_namespace_id(),
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

    # NATS tells other services' caches when a namespace was deleted; without
    # it they expire on their TTL. Started before recovery, whose deletions
    # announce themselves. Connects in the background, never blocks startup.
    if await start_event_bus(settings.NATS_URL):
        print("NATS cache-invalidation bus started (connecting in background)")
    else:
        print("NATS_URL not configured: other services' caches expire on their TTL")

    # Resume incomplete namespace deletions in the background: off the
    # startup path (they call document-store and reporting-sync, which may not
    # be up yet), and retried until steps waiting on those services have run.
    from .api.namespace_deletion import get_deletion_service
    get_deletion_service().start_recovery()

    # Give namespaces and reservations stored before namespace ids their id,
    # and delete orphaned grants that have none (nothing says which namespace
    # they were made for). Before anything that creates or matches a namespace.
    from .services.namespace_identity import migrate_namespace_ids
    await migrate_namespace_ids()

    # Store each namespace's claimed `prefixed` ID prefixes and report the
    # ones it cannot claim (a prefix two namespaces already share) — configs
    # written before the uniqueness rule are reported, never rewritten.
    from .services.id_prefixes import backfill_id_prefixes
    try:
        await backfill_id_prefixes()
    except Exception as e:
        print(f"WARNING: Failed to check id_config prefixes: {e}")

    # Resolve stale pending composite-key claims (two-phase protocol):
    # pending older than the grace window are confirmed when their entry
    # backs them (the request died between insert and flip) or deleted when
    # not (it died before the insert). O(pending) via a partial index; never
    # touches young or confirmed claims, so it is safe beside live traffic —
    # unlike the full-collection scan, which is an explicit admin verb now
    # (admin_backfill_claims), together with the destructive backfill.
    from .services.claims import reconcile_pending_claims
    try:
        await reconcile_pending_claims()
    except Exception as e:
        print(f"WARNING: Failed to reconcile pending claims: {e}")

    # Attachments are served at their origin's root: records written while
    # they were served under /apps/<name> lose their prefix settings, once.
    from .models.attachment import drop_removed_fields
    try:
        if changed := await drop_removed_fields():
            print(f"Dropped the /apps/<name> prefix settings from {changed} attachment(s).")
    except Exception as e:
        print(f"WARNING: Failed to drop attachments' prefix settings: {e}")

    # CASE-568: authentication is enforced by the wip-auth middleware
    # (setup_auth below) — the legacy AuthService.initialize call was a
    # documented no-op and its "initialized" message overstated what
    # happened. Only the key's presence is worth reporting here.
    if settings.MASTER_API_KEY:
        print("MASTER_API_KEY present (auth enforced by wip-auth middleware).")
    else:
        print("WARNING: No MASTER_API_KEY set. Auth may not work correctly.")

    # Wire runtime API key management
    api_key_provider = None
    for p in providers:
        if isinstance(p, APIKeyProvider):
            api_key_provider = p
            break

    if api_key_provider:
        # Before importing or serving: the salt runtime keys are hashed
        # under, stored by the Registry for the stack (services/key_salt).
        from .services import key_salt
        api_key_provider.set_runtime_salt(await key_salt.ensure())

        # Before serving: import the install's key file and reservations
        # file into the Registry's records, so lookups never answer from an
        # import older than the files. A file that cannot be read imports
        # nothing and removes nothing.
        try:
            await key_import.run_pass(api_key_provider)
        except Exception as e:
            print(f"WARNING: Key import failed; the previous import stands: {e}")

        # Load runtime keys from MongoDB and add to provider. A managed key
        # the provider already holds as a config key of the same name stays
        # out (dormant): one plaintext, one record.
        config_names = api_key_provider.config_key_names()
        runtime_docs = [
            d for d in await StoredAPIKey.find(StoredAPIKey.enabled == True).to_list()  # noqa: E712
            if not key_import.is_dormant(d, config_names)
        ]
        for doc in runtime_docs:
            api_key_provider.add_key(key_shadowing.runtime_record(doc))
        print(f"Loaded {len(runtime_docs)} runtime API key(s) from MongoDB.")
        # A key stored under a name new keys may not take (made before the
        # rule) is also read as the key its name spells: it holds that key's
        # grants. Name it loudly so an operator revokes and re-creates it.
        import logging

        from .models.api_key import key_name_error

        for doc in await StoredAPIKey.find_all().to_list():
            name_error = key_name_error(doc.name)
            if name_error:
                logging.getLogger(__name__).critical(
                    "API key %r: %s. It is also read as that key; revoke it and "
                    "create it again under another name.", doc.name, name_error,
                )
        # A legacy SHA-256 hash is unsalted by bcrypt and fast to attack: the
        # salt is not secret, so only the key's own entropy protects it.
        for doc in runtime_docs:
            if not doc.key_hash.startswith(("$2b$", "$2a$", "$2y$")):
                print(
                    f"WARNING: API key '{doc.name}' is stored with a legacy (non-bcrypt) "
                    "hash; re-create it so it is hashed with bcrypt."
                )

        # Before serving: a runtime key sharing a config key's plaintext must
        # be out of the provider and of /api-keys/sync from the first request.
        shadowed = await key_shadowing.reconcile(api_key_provider)
        if shadowed:
            print(f"Withheld {len(shadowed)} runtime API key(s) shadowed by a config key.")
        shadow_task = asyncio.create_task(key_import.run_loop(api_key_provider))

        configure_api_key_management(api_key_provider)
    else:
        print("WARNING: No APIKeyProvider found — runtime key management disabled.")

    yield

    # Shutdown
    if api_key_provider:
        shadow_task.cancel()
    print("Shutting down WIP Registry Service...")
    await get_deletion_service().stop_recovery()
    await stop_event_bus()
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

All endpoints require API key authentication via the `X-API-Key` header,
except the public probe and docs routes (`/`, `/health`, `/ready`,
`/api/registry/health`, `/docs`, `/redoc`, `/openapi.json`).

Admin operations (namespace management) require elevated privileges.
    """,
    version="0.4.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Setup authentication (reads from WIP_AUTH_* env vars).
# public_paths exempts the api-prefixed /health route from auth so
# external monitors and stale-key clients can probe it without a 401
# (CASE-60). Root /health is already in the universal default set.
providers = setup_auth(app, public_paths=["/api/registry/health"])
# Process-alive probe (no I/O) — the k8s liveness and startup probes' target,
# kept apart from the dependency-checking health and readiness endpoints.
install_liveness(app)
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
# A write to a record wip-deploy owns: 409 with error_code managed_by_deployer.
app.add_exception_handler(ManagedRecordRefused, managed_record_refused_handler)

# Declare the X-API-Key requirement in the OpenAPI contract — runtime
# Depends() enforcement emits nothing into the schema, and a client
# generated from an auth-silent schema would not send a key.
declare_api_key_security(app)


# Root endpoint. The prefixed alias matters: Caddy preserves the
# /api/registry prefix on the way to this app, so the bare "/" is reachable
# only container-direct — without the alias, router-side consumers (the
# console's build-provenance probe at /api/registry/) get a 404 and the
# dashboard shows no build info for this service.
@app.get("/", tags=["Health"])
@app.get("/api/registry/", include_in_schema=False, tags=["Health"])
async def root():
    """Root endpoint with service information."""
    return {
        "service": "WIP Registry",
        "version": __version__,
        # Uniform build-provenance block (sha/built_at/image_tag).
        "build": build_metadata(__version__),
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
        ) from e


# Also expose /health under the api-prefix so external callers through
# Caddy can reach it (Caddy only routes /api/registry/*; root /health
# is unreachable from outside the container network). The root /health
# above stays for direct container probes (podman/k8s health checks).
app.add_api_route(
    "/api/registry/health", health_check, methods=["GET"], tags=["Health"]
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
    except Exception as e:
        raise HTTPException(status_code=503, detail={"ready": False}) from e
