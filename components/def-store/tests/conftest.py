"""Pytest configuration and fixtures for Def-Store tests.

Uses transport injection to mount the real Registry in-process.
No mock registry — all ID generation and resolution goes through
the real Registry code via ASGITransport.
"""

import os
import sys
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from beanie import init_beanie
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

# Add registry src to path so we can import it in-process
_registry_src = str(Path(__file__).resolve().parents[2] / "registry" / "src")
if _registry_src not in sys.path:
    sys.path.insert(0, _registry_src)

# Use existing env vars if set, otherwise defaults for local testing
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017/")
os.environ.setdefault("DATABASE_NAME", "wip_def_store_test")
os.environ.setdefault("API_KEY", "test_api_key")
os.environ.setdefault("REGISTRY_URL", "http://registry")
os.environ.setdefault("REGISTRY_API_KEY", "test_api_key")
os.environ.setdefault("MASTER_API_KEY", "test_api_key")
os.environ.setdefault("AUTH_ENABLED", "true")

# Def-store models and app
from def_store.api.auth import set_api_key
from def_store.main import app
from def_store.models.audit_log import TermAuditLog
from def_store.models.term import Term
from def_store.models.term_relationship import TermRelationship
from def_store.models.terminology import Terminology
from def_store.services.registry_client import RegistryClient

# Registry models and app (mounted in-process via transport injection)
from registry.main import app as registry_app
from registry.models.deletion_journal import DeletionJournal
from registry.models.entry import RegistryEntry
from registry.models.grant import NamespaceGrant
from registry.models.id_counter import IdCounter
from registry.models.namespace import Namespace
from registry.services.auth import AuthService

# Resolution transport injection
from wip_auth.resolve import clear_resolution_cache, set_resolve_transport


@pytest_asyncio.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Create test client with real Registry mounted in-process.

    Both Registry and Def-Store share a MongoDB connection but use
    separate databases. The Registry is mounted via ASGITransport —
    all RegistryClient HTTP calls and resolve_entity_id calls route
    to the real Registry app without leaving the process.
    """
    mongo_client = AsyncIOMotorClient(os.environ["MONGO_URI"])

    # --- Initialize Registry ---
    await init_beanie(
        database=mongo_client["wip_registry_test"],
        document_models=[Namespace, RegistryEntry, IdCounter, NamespaceGrant, DeletionJournal],
    )
    await RegistryEntry.delete_all()
    await Namespace.delete_all()
    await IdCounter.delete_all()
    await NamespaceGrant.delete_all()
    await DeletionJournal.delete_all()

    registry_app.state.mongodb_client = mongo_client
    AuthService.initialize(master_key=os.environ["MASTER_API_KEY"])

    # Create test namespaces in Registry
    for prefix in ("wip", "test-ns"):
        await Namespace(prefix=prefix, description=f"Test namespace: {prefix}").insert()

    # Mount Registry in-process
    registry_transport = ASGITransport(app=registry_app)

    # --- Initialize Def-Store ---
    await init_beanie(
        database=mongo_client[os.environ["DATABASE_NAME"]],
        document_models=[Terminology, Term, TermAuditLog, TermRelationship],
    )
    await Term.delete_all()
    await Terminology.delete_all()
    await TermAuditLog.delete_all()
    await TermRelationship.delete_all()

    # Invalidate OntologyService cache so each test starts fresh
    from def_store.services.ontology_service import OntologyService
    OntologyService.invalidate_relationship_type_cache()

    # Bootstrap system terminologies directly in MongoDB.
    # These are internal data (relationship types etc.) that def-store
    # creates at startup. They use hardcoded SYS-* IDs and don't need
    # Registry registration — they're never resolved via synonyms.
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

    app.state.mongodb_client = mongo_client
    set_api_key(os.environ["API_KEY"])

    # Wire real RegistryClient with transport injection
    real_registry = RegistryClient(
        base_url="http://registry",
        api_key=os.environ["MASTER_API_KEY"],
        transport=registry_transport,
    )

    # Wire real resolution with transport injection
    set_resolve_transport(registry_transport)
    clear_resolution_cache()

    # Patch singleton getter to return transport-injected client
    with (
        patch('def_store.services.terminology_service.get_registry_client', return_value=real_registry),
        patch('def_store.main.get_registry_client', return_value=real_registry),
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
