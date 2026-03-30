"""Pytest configuration and fixtures for Def-Store tests."""

import os
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from beanie import init_beanie
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

# Use existing env vars if set, otherwise defaults for local testing
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017/")
os.environ.setdefault("DATABASE_NAME", "wip_def_store_test")
os.environ.setdefault("API_KEY", "test_api_key")
os.environ.setdefault("REGISTRY_URL", "http://localhost:8001")
os.environ.setdefault("REGISTRY_API_KEY", "test_registry_key")

from def_store.api.auth import set_api_key
from def_store.main import app
from def_store.models.audit_log import TermAuditLog
from def_store.models.term import Term
from def_store.models.term_relationship import TermRelationship
from def_store.models.terminology import Terminology
from def_store.services.registry_client import RegistryClient

# Counter for generating mock IDs
_term_counter = 0
_terminology_counter = 0


def _reset_counters():
    """Reset ID counters for a new test."""
    global _term_counter, _terminology_counter
    _term_counter = 0
    _terminology_counter = 0


def create_mock_registry_client():
    """Create a mock registry client for testing."""
    global _term_counter, _terminology_counter

    mock_client = AsyncMock(spec=RegistryClient)

    async def mock_register_terminology(value: str, label: str, created_by=None, namespace: str = "wip", entry_id=None):
        global _terminology_counter
        _terminology_counter += 1
        return f"TERM-{_terminology_counter:06d}"

    async def mock_register_term(terminology_id: str, value: str, created_by=None, namespace: str = "wip", entry_id=None):
        global _term_counter
        _term_counter += 1
        return f"T-{_term_counter:06d}"

    async def mock_register_terms_bulk(terminology_id: str, terms: list, created_by=None, registry_batch_size: int = 100, namespace: str = "wip", entry_id=None):
        global _term_counter
        results = []
        for term in terms:
            _term_counter += 1
            results.append({
                "status": "registered",
                "registry_id": f"T-{_term_counter:06d}",
                "value": term["value"]
            })
        return results

    async def mock_add_synonym(*args, **kwargs):
        return True

    async def mock_lookup_by_value(*args, **kwargs):
        return None

    async def mock_health_check():
        return True

    mock_client.register_terminology = mock_register_terminology
    mock_client.register_term = mock_register_term
    mock_client.register_terms_bulk = mock_register_terms_bulk
    mock_client.add_synonym = mock_add_synonym
    mock_client.lookup_by_value = mock_lookup_by_value
    mock_client.health_check = mock_health_check

    return mock_client


@pytest_asyncio.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Create an async HTTP client for testing the API."""
    _reset_counters()

    mongo_client = AsyncIOMotorClient(os.environ["MONGO_URI"])
    await init_beanie(
        database=mongo_client[os.environ["DATABASE_NAME"]],
        document_models=[Terminology, Term, TermAuditLog, TermRelationship]
    )

    # Clean up data from previous test
    await Term.delete_all()
    await Terminology.delete_all()
    await TermAuditLog.delete_all()
    await TermRelationship.delete_all()

    # Invalidate OntologyService cache so each test starts fresh
    from def_store.services.ontology_service import OntologyService
    OntologyService.invalidate_relationship_type_cache()

    # Bootstrap _ONTOLOGY_RELATIONSHIP_TYPES directly in MongoDB for tests
    from def_store.services.system_terminologies import SYSTEM_TERMINOLOGIES
    for sys_term in SYSTEM_TERMINOLOGIES:
        terminology = Terminology(
            terminology_id=f"SYS-{sys_term['value']}",
            namespace="wip",
            value=sys_term["value"],
            label=sys_term["label"],
            description=sys_term.get("description", ""),
            case_sensitive=sys_term.get("case_sensitive", False),
            metadata=sys_term.get("metadata", {}),
            status="active",
            term_count=len(sys_term.get("terms", [])),
        )
        await terminology.insert()
        for j, t in enumerate(sys_term.get("terms", [])):
            term = Term(
                term_id=f"SYS-T-{sys_term['value']}-{j}",
                namespace="wip",
                terminology_id=terminology.terminology_id,
                value=t["value"],
                label=t.get("label", t["value"]),
                description=t.get("description", ""),
                status="active",
                sort_order=t.get("sort_order", j),
                metadata=t.get("metadata", {}),
            )
            await term.insert()

    # Store client in app state (needed by health check)
    app.state.mongodb_client = mongo_client

    # Set API key
    set_api_key(os.environ["API_KEY"])

    # Create mock registry client and patch the getter
    mock_registry = create_mock_registry_client()

    # Only patch where get_registry_client is actually imported
    with (
        patch('def_store.services.terminology_service.get_registry_client', return_value=mock_registry),
        patch('def_store.main.get_registry_client', return_value=mock_registry),
    ):
            # Create test HTTP client
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                yield ac


@pytest.fixture
def api_key() -> str:
    """Return the test API key."""
    return os.environ["API_KEY"]


@pytest.fixture
def auth_headers(api_key: str) -> dict:
    """Return headers with API key for authenticated requests."""
    return {"X-API-Key": api_key}
