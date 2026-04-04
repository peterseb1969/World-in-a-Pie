"""Pytest configuration and fixtures for Document Store tests.

Uses transport injection to mount the real Registry in-process.
No mock registry, no mock resolution — all ID generation and synonym
resolution goes through the real Registry code via ASGITransport.

Template-Store and Def-Store clients are still mocked (separate services
not mounted in-process).
"""

import json
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
os.environ.setdefault("DATABASE_NAME", "wip_document_store_test")
os.environ.setdefault("API_KEY", "test_api_key")
os.environ.setdefault("REGISTRY_URL", "http://registry")
os.environ.setdefault("REGISTRY_API_KEY", "test_api_key")
os.environ.setdefault("MASTER_API_KEY", "test_api_key")
os.environ.setdefault("TEMPLATE_STORE_URL", "http://localhost:8003")
os.environ.setdefault("TEMPLATE_STORE_API_KEY", "test_template_store_key")
os.environ.setdefault("DEF_STORE_URL", "http://localhost:8002")
os.environ.setdefault("DEF_STORE_API_KEY", "test_def_store_key")
os.environ.setdefault("AUTH_ENABLED", "true")

# Configure namespace-scoped API key for the middleware created at import time.
# This must happen BEFORE importing document_store.main (which calls setup_auth).
# The legacy key (from API_KEY env var) has namespaces=None, which breaks
# namespace derivation in resolve_or_404. Using api_keys_json with an explicit
# namespace ensures resolution works for list/GET endpoints.
os.environ["WIP_AUTH_LEGACY_API_KEY"] = ""  # Suppress legacy key (namespaces=None)
os.environ.setdefault("WIP_AUTH_API_KEYS_JSON", json.dumps([{
    "name": "test",
    "key": os.environ["API_KEY"],
    "owner": "test",
    "groups": ["wip-admins"],
    "namespaces": ["wip"],
}]))


# Document-store models and app (must be after env var setup)
from document_store.main import app  # noqa: E402
from document_store.models.document import Document  # noqa: E402
from document_store.services.def_store_client import DefStoreClient  # noqa: E402
from document_store.services.registry_client import RegistryClient  # noqa: E402
from document_store.services.template_store_client import TemplateStoreClient  # noqa: E402

# Registry models and app (mounted in-process via transport injection)
from registry.main import app as registry_app  # noqa: E402
from registry.models.deletion_journal import DeletionJournal  # noqa: E402
from registry.models.entry import RegistryEntry  # noqa: E402
from registry.models.grant import NamespaceGrant  # noqa: E402
from registry.models.id_counter import IdCounter  # noqa: E402
from registry.models.namespace import Namespace  # noqa: E402
from registry.services.auth import AuthService  # noqa: E402

# Auth configuration (namespace-scoped key for consistent resolution)
from wip_auth import AuthConfig, set_auth_config  # noqa: E402

# Resolution transport injection
from wip_auth.resolve import clear_resolution_cache, set_resolve_transport  # noqa: E402


# ---------------------------------------------------------------------------
# Template definitions (the template DATA — keys are assigned dynamically
# after registering in the real Registry)
# ---------------------------------------------------------------------------

