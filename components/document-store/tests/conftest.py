"""Pytest configuration and fixtures for Document Store tests."""

import os
import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from beanie import init_beanie
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

# Use existing env vars if set, otherwise defaults for local testing
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017/")
os.environ.setdefault("DATABASE_NAME", "wip_document_store_test")
os.environ.setdefault("API_KEY", "test_api_key")
os.environ.setdefault("REGISTRY_URL", "http://localhost:8001")
os.environ.setdefault("REGISTRY_API_KEY", "test_registry_key")
os.environ.setdefault("TEMPLATE_STORE_URL", "http://localhost:8003")
os.environ.setdefault("TEMPLATE_STORE_API_KEY", "test_template_store_key")
os.environ.setdefault("DEF_STORE_URL", "http://localhost:8002")
os.environ.setdefault("DEF_STORE_API_KEY", "test_def_store_key")

from document_store.api.auth import set_api_key
from document_store.main import app
from document_store.models.document import Document
from document_store.services.def_store_client import DefStoreClient
from document_store.services.registry_client import RegistryClient
from document_store.services.template_store_client import TemplateStoreClient

# Counter for generating mock IDs
_document_counter = 0


def _reset_counters():
    """Reset ID counters for a new test."""
    global _document_counter
    _document_counter = 0


# Sample templates for testing
SAMPLE_TEMPLATES = {
    "TPL-000001": {
        "template_id": "TPL-000001",
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
                "validation": {"pattern": r"^\d{9}$"}
            },
            {
                "name": "first_name",
                "label": "First Name",
                "type": "string",
                "mandatory": True,
                "validation": {"min_length": 1, "max_length": 100}
            },
            {
                "name": "last_name",
                "label": "Last Name",
                "type": "string",
                "mandatory": True
            },
            {
                "name": "birth_date",
                "label": "Birth Date",
                "type": "date",
                "mandatory": False
            },
            {
                "name": "gender",
                "label": "Gender",
                "type": "term",
                "terminology_ref": "GENDER",
                "mandatory": False
            },
            {
                "name": "age",
                "label": "Age",
                "type": "integer",
                "mandatory": False,
                "validation": {"minimum": 0, "maximum": 150}
            },
            {
                "name": "email",
                "label": "Email",
                "type": "string",
                "mandatory": False,
                "validation": {"pattern": r"^[\w\.-]+@[\w\.-]+\.\w+$"}
            }
        ],
        "rules": []
    },
    "TPL-000002": {
        "template_id": "TPL-000002",
        "value": "EMPLOYEE",
        "label": "Employee Template",
        "version": 1,
        "status": "active",
        "identity_fields": ["employee_id", "company_id"],
        "fields": [
            {
                "name": "employee_id",
                "label": "Employee ID",
                "type": "string",
                "mandatory": True
            },
            {
                "name": "company_id",
                "label": "Company ID",
                "type": "string",
                "mandatory": True
            },
            {
                "name": "name",
                "label": "Name",
                "type": "string",
                "mandatory": True
            },
            {
                "name": "department",
                "label": "Department",
                "type": "string",
                "mandatory": False
            },
            {
                "name": "manager_id",
                "label": "Manager ID",
                "type": "string",
                "mandatory": False
            }
        ],
        "rules": [
            {
                "type": "conditional_required",
                "conditions": [
                    {"field": "department", "operator": "exists", "value": True}
                ],
                "target_field": "manager_id",
                "required": True,
                "error_message": "Manager ID is required when department is specified"
            }
        ]
    },
    "TPL-INACTIVE": {
        "template_id": "TPL-INACTIVE",
        "value": "INACTIVE",
        "label": "Inactive Template",
        "version": 1,
        "status": "inactive",
        "identity_fields": [],
        "fields": [],
        "rules": []
    },
    "TPL-NO-IDENTITY": {
        "template_id": "TPL-NO-IDENTITY",
        "value": "NO_IDENTITY",
        "label": "Template Without Identity Fields",
        "version": 1,
        "status": "active",
        "identity_fields": [],
        "fields": [
            {
                "name": "title",
                "label": "Title",
                "type": "string",
                "mandatory": True
            },
            {
                "name": "notes",
                "label": "Notes",
                "type": "string",
                "mandatory": False
            }
        ],
        "rules": []
    }
}


