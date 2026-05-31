"""Pytest configuration and fixtures for Template Store tests.

Uses transport injection to mount the real Registry in-process.
No mock registry, no mock resolution — all ID generation and synonym
resolution goes through the real Registry code via ASGITransport.

Def-Store client is still mocked (template-store validates terminology
references via Def-Store, which is a separate service).
"""

import os
import sys
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from beanie import init_beanie
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

# Add registry src to path for in-process mounting
_registry_src = str(Path(__file__).resolve().parents[2] / "registry" / "src")
if _registry_src not in sys.path:
    sys.path.insert(0, _registry_src)

# Use existing env vars if set, otherwise defaults for local testing
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017/")
os.environ.setdefault("DATABASE_NAME", "wip_template_store_test")
os.environ.setdefault("API_KEY", "test_api_key")
os.environ.setdefault("REGISTRY_URL", "http://registry")
os.environ.setdefault("REGISTRY_API_KEY", "test_api_key")
os.environ.setdefault("MASTER_API_KEY", "test_api_key")
os.environ.setdefault("AUTH_ENABLED", "true")
os.environ.setdefault("DEF_STORE_URL", "http://localhost:8002")
os.environ.setdefault("DEF_STORE_API_KEY", "test_def_store_key")

# Models and apps (must be after env var setup; sorted as one block for ruff I001)
from registry.main import app as registry_app  # noqa: E402
from registry.models.composite_key_claim import CompositeKeyClaim  # noqa: E402
from registry.models.deletion_journal import DeletionJournal  # noqa: E402
from registry.models.entry import RegistryEntry  # noqa: E402
from registry.models.grant import NamespaceGrant  # noqa: E402
from registry.models.id_counter import IdCounter  # noqa: E402
from registry.models.namespace import Namespace  # noqa: E402
from registry.services.auth import AuthService  # noqa: E402
from template_store.api.auth import set_api_key  # noqa: E402
from template_store.main import app  # noqa: E402
from template_store.models.template import Template  # noqa: E402
from template_store.services.def_store_client import DefStoreClient  # noqa: E402
from template_store.services.registry_client import RegistryClient  # noqa: E402
from wip_auth.resolve import clear_resolution_cache, set_resolve_transport  # noqa: E402

# Test terminologies that template fields reference
_TEST_TERMINOLOGIES = ("GENDER", "COUNTRY", "DOC_STATUS")


def _create_mock_def_store_client():
    """Create a mock Def-Store client for testing.

    Template-store validates terminology references via Def-Store.
    We mock this because Def-Store is a separate service not mounted
    in-process. The Registry handles ID generation and resolution;
    Def-Store handles terminology existence/validation.
    """
    mock_client = AsyncMock(spec=DefStoreClient)

    async def mock_terminology_exists(terminology_ref: str, namespace=None):
        # Accept any UUID-shaped ID (from real Registry) or known test names
        if len(terminology_ref) > 8 and "-" in terminology_ref:
            return True
        return terminology_ref in _TEST_TERMINOLOGIES

    async def mock_get_terminology(terminology_id=None, terminology_value=None, namespace=None):
        if terminology_id:
            return {"terminology_id": terminology_id, "status": "active"}
        if terminology_value in _TEST_TERMINOLOGIES:
            return {"terminology_id": f"TERM-{terminology_value}", "status": "active"}
        return None

    async def mock_validate_value(terminology_ref: str, value: str):
        return {"valid": True, "matched_term": {"term_id": "0190b000-0000-7000-0000-000000000001", "value": value}}

    async def mock_health_check():
        return True

    mock_client.terminology_exists = mock_terminology_exists
    mock_client.get_terminology = mock_get_terminology
    mock_client.validate_value = mock_validate_value
    mock_client.health_check = mock_health_check

    return mock_client