_TEMPLATE_DEFS = [
    {
        "legacy_key": "TPL-000001",
        "value": "PERSON",
        "label": "Person Template",
        "version": 1,
        "status": "active",
        "identity_fields": ["national_id"],
        "fields": [
            {
                "name": "national_id",
                "label": "National ID",
                "type": "string",
                "mandatory": True,
                "validation": {"pattern": r"^\d{9}$"},
            },
            {
                "name": "first_name",
                "label": "First Name",
                "type": "string",
                "mandatory": True,
                "validation": {"min_length": 1, "max_length": 100},
            },
            {
                "name": "last_name",
                "label": "Last Name",
                "type": "string",
                "mandatory": True,
            },
            {
                "name": "birth_date",
                "label": "Birth Date",
                "type": "date",
                "mandatory": False,
            },
            {
                "name": "gender",
                "label": "Gender",
                "type": "term",
                "terminology_ref": "GENDER",
                "mandatory": False,
            },
            {
                "name": "age",
                "label": "Age",
                "type": "integer",
                "mandatory": False,
                "validation": {"minimum": 0, "maximum": 150},
            },
            {
                "name": "email",
                "label": "Email",
                "type": "string",
                "mandatory": False,
                "validation": {"pattern": r"^[\w\.-]+@[\w\.-]+\.\w+$"},
            },
        ],
        "rules": [],
    },
    {
        "legacy_key": "TPL-000002",
        "value": "EMPLOYEE",
        "label": "Employee Template",
        "version": 1,
        "status": "active",
        "identity_fields": ["employee_id", "company_id"],
        "fields": [
            {"name": "employee_id", "label": "Employee ID", "type": "string", "mandatory": True},
            {"name": "company_id", "label": "Company ID", "type": "string", "mandatory": True},
            {"name": "name", "label": "Name", "type": "string", "mandatory": True},
            {"name": "department", "label": "Department", "type": "string", "mandatory": False},
            {"name": "manager_id", "label": "Manager ID", "type": "string", "mandatory": False},
        ],
        "rules": [
            {
                "type": "conditional_required",
                "conditions": [{"field": "department", "operator": "exists", "value": True}],
                "target_field": "manager_id",
                "required": True,
                "error_message": "Manager ID is required when department is specified",
            }
        ],
    },
    {
        "legacy_key": "TPL-INACTIVE",
        "value": "INACTIVE",
        "label": "Inactive Template",
        "version": 1,
        "status": "inactive",
        "identity_fields": [],
        "fields": [],
        "rules": [],
    },
    {
        "legacy_key": "TPL-NO-IDENTITY",
        "value": "NO_IDENTITY",
        "label": "Template Without Identity Fields",
        "version": 1,
        "status": "active",
        "identity_fields": [],
        "fields": [
            {"name": "title", "label": "Title", "type": "string", "mandatory": True},
            {"name": "notes", "label": "Notes", "type": "string", "mandatory": False},
        ],
        "rules": [],
    },
]

# Populated per-test by the client fixture after registering in real Registry.
# Keyed by BOTH UUID7 (canonical) and legacy key (TPL-000001 etc.) for
# backward compatibility with mock template_store_client lookups.
SAMPLE_TEMPLATES: dict[str, dict] = {}

# Maps legacy key → real UUID7 template_id (populated per-test)
_TEMPLATE_ID_MAP: dict[str, str] = {}


async def _register_templates_in_registry(registry_transport):
    """Register template entries in Registry and return legacy→UUID7 mapping.

    Also registers synonyms so that both the legacy key (TPL-000001) and
    the value name (PERSON) resolve to the canonical UUID7 ID.
    """
    api_key = os.environ["MASTER_API_KEY"]
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    mapping: dict[str, str] = {}

    async with AsyncClient(transport=registry_transport, base_url="http://registry") as client:
        for tdef in _TEMPLATE_DEFS:
            # Register entry
            resp = await client.post(
                "/api/registry/entries/register",
                headers=headers,
                json=[{
                    "namespace": "wip",
                    "entity_type": "templates",
                    "composite_key": {"value": tdef["value"], "label": tdef["label"]},
                }],
            )
            assert resp.status_code == 200, f"Register template failed: {resp.text}"
            entry_id = resp.json()["results"][0]["registry_id"]
            mapping[tdef["legacy_key"]] = entry_id

            # Register synonyms so resolution works:
            # 1. Value-based: {"ns": "wip", "type": "template", "value": "PERSON"}
            # 2. Legacy key: {"ns": "wip", "type": "template", "value": "TPL-000001"}
            synonyms = [
                {
                    "target_id": entry_id,
                    "synonym_namespace": "wip",
                    "synonym_entity_type": "templates",
                    "synonym_composite_key": {
                        "ns": "wip",
                        "type": "template",
                        "value": tdef["value"],
                    },
                },
                {
                    "target_id": entry_id,
                    "synonym_namespace": "wip",
                    "synonym_entity_type": "templates",
                    "synonym_composite_key": {
                        "ns": "wip",
                        "type": "template",
                        "value": tdef["legacy_key"],
                    },
                },
            ]
            await client.post(
                "/api/registry/synonyms/add",
                headers=headers,
                json=synonyms,
            )

    return mapping


