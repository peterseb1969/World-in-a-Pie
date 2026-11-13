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
    ADMIN_GROUP,
    APIKeyProvider,
    build_metadata,
    check_production_security,
    declare_api_key_security,
    get_auth_config,
    init_beanie_with_retry,
    install_liveness,
    require_service_keys,
    setup_auth,
    setup_rate_limiting,
)
from wip_auth.mongo import mongo_client_options, mongo_identity, mongo_ping

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
from .services.namespace_deletion import SERVICE_KEY_VARS


# Application configuration
class Settings:
    """Application settings loaded from environment."""

    MONGO_URI: str = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    DATABASE_NAME: str = os.getenv("DATABASE_NAME", "wip_registry")
    MASTER_API_KEY: str | None = os.getenv("MASTER_API_KEY")
    CORS_ORIGINS: list[str] = os.getenv("CORS_ORIGINS", "https://localhost:8443").split(",")
    NATS_URL: str = os.getenv("NATS_URL", "")


settings = Settings()


# Every key this service presents to another service, as the variable chains
# it reads them from: the service key namespace deletion presents to the
# stores and reporting-sync when it cleans up their data. The lifespan refuses
# to start while any is unset.
OUTBOUND_KEYS = (SERVICE_KEY_VARS,)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup and shutdown."""
    # Startup — security check first
    check_production_security()
    require_service_keys(OUTBOUND_KEYS, service="registry")
    print("Starting WIP Registry Service...")

    # Initialize MongoDB connection
    print(f"Connecting to MongoDB at {settings.MONGO_URI} as {mongo_identity()}...")
    client: AsyncIOMotorClient = AsyncIOMotorClient(settings.MONGO_URI, **mongo_client_options())

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

    await _ensure_wip_namespace()

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

    await _startup_maintenance()

    # CASE-568: authentication is enforced by the wip-auth middleware
    # (setup_auth below) — the legacy AuthService.initialize call was a
    # documented no-op and its "initialized" message overstated what
    # happened. Only the key's presence is worth reporting here.
    if settings.MASTER_API_KEY:
        print("MASTER_API_KEY present (auth enforced by wip-auth middleware).")
    else:
        print("WARNING: No MASTER_API_KEY set. Auth may not work correctly.")

    # Wire runtime API key management
    api_key_provider = next((p for p in providers if isinstance(p, APIKeyProvider)), None)
    if api_key_provider:
        shadow_task = await _start_key_management(api_key_provider)
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


async def _ensure_wip_namespace() -> None:
    """The 'wip' system namespace exists, with its default group grants."""
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
        (ADMIN_GROUP, "group", "admin"),
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



async def _startup_maintenance() -> None:
    """One-time and idempotent fix-ups of stored records, before anything
    creates or matches a namespace. A failure of the later ones is reported
    and startup goes on."""
    # Give namespaces and reservations stored before namespace ids their id,
    # and delete orphaned grants that have none (nothing says which namespace
    # they were made for). Before anything that creates or matches a namespace.
    from .services.namespace_identity import migrate_namespace_ids
    await migrate_namespace_ids()

    # Once per install: give runtime keys the read grants their namespace
    # lists used to stand in for (a key's list is a scope and gives nothing).
    from .services.key_scope_grants import backfill_scope_read_grants
    await backfill_scope_read_grants()

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

    # Drop the stored-object key batches of deletions whose journal was never
    # stored (an orphan nothing would ever read), and index them.
    from .services.namespace_deletion import sweep_key_batches
    try:
        if dropped := await sweep_key_batches():
            print(f"Dropped {dropped} orphaned namespace-deletion key batch(es).")
    except Exception as e:
        print(f"WARNING: Failed to sweep namespace-deletion key batches: {e}")

    # Attachments are served at their origin's root: records written while
    # they were served under /apps/<name> lose their prefix settings, once.
    from .models.attachment import drop_removed_fields
    try:
        if changed := await drop_removed_fields():
            print(f"Dropped the /apps/<name> prefix settings from {changed} attachment(s).")
    except Exception as e:
        print(f"WARNING: Failed to drop attachments' prefix settings: {e}")



async def _report_stored_keys_made_before_rules() -> None:
    """Name, loudly, the stored keys a rule made since then refuses; they
    are reported, never rewritten.

    A key stored under a name new keys may not take is also read as the key
    its name spells: it holds that key's grants. A key in a privileged group
    that also carries a namespaces list is scoped by the stores and not by
    the Registry's platform doors."""
    import logging

    from .models.api_key import key_name_error

    log = logging.getLogger(__name__)
    for doc in await StoredAPIKey.find_all().to_list():
        name_error = key_name_error(doc.name)
        if name_error:
            log.critical(
                "API key %r: %s. It is also read as that key; revoke it and "
                "create it again under another name.", doc.name, name_error,
            )
    for name in await key_import.scoped_privileged_keys():
        log.critical(
            "API key %r is in a privileged group and carries a namespaces list; "
            "a privileged key is never scoped, and the services disagree about "
            "this one. Drop the group (PATCH groups), or revoke it and create it "
            "again without the list.", name,
        )


async def _start_key_management(api_key_provider: APIKeyProvider) -> asyncio.Task:
    """Set the runtime salt, import the key file, load the runtime keys into
    the provider (warning about names and hashes that need attention),
    withhold shadowed keys, and start the import loop. Returns the loop's
    task."""
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
    await _report_stored_keys_made_before_rules()
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

    return shadow_task


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
- **Pluggable ID Generation**: UUID7, UUID4, Prefixed or NanoID minted by the
  Registry; Pattern and Any validate IDs the caller supplies

### Authentication

All endpoints require API key authentication via the `X-API-Key` header,
except the public probe and docs routes (`/`, `/health`, `/ready`, `/livez`,
`/api/registry/health`, `/docs`, `/redoc`, `/openapi.json`).

Admin operations (namespace management) require elevated privileges.
    """,
    version=__version__,
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

# Rate limiting (WIP_RATE_LIMIT) per client address on requests through the
# proxy; internal calls and probes are exempt — see wip_auth.ratelimit. After
# setup_auth so it runs before authentication, before CORS so preflights pass.
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
        await mongo_ping(app.state.mongodb_client)
        from .services.key_import import key_file_state

        return {
            "status": "healthy",
            "database": "connected",
            # The mode the auth layer actually enforces (WIP_AUTH_MODE).
            "auth_enabled": get_auth_config().mode != "none",
            # What the last pass did with the install's key file: imported,
            # refused, failed, not_configured or pending. Anything but
            # "imported" means a change in the file has not taken effect,
            # while the previous import keeps serving, so the Registry stays
            # healthy. The log says why.
            "key_file_import": key_file_state(),
            # Stored keys in a privileged group that carry a namespaces list,
            # a combination the API refuses; empty when there are none.
            "scoped_privileged_keys": await key_import.scoped_privileged_keys(),
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
        await mongo_ping(app.state.mongodb_client)
        return {"ready": True}
    except Exception as e:
        raise HTTPException(status_code=503, detail={"ready": False}) from e
