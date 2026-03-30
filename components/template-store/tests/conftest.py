"""Pytest configuration and fixtures for Template Store tests."""

import asyncio
import os
import re
from typing import AsyncGenerator
from unittest.mock import AsyncMock, patch

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)

import pytest
import pytest_asyncio
from beanie import init_beanie
from httpx import AsyncClient, ASGITransport
from motor.motor_asyncio import AsyncIOMotorClient

# Use existing env vars if set, otherwise defaults for local testing
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017/")
os.environ.setdefault("DATABASE_NAME", "wip_template_store_test")
os.environ.setdefault("API_KEY", "test_api_key")
os.environ.setdefault("REGISTRY_URL", "http://localhost:8001")
os.environ.setdefault("REGISTRY_API_KEY", "test_registry_key")
os.environ.setdefault("DEF_STORE_URL", "http://localhost:8002")
os.environ.setdefault("DEF_STORE_API_KEY", "test_def_store_key")

from template_store.main import app
from template_store.models.template import Template
from template_store.api.auth import set_api_key
from template_store.services.registry_client import configure_registry_client, RegistryClient
from template_store.services.def_store_client import configure_def_store_client, DefStoreClient


# Counter for generating mock IDs
_template_counter = 0


def _reset_counters():
    """Reset ID counters for a new test."""
    global _template_counter
    _template_counter = 0


def create_mock_registry_client():
    """Create a mock registry client for testing."""
    global _template_counter

    mock_client = AsyncMock(spec=RegistryClient)

    async def mock_register_template(created_by=None, namespace="wip", entry_id=None):
        global _template_counter
        if entry_id:
            return entry_id
        _template_counter += 1
        return f"TPL-{_template_counter:06d}"

    async def mock_register_templates_bulk(count: int, created_by=None, namespace="wip"):
        global _template_counter
        results = []
        for _ in range(count):
            _template_counter += 1
            results.append({
                "status": "registered",
                "registry_id": f"TPL-{_template_counter:06d}",
            })
        return results

    async def mock_add_synonym(*args, **kwargs):
        return True

    async def mock_lookup_by_value(*args, **kwargs):
        return None

    async def mock_health_check():
        return True

    mock_client.register_template = mock_register_template
    mock_client.register_templates_bulk = mock_register_templates_bulk
    mock_client.add_synonym = mock_add_synonym
    mock_client.lookup_by_value = mock_lookup_by_value
    mock_client.health_check = mock_health_check

    return mock_client


def create_mock_def_store_client():
    """Create a mock Def-Store client for testing."""
    mock_client = AsyncMock(spec=DefStoreClient)

    async def mock_terminology_exists(terminology_ref: str, namespace=None):
        # Return True for any terminology ref starting with "TERM-" or known codes
        if terminology_ref.startswith("TERM-"):
            return True
        if terminology_ref in ["GENDER", "COUNTRY", "DOC_STATUS"]:
            return True
        return False

    async def mock_get_terminology(terminology_id=None, terminology_value=None, namespace=None):
        if terminology_id and terminology_id.startswith("TERM-"):
            return {"terminology_id": terminology_id, "status": "active"}
        if terminology_value in ["GENDER", "COUNTRY", "DOC_STATUS"]:
            return {"terminology_id": f"TERM-{terminology_value}", "status": "active"}
        return None

    async def mock_validate_value(terminology_ref: str, value: str):
        return {"valid": True, "matched_term": {"term_id": "T-001", "value": value}}

    async def mock_health_check():
        return True

    mock_client.terminology_exists = mock_terminology_exists
    mock_client.get_terminology = mock_get_terminology
    mock_client.validate_value = mock_validate_value
    mock_client.health_check = mock_health_check

    return mock_client


@pytest_asyncio.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Create an async HTTP client for testing the API."""
    _reset_counters()

    mongo_client = AsyncIOMotorClient(os.environ["MONGO_URI"])
    await init_beanie(
        database=mongo_client[os.environ["DATABASE_NAME"]],
        document_models=[Template]
    )

    # Clean up data from previous test
    await Template.delete_all()

    # Store client in app state (needed by health check)
    app.state.mongodb_client = mongo_client

    # Set API key
    set_api_key(os.environ["API_KEY"])

    # Create mock clients and patch the getters
    mock_registry = create_mock_registry_client()
    mock_def_store = create_mock_def_store_client()

    # Mock resolve_entity_ids for template service normalization
    # Maps known values to fake IDs — mirrors the mock def-store client
    async def mock_resolve_entity_ids(raw_ids, entity_type, namespace, include_statuses=None):
        result = {}
        for raw_id in raw_ids:
            if raw_id.startswith(("TPL-", "TERM-")) or _UUID_RE.match(raw_id):
                result[raw_id] = raw_id
            elif entity_type == "terminology" and raw_id in ["GENDER", "COUNTRY", "DOC_STATUS"]:
                result[raw_id] = f"TERM-{raw_id}"
            else:
                from wip_auth.resolve import EntityNotFoundError
                raise EntityNotFoundError(raw_id, entity_type)
        return result

    async def mock_resolve_entity_id(raw_id, entity_type, namespace, include_statuses=None):
        ids = await mock_resolve_entity_ids([raw_id], entity_type, namespace, include_statuses)
        return ids[raw_id]

    # Patch where the clients are actually used
    with patch('template_store.services.template_service.get_registry_client', return_value=mock_registry):
        with patch('template_store.services.template_service.get_def_store_client', return_value=mock_def_store):
            with patch('template_store.services.template_service.resolve_entity_ids', side_effect=mock_resolve_entity_ids):
                with patch('template_store.services.template_service.resolve_entity_id', side_effect=mock_resolve_entity_id):
                    with patch('template_store.main.get_registry_client', return_value=mock_registry):
                        with patch('template_store.main.get_def_store_client', return_value=mock_def_store):
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