def create_mock_registry_client():
    """Create a mock registry client for testing.

    Simulates stable document IDs with centralized identity hashing:
    - With identity fields: sends identity_values to Registry, which computes
      identity_hash, uses it for dedup, and returns it
    - Without identity fields: always generates a fresh ID (is_new=True)
    """
    mock_client = AsyncMock(spec=RegistryClient)

    # Track registered composite keys → (document_id, identity_hash) for stable ID simulation
    _registry: dict[str, tuple[str, str]] = {}

    def _compute_mock_identity_hash(identity_values):
        """Compute a simple mock hash from identity values."""
        import hashlib
        import json
        canonical = json.dumps(identity_values, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    async def mock_generate_document_id(
        template_id, identity_values=None, has_identity_fields=True,
        created_by=None, namespace="wip", entry_id=None
    ):
        if entry_id:
            id_hash = _compute_mock_identity_hash(identity_values) if identity_values else None
            return entry_id, False, id_hash
        if has_identity_fields and identity_values:
            id_hash = _compute_mock_identity_hash(identity_values)
            composite_key = f"{id_hash}:{template_id}"
            if composite_key in _registry:
                doc_id, _ = _registry[composite_key]
                return doc_id, False, id_hash  # existing
            doc_id = str(uuid.uuid4())
            _registry[composite_key] = (doc_id, id_hash)
            return doc_id, True, id_hash  # new
        else:
            return str(uuid.uuid4()), True, None  # always new, no identity

    async def mock_generate_document_ids_bulk(items, created_by=None, namespace="wip"):
        results = []
        for item in items:
            has_identity = item.get("has_identity_fields", True)
            identity_values = item.get("identity_values")
            if has_identity and identity_values:
                id_hash = _compute_mock_identity_hash(identity_values)
                composite_key = f"{id_hash}:{item['template_id']}"
                if composite_key in _registry:
                    doc_id, _ = _registry[composite_key]
                    results.append({
                        "status": "already_exists",
                        "registry_id": doc_id,
                        "identity_hash": id_hash,
                    })
                else:
                    doc_id = str(uuid.uuid4())
                    _registry[composite_key] = (doc_id, id_hash)
                    results.append({
                        "status": "created",
                        "registry_id": doc_id,
                        "identity_hash": id_hash,
                    })
            else:
                results.append({
                    "status": "created",
                    "registry_id": str(uuid.uuid4()),
                    "identity_hash": None,
                })
        return results

    async def mock_health_check():
        return True

    mock_client.generate_document_id = mock_generate_document_id
    mock_client.generate_document_ids_bulk = mock_generate_document_ids_bulk
    mock_client.health_check = mock_health_check

    return mock_client


def create_mock_template_store_client():
    """Create a mock Template Store client for testing."""
    mock_client = AsyncMock(spec=TemplateStoreClient)

    async def mock_get_template(template_id=None, template_value=None, resolve_inheritance=True):
        if template_id and template_id in SAMPLE_TEMPLATES:
            return SAMPLE_TEMPLATES[template_id]
        return None

    async def mock_get_template_resolved(template_id, version=None):
        return await mock_get_template(template_id=template_id)

    async def mock_template_exists(template_ref):
        return template_ref in SAMPLE_TEMPLATES and SAMPLE_TEMPLATES[template_ref]["status"] == "active"

    async def mock_health_check():
        return True

    mock_client.get_template = mock_get_template
    mock_client.get_template_resolved = mock_get_template_resolved
    mock_client.template_exists = mock_template_exists
    mock_client.health_check = mock_health_check

    return mock_client


def create_mock_def_store_client():
    """Create a mock Def-Store client for testing."""
    mock_client = AsyncMock(spec=DefStoreClient)

    # Valid term values for testing
    VALID_TERMS = {
        "GENDER": ["M", "F", "O"],
        "COUNTRY": ["USA", "UK", "CA"],
    }

    async def mock_terminology_exists(terminology_ref):
        if terminology_ref.startswith("TERM-"):
            return True
        return terminology_ref in VALID_TERMS

    async def mock_get_terminology(terminology_id=None, terminology_value=None):
        if terminology_id and terminology_id.startswith("TERM-"):
            return {"terminology_id": terminology_id, "status": "active"}
        if terminology_value in VALID_TERMS:
            return {"terminology_id": f"TERM-{terminology_value}", "status": "active"}
        return None

    async def mock_validate_value(terminology_ref, value):
        code = terminology_ref.replace("TERM-", "") if terminology_ref.startswith("TERM-") else terminology_ref
        if code in VALID_TERMS and value in VALID_TERMS[code]:
            return {"valid": True, "matched_term": {"term_id": "T-001", "value": value}}
        return {"valid": False, "suggestion": None}

    async def mock_validate_values_bulk(items):
        results = []
        for item in items:
            terminology_ref = item["terminology_ref"]
            value = item["value"]
            code = terminology_ref.replace("TERM-", "") if terminology_ref.startswith("TERM-") else terminology_ref
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


@pytest_asyncio.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Create an async HTTP client for testing the API."""
    _reset_counters()

    mongo_client = AsyncIOMotorClient(os.environ["MONGO_URI"])
    await init_beanie(
        database=mongo_client[os.environ["DATABASE_NAME"]],
        document_models=[Document]
    )

    # Clean up data from previous test
    await Document.delete_all()

    # Store client in app state (needed by health check)
    app.state.mongodb_client = mongo_client

    # Set API key
    set_api_key(os.environ["API_KEY"])

    # Create mock clients
    mock_registry = create_mock_registry_client()
    mock_template_store = create_mock_template_store_client()
    mock_def_store = create_mock_def_store_client()

    # Mock Registry resolution: simulates Registry confirming any ID it receives.
    # In tests, all IDs come from the mock registry above, so they are always valid.
    async def mock_resolve_entity_id(raw_id, entity_type, namespace, **kwargs):
        return raw_id

    # Patch where the clients are actually used
    with patch('document_store.services.document_service.get_registry_client', return_value=mock_registry), \
         patch('document_store.services.document_service.get_template_store_client', return_value=mock_template_store), \
         patch('document_store.services.document_service.get_def_store_client', return_value=mock_def_store), \
         patch('document_store.services.validation_service.get_template_store_client', return_value=mock_template_store), \
         patch('document_store.services.validation_service.get_def_store_client', return_value=mock_def_store), \
         patch('document_store.main.get_registry_client', return_value=mock_registry), \
         patch('document_store.main.get_template_store_client', return_value=mock_template_store), \
         patch('document_store.main.get_def_store_client', return_value=mock_def_store), \
         patch('document_store.api.table_view.get_template_store_client', return_value=mock_template_store), \
         patch('wip_auth.fastapi_helpers.resolve_entity_id', side_effect=mock_resolve_entity_id):
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


@pytest.fixture
def sample_person_data() -> dict:
    """Sample valid person document data."""
    return {
        "national_id": "123456789",
        "first_name": "John",
        "last_name": "Doe",
        "birth_date": "1990-01-15",
        "gender": "M",
        "age": 34
    }


@pytest.fixture
def sample_employee_data() -> dict:
    """Sample valid employee document data."""
    return {
        "employee_id": "EMP001",
        "company_id": "COMP001",
        "name": "Jane Smith",
        "department": "Engineering",
        "manager_id": "MGR001"
    }
