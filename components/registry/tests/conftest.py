"""Pytest configuration and fixtures for Registry tests."""

import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from beanie import init_beanie
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

# Use existing env vars if set, otherwise defaults for local testing
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017/")
os.environ.setdefault("DATABASE_NAME", "wip_registry_test")
# Under pytest-xdist every worker gets its own database — without it,
# workers wipe each other's fixtures mid-flight. Dropped at session end
# (_drop_worker_database).
_XDIST_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "")
if _XDIST_WORKER:
    os.environ["DATABASE_NAME"] = f"{os.environ['DATABASE_NAME']}_{_XDIST_WORKER}"
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault("AUTH_ENABLED", "true")

from registry.api.api_keys import configure_api_key_management
from registry.main import app
from registry.main import providers as _app_providers
from registry.models.api_key import StoredAPIKey
from registry.models.composite_key_claim import CompositeKeyClaim
from registry.models.deletion_journal import DeletionJournal
from registry.models.entry import RegistryEntry
from registry.models.grant import NamespaceGrant
from registry.models.id_counter import IdCounter
from registry.models.namespace import Namespace
from registry.services.auth import AuthService


async def _ensure_mongo_reachable(mongo_client: AsyncIOMotorClient, uri: str) -> None:
    """Fail fast with a clear error if MongoDB isn't reachable.

    motor's default behavior is retry-forever-with-backoff; before this
    check, an unreachable mongo (CASE-320) caused tests to hang silently
    with no diagnostic. The 5s serverSelectionTimeoutMS bound + explicit
    ping turns that into a clear, actionable error inside 5 seconds —
    regardless of whether pytest was invoked via wip-test.sh, an IDE,
    or directly.
    """
    from pymongo.errors import ServerSelectionTimeoutError

    try:
        await mongo_client.admin.command("ping")
    except ServerSelectionTimeoutError:
        raise RuntimeError(
            f"MongoDB at {uri} is not reachable within 5s. "
            f"Run scripts/wip-test.sh (auto-provisions test-mongo), "
            f"or set MONGO_URI to your own instance, or start test-mongo manually: "
            f"podman run -d --name test-mongo -p 27017:27017 mongo:7"
        ) from None


@pytest_asyncio.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Create an async HTTP client for testing the API."""
    mongo_uri = os.environ["MONGO_URI"]
    mongo_client = AsyncIOMotorClient(mongo_uri, serverSelectionTimeoutMS=5000)
    await _ensure_mongo_reachable(mongo_client, mongo_uri)
    await init_beanie(
        database=mongo_client[os.environ["DATABASE_NAME"]],
        document_models=[Namespace, RegistryEntry, IdCounter, NamespaceGrant, DeletionJournal, StoredAPIKey, CompositeKeyClaim]
    )

    # Clean up data from previous test
    await RegistryEntry.delete_all()
    await Namespace.delete_all()
    await IdCounter.delete_all()
    await DeletionJournal.delete_all()
    await StoredAPIKey.delete_all()
    await CompositeKeyClaim.delete_all()

    # Store client in app state (needed by health check)
    app.state.mongodb_client = mongo_client

    # Initialize auth service
    AuthService.initialize(master_key=os.environ["MASTER_API_KEY"])

    # Configure API key management using the SAME providers the middleware uses
    from wip_auth import APIKeyProvider
    for p in _app_providers:
        if isinstance(p, APIKeyProvider):
            configure_api_key_management(p)
            break

    # Create test namespaces
    await _create_test_namespaces()

    # Create test HTTP client
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _create_test_namespaces():
    """Create namespaces needed for tests."""
    namespaces = [
        Namespace(
            prefix="default",
            description="Default namespace for testing"
        ),
        Namespace(
            prefix="vendor1",
            description="Vendor 1 namespace for testing"
        ),
        Namespace(
            prefix="vendor2",
            description="Vendor 2 namespace for testing"
        ),
    ]

    for ns in namespaces:
        existing = await Namespace.find_one(Namespace.prefix == ns.prefix)
        if not existing:
            await ns.insert()


@pytest.fixture
def api_key() -> str:
    """Return the test API key."""
    return os.environ["MASTER_API_KEY"]


@pytest.fixture
def auth_headers(api_key: str) -> dict:
    """Return headers with API key for authenticated requests."""
    return {"X-API-Key": api_key}


@pytest.fixture(scope="session", autouse=True)
def _drop_worker_database():
    """Drop this xdist worker's suffixed database when its session ends,
    so per-worker databases don't accumulate on the test MongoDB."""
    yield
    if _XDIST_WORKER:
        from pymongo import MongoClient

        mongo_client = MongoClient(os.environ["MONGO_URI"], serverSelectionTimeoutMS=5000)
        try:
            mongo_client.drop_database(os.environ["DATABASE_NAME"])
        finally:
            mongo_client.close()