def _build_sample_templates(id_map: dict[str, str]) -> dict[str, dict]:
    """Build SAMPLE_TEMPLATES dict keyed by both UUID7 and legacy keys."""
    templates: dict[str, dict] = {}

    for tdef in _TEMPLATE_DEFS:
        real_id = id_map[tdef["legacy_key"]]
        template_data = {
            "template_id": real_id,
            "value": tdef["value"],
            "label": tdef["label"],
            "version": tdef["version"],
            "status": tdef["status"],
            "namespace": "wip",
            "identity_fields": tdef["identity_fields"],
            "fields": tdef["fields"],
            "rules": tdef["rules"],
        }
        # Key by canonical UUID7 (for lookups after resolution)
        templates[real_id] = template_data
        # Key by legacy key (for backward compat with test code)
        templates[tdef["legacy_key"]] = template_data

    return templates


def create_mock_template_store_client():
    """Create a mock Template Store client for testing.

    Looks up templates in SAMPLE_TEMPLATES (keyed by both UUID7 and legacy).
    """
    mock_client = AsyncMock(spec=TemplateStoreClient)

    async def mock_get_template(template_id=None, template_value=None, resolve_inheritance=True):
        if template_id and template_id in SAMPLE_TEMPLATES:
            return SAMPLE_TEMPLATES[template_id]
        # Also try looking up by value
        if template_value:
            for t in SAMPLE_TEMPLATES.values():
                if t["value"] == template_value:
                    return t
        return None

    async def mock_get_template_resolved(template_id, version=None):
        return await mock_get_template(template_id=template_id)

    async def mock_template_exists(template_ref):
        if template_ref in SAMPLE_TEMPLATES:
            return SAMPLE_TEMPLATES[template_ref]["status"] == "active"
        return False

    async def mock_health_check():
        return True

    mock_client.get_template = mock_get_template
    mock_client.get_template_resolved = mock_get_template_resolved
    mock_client.template_exists = mock_template_exists
    mock_client.health_check = mock_health_check

    return mock_client


def create_mock_def_store_client():
    """Create a mock Def-Store client for testing.

    Template-store validates terminology references via Def-Store.
    We mock this because Def-Store is a separate service not mounted
    in-process.
    """
    mock_client = AsyncMock(spec=DefStoreClient)

    VALID_TERMS = {
        "GENDER": ["M", "F", "O"],
        "COUNTRY": ["USA", "UK", "CA"],
    }

    async def mock_terminology_exists(terminology_ref):
        # Accept any UUID-shaped ID (from real Registry) or known test names
        if len(terminology_ref) > 8 and "-" in terminology_ref:
            return True
        return terminology_ref in VALID_TERMS

    async def mock_get_terminology(terminology_id=None, terminology_value=None):
        if terminology_id:
            if terminology_id in VALID_TERMS or (len(terminology_id) > 8 and "-" in terminology_id):
                return {"terminology_id": terminology_id, "status": "active"}
        if terminology_value in VALID_TERMS:
            return {"terminology_id": f"TERM-{terminology_value}", "status": "active"}
        return None

    async def mock_validate_value(terminology_ref, value):
        # Strip prefix for lookup
        code = terminology_ref
        for prefix in ("TERM-",):
            if terminology_ref.startswith(prefix):
                code = terminology_ref[len(prefix):]
                break
        if code in VALID_TERMS and value in VALID_TERMS[code]:
            return {"valid": True, "matched_term": {"term_id": "T-001", "value": value}}
        return {"valid": False, "suggestion": None}

    async def mock_validate_values_bulk(items):
        results = []
        for item in items:
            terminology_ref = item["terminology_ref"]
            value = item["value"]
            code = terminology_ref
            for prefix in ("TERM-",):
                if terminology_ref.startswith(prefix):
                    code = terminology_ref[len(prefix):]
                    break
            if code in VALID_TERMS and value in VALID_TERMS[code]:
                results.append({"valid": True, "matched_term": {"term_id": "T-001", "value": value}})
            else:
                results.append({"valid": False, "suggestion": None})
        return results

    async def mock_health_check():
        return True

    mock_client.terminology_exists = mock_terminology_exists
    mock_client.get_terminology = mock_get_terminology
    mock_client.validate_value = mock_validate_value
    mock_client.validate_values_bulk = mock_validate_values_bulk
    mock_client.health_check = mock_health_check

    return mock_client