async def _register_test_terminologies(registry_transport):
    """Register test terminologies in Registry so resolution can find them.

    Mimics what def-store does: register entry + auto-synonym.
    """
    api_key = os.environ["MASTER_API_KEY"]
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

    async with AsyncClient(transport=registry_transport, base_url="http://registry") as client:
        for value in _TEST_TERMINOLOGIES:
            # Register entry
            resp = await client.post(
                "/api/registry/entries/register",
                headers=headers,
                json=[{
                    "namespace": "wip",
                    "entity_type": "terminologies",
                    "composite_key": {"value": value, "label": value},
                }],
            )
            entry_id = resp.json()["results"][0]["registry_id"]

            # Register auto-synonym for resolution
            await client.post(
                "/api/registry/synonyms/add",
                headers=headers,
                json=[{
                    "target_id": entry_id,
                    "synonym_namespace": "wip",
                    "synonym_entity_type": "terminologies",
                    "synonym_composite_key": {
                        "ns": "wip",
                        "type": "terminology",
                        "value": value,
                    },
                }],
            )


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
    """Create test client with real Registry mounted in-process."""
    mongo_uri = os.environ["MONGO_URI"]
    mongo_client = AsyncIOMotorClient(mongo_uri, serverSelectionTimeoutMS=5000)
    await _ensure_mongo_reachable(mongo_client, mongo_uri)
    test_db = mongo_client[os.environ["DATABASE_NAME"]]

    # Single init_beanie for all models — avoids database binding drift
    await init_beanie(
        database=test_db,
        document_models=[
            # Registry models
            Namespace, RegistryEntry, IdCounter, NamespaceGrant, DeletionJournal,
            CompositeKeyClaim,  # CASE-427: register_keys now claims keys here
            # Template-Store models
            Template,
        ],
    )

    # Clean all collections
    await RegistryEntry.delete_all()
    await Namespace.delete_all()
    await IdCounter.delete_all()
    await NamespaceGrant.delete_all()
    await DeletionJournal.delete_all()
    await CompositeKeyClaim.delete_all()
    await Template.delete_all()

    registry_app.state.mongodb_client = mongo_client
    AuthService.initialize(master_key=os.environ["MASTER_API_KEY"])

    # Create test namespaces
    for prefix in ("wip", "test-ns"):
        await Namespace(prefix=prefix, description=f"Test namespace: {prefix}").insert()

    # Mount Registry in-process
    registry_transport = ASGITransport(app=registry_app)

    # Register test terminologies so resolution works for template fields
    await _register_test_terminologies(registry_transport)

    app.state.mongodb_client = mongo_client
    set_api_key(os.environ["API_KEY"])

    # Wire real RegistryClient with module-level transport injection
    # (CASE-398: per-instance transport= kwarg is gone).
    from template_store.services.registry_client import set_registry_transport
    set_registry_transport(registry_transport)
    real_registry = RegistryClient(
        base_url="http://registry",
        api_key=os.environ["MASTER_API_KEY"],
    )

    # Wire real resolution with transport injection
    set_resolve_transport(registry_transport)
    clear_resolution_cache()

    # Mock Def-Store client (separate service, not mounted in-process)
    mock_def_store = _create_mock_def_store_client()

    # Patch singleton getters
    with (
        patch('template_store.services.template_service.get_registry_client', return_value=real_registry),
        patch('template_store.services.template_service.get_def_store_client', return_value=mock_def_store),
        patch('template_store.main.get_registry_client', return_value=real_registry),
        patch('template_store.main.get_def_store_client', return_value=mock_def_store),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    # Cleanup
    set_resolve_transport(None)
    from template_store.services.registry_client import clear_registry_transport
    clear_registry_transport()
    clear_resolution_cache()


@pytest.fixture
def api_key() -> str:
    """Return the test API key."""
    return os.environ["API_KEY"]


@pytest.fixture
def auth_headers(api_key: str) -> dict:
    """Return headers with API key for authenticated requests."""
    return {"X-API-Key": api_key}
