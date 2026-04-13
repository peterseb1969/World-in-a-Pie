"""Tests for fastapi_helpers — namespace derivation from identity.

Uses real Registry via transport injection for resolve_or_404 tests.
No mock of resolve_entity_id.
"""

import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from beanie import init_beanie
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

# Add registry src to path for in-process mounting
_registry_src = str(Path(__file__).resolve().parents[3] / "components" / "registry" / "src")
if _registry_src not in sys.path:
    sys.path.insert(0, _registry_src)

os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017/")
# Registry auth middleware is created at import time — must set before importing registry.main
os.environ.setdefault("WIP_AUTH_LEGACY_API_KEY", "test_api_key")
os.environ.setdefault("WIP_AUTH_MODE", "api_key_only")
# resolve.py reads REGISTRY_API_KEY for its calls to Registry
os.environ.setdefault("REGISTRY_API_KEY", "test_api_key")

from registry.main import app as registry_app  # noqa: E402
from registry.models.deletion_journal import DeletionJournal  # noqa: E402
from registry.models.entry import RegistryEntry  # noqa: E402
from registry.models.grant import NamespaceGrant  # noqa: E402
from registry.models.id_counter import IdCounter  # noqa: E402
from registry.models.namespace import Namespace  # noqa: E402
from registry.services.auth import AuthService  # noqa: E402

from wip_auth.fastapi_helpers import (  # noqa: E402
    _derive_namespace_from_identity,
    resolve_or_404,
)
from wip_auth.identity import set_current_identity  # noqa: E402
from wip_auth.models import UserIdentity  # noqa: E402
from wip_auth.resolve import clear_resolution_cache, set_resolve_transport  # noqa: E402


def _make_identity(namespaces=None, auth_method="api_key"):
    """Create a UserIdentity with given namespace scope."""
    return UserIdentity(
        user_id="apikey:test",
        username="test",
        email=None,
        groups=["wip-writers"],
        auth_method=auth_method,
        provider="api_key",
        raw_claims={"namespaces": namespaces},
    )


@pytest_asyncio.fixture(scope="function")
async def registry():
    """Mount real Registry in-process for resolution tests.

    Creates namespace 'aa' with a registered template entry and synonym
    so resolve_or_404 can resolve "AA_CHAPTER" → canonical UUID.
    """
    mongo_client = AsyncIOMotorClient(os.environ["MONGO_URI"])

    await init_beanie(
        database=mongo_client["wip_auth_helpers_test"],
        document_models=[Namespace, RegistryEntry, IdCounter, NamespaceGrant, DeletionJournal],
    )
    await RegistryEntry.delete_all()
    await Namespace.delete_all()
    await IdCounter.delete_all()
    await NamespaceGrant.delete_all()
    await DeletionJournal.delete_all()

    registry_app.state.mongodb_client = mongo_client
    AuthService.initialize(master_key=os.environ.get("MASTER_API_KEY", "test_api_key"))

    await Namespace(prefix="aa", description="Test namespace: aa").insert()

    transport = ASGITransport(app=registry_app)

    # Register a template entry with synonym in namespace "aa"
    api_key = os.environ["WIP_AUTH_LEGACY_API_KEY"]
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    async with AsyncClient(transport=transport, base_url="http://registry") as client:
        resp = await client.post(
            "/api/registry/entries/register",
            headers=headers,
            json=[{
                "namespace": "aa",
                "entity_type": "templates",
                "composite_key": {"value": "AA_CHAPTER", "label": "AA Chapter"},
            }],
        )
        assert resp.status_code == 200, f"Failed to register: {resp.text}"
        entry_id = resp.json()["results"][0]["registry_id"]

        await client.post(
            "/api/registry/synonyms/add",
            headers=headers,
            json=[{
                "target_id": entry_id,
                "synonym_namespace": "aa",
                "synonym_entity_type": "templates",
                "synonym_composite_key": {
                    "ns": "aa",
                    "type": "template",
                    "value": "AA_CHAPTER",
                },
            }],
        )

    set_resolve_transport(transport)
    clear_resolution_cache()

    yield entry_id

    set_resolve_transport(None)
    clear_resolution_cache()
    mongo_client.close()


class TestDeriveNamespaceFromIdentity:
    """Tests for _derive_namespace_from_identity (pure unit tests)."""

    def test_single_namespace_key(self):
        set_current_identity(_make_identity(namespaces=["my-app"]))
        assert _derive_namespace_from_identity() == "my-app"

    def test_multi_namespace_key_returns_none(self):
        set_current_identity(_make_identity(namespaces=["ns-a", "ns-b"]))
        assert _derive_namespace_from_identity() is None

    def test_empty_namespace_list_returns_none(self):
        set_current_identity(_make_identity(namespaces=[]))
        assert _derive_namespace_from_identity() is None

    def test_null_namespaces_returns_none(self):
        set_current_identity(_make_identity(namespaces=None))
        assert _derive_namespace_from_identity() is None

    def test_no_identity_returns_none(self):
        set_current_identity(None)
        assert _derive_namespace_from_identity() is None

    def test_jwt_identity_with_no_namespaces(self):
        set_current_identity(_make_identity(namespaces=None, auth_method="jwt"))
        assert _derive_namespace_from_identity() is None


class TestResolveOr404NamespaceDerivation:
    """Tests that resolve_or_404 derives namespace from identity and resolves via real Registry."""

    @pytest.mark.asyncio
    async def test_derives_namespace_from_single_scope_key(self, registry):
        """When namespace is None but key has one namespace, resolution uses it."""
        expected_id = registry
        set_current_identity(_make_identity(namespaces=["aa"]))

        result = await resolve_or_404("AA_CHAPTER", "template", None)
        assert result == expected_id

    @pytest.mark.asyncio
    async def test_explicit_namespace_takes_precedence(self, registry):
        """Explicit namespace is used even if key has a single namespace."""
        set_current_identity(_make_identity(namespaces=["aa"]))

        # Explicit namespace "other-ns" overrides derived "aa".
        # Since the synonym is only registered in "aa", resolution fails — 404.
        with pytest.raises(HTTPException) as exc_info:
            await resolve_or_404("AA_CHAPTER", "template", "other-ns")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_multi_namespace_key_no_derivation(self, registry):
        """Multi-namespace key without explicit namespace returns raw ID."""
        set_current_identity(_make_identity(namespaces=["ns-a", "ns-b"]))

        result = await resolve_or_404("AA_CHAPTER", "template", None)
        assert result == "AA_CHAPTER"  # raw, unresolved

    @pytest.mark.asyncio
    async def test_unscoped_key_no_derivation(self, registry):
        """Unscoped key (namespaces=None) without explicit namespace returns raw ID."""
        set_current_identity(_make_identity(namespaces=None))

        result = await resolve_or_404("AA_CHAPTER", "template", None)
        assert result == "AA_CHAPTER"  # raw, unresolved