async def setup_registry_and_app(mongo_client, document_models=None):
    """Common setup: init real Registry, register templates, configure auth.

    Used by both the main `client` fixture and the `file_client` fixture
    in test_files.py. Returns (real_registry, registry_transport) so callers
    can wire them into their patch context.
    """
    if document_models is None:
        document_models = [Document]

    test_db = mongo_client[os.environ["DATABASE_NAME"]]

    # Single init_beanie for all models — avoids database binding drift
    # that causes "Namespace not found" errors in CI when beanie rebinds
    # Registry models to the wrong database after a second init_beanie call.
    await init_beanie(
        database=test_db,
        document_models=[
            # Registry models
            Namespace, RegistryEntry, IdCounter, NamespaceGrant, DeletionJournal,
            # Document-Store models
            *document_models,
        ],
    )

    # Clean all collections
    await RegistryEntry.delete_all()
    await Namespace.delete_all()
    await IdCounter.delete_all()
    await NamespaceGrant.delete_all()
    await DeletionJournal.delete_all()
    for model in document_models:
        await model.delete_all()

    registry_app.state.mongodb_client = mongo_client
    AuthService.initialize(master_key=os.environ["MASTER_API_KEY"])

    # Create test namespaces
    for prefix in ("wip", "test-ns"):
        await Namespace(prefix=prefix, description=f"Test namespace: {prefix}").insert()

    # Mount Registry in-process
    registry_transport = ASGITransport(app=registry_app)

    # Register template entries in Registry and get UUID7 IDs
    _TEMPLATE_ID_MAP.clear()
    _TEMPLATE_ID_MAP.update(await _register_templates_in_registry(registry_transport))
    SAMPLE_TEMPLATES.clear()
    SAMPLE_TEMPLATES.update(_build_sample_templates(_TEMPLATE_ID_MAP))

    app.state.mongodb_client = mongo_client

    # Configure API key with namespace scope so resolution can derive
    # namespace from identity (needed for list/GET where namespace is optional)
    config = AuthConfig(
        mode="api_key_only",
        api_keys_json=json.dumps([{
            "name": "test",
            "key": os.environ["API_KEY"],
            "owner": "test",
            "groups": ["wip-admins"],
            "namespaces": ["wip"],
        }]),
    )
    set_auth_config(config)

    # Wire real RegistryClient with transport injection
    real_registry = RegistryClient(
        base_url="http://registry",
        api_key=os.environ["MASTER_API_KEY"],
        transport=registry_transport,
    )

    # Wire real resolution with transport injection
    set_resolve_transport(registry_transport)
    clear_resolution_cache()

    return real_registry, registry_transport


@pytest_asyncio.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Create test client with real Registry mounted in-process."""
    mongo_client = AsyncIOMotorClient(os.environ["MONGO_URI"])
    real_registry, _transport = await setup_registry_and_app(mongo_client)

    # Mock Template-Store and Def-Store clients (separate services)
    mock_template_store = create_mock_template_store_client()
    mock_def_store = create_mock_def_store_client()

    # Patch singleton getters — NO resolve_entity_id mock
    with (
        patch('document_store.services.document_service.get_registry_client', return_value=real_registry),
        patch('document_store.services.document_service.get_template_store_client', return_value=mock_template_store),
        patch('document_store.services.document_service.get_def_store_client', return_value=mock_def_store),
        patch('document_store.services.validation_service.get_template_store_client', return_value=mock_template_store),
        patch('document_store.services.validation_service.get_def_store_client', return_value=mock_def_store),
        patch('document_store.main.get_registry_client', return_value=real_registry),
        patch('document_store.main.get_template_store_client', return_value=mock_template_store),
        patch('document_store.main.get_def_store_client', return_value=mock_def_store),
        patch('document_store.api.table_view.get_template_store_client', return_value=mock_template_store),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    # Cleanup
    set_resolve_transport(None)
    clear_resolution_cache()


@pytest.fixture
def api_key() -> str:
    """Return the test API key."""
    return os.environ["API_KEY"]


@pytest.fixture
def auth_headers(api_key: str) -> dict:
    """Return headers with API key for authenticated requests."""
    return {"X-API-Key": api_key}


@pytest.fixture
def sample_person_data() -> dict:
    """Sample valid person document data."""
    return {
        "national_id": "123456789",
        "first_name": "John",
        "last_name": "Doe",
        "birth_date": "1990-01-15",
        "gender": "M",
        "age": 34,
    }


@pytest.fixture
def sample_employee_data() -> dict:
    """Sample valid employee document data."""
    return {
        "employee_id": "EMP001",
        "company_id": "COMP001",
        "name": "Jane Smith",
        "department": "Engineering",
        "manager_id": "MGR001",
    }
