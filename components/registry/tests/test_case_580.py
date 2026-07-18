"""Tests for CASE-580 — registry enumeration endpoints must honour the
caller's namespace scope.

The registry's own listing surface (``GET /entries`` browse and
``GET /entries/search`` unified search) previously applied no namespace
filter: a non-privileged, namespace-scoped api-key could enumerate every
namespace's entry inventory. That contradicts the convention that *listing*
a namespace's data requires a grant (cross-namespace *reference resolution*
stays open — that is a separate, deliberate guarantee gated by isolation,
not grants, and enforced at the stores).

These tests cover:
  - ``resolve_accessible_namespaces`` (the scoping decision): superadmin →
    None (no filter); scoped key → only granted / in-scope namespaces.
  - ``browse_entries`` end-to-end with a scoped identity: unfiltered browse
    returns only in-scope entries; an explicit foreign namespace returns an
    empty page (no leak, no 404-vs-empty distinction to probe); an explicit
    in-scope namespace returns its entries.
  - ``unified_search`` end-to-end: same scoping.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient

from registry.api.grants import resolve_accessible_namespaces
from registry.main import app
from registry.models.entry import RegistryEntry
from registry.models.grant import NamespaceGrant
from registry.models.namespace import Namespace
from registry.services.auth import require_api_key
from wip_auth import UserIdentity


def _scoped_key_identity(namespaces: list[str]) -> UserIdentity:
    """A non-privileged api-key identity scoped to ``namespaces`` — the shape
    the api-key provider builds for a runtime key with a ``namespaces`` list."""
    return UserIdentity(
        user_id="apikey:case580-scoped",
        username="case580-scoped",
        groups=[],
        auth_method="api_key",
        raw_claims={"namespaces": namespaces},
    )


async def _seed_entry(namespace: str, value: str) -> None:
    await RegistryEntry(
        entry_id=f"{namespace}-{value}",
        namespace=namespace,
        entity_type="terms",
        primary_composite_key={"ns": namespace, "value": value},
        primary_composite_key_hash=f"hash-{namespace}-{value}",
        search_values=[value],
        status="active",
    ).insert()


# ──────────────────────────────────────────────────────────────────────
# resolve_accessible_namespaces — the scoping decision


class TestResolveAccessibleNamespaces:
    @pytest.mark.asyncio
    async def test_superadmin_returns_none(self, client: AsyncClient):
        """wip-admins → None means 'no filter' (see everything)."""
        admin = UserIdentity(
            user_id="apikey:admin",
            username="admin",
            groups=["wip-admins"],
            auth_method="api_key",
        )
        assert await resolve_accessible_namespaces(admin) is None

    @pytest.mark.asyncio
    async def test_scoped_key_returns_only_in_scope(self, client: AsyncClient):
        """A key scoped to one namespace resolves 'read' there (the api-key
        fallback) and 'none' elsewhere, so only that namespace comes back."""
        for prefix in ("case580-a", "case580-b"):
            await Namespace(prefix=prefix).insert()

        accessible = await resolve_accessible_namespaces(
            _scoped_key_identity(["case580-a"])
        )
        assert accessible is not None
        assert "case580-a" in accessible
        assert "case580-b" not in accessible

    @pytest.mark.asyncio
    async def test_oidc_user_grant_included(self, client: AsyncClient):
        """An OIDC user's accessible set is driven by grants: a read grant on
        a namespace makes it accessible, an ungranted namespace does not."""
        # The shared client fixture clears entries/namespaces but not grants —
        # keep this test self-isolating.
        await NamespaceGrant.delete_all()
        for prefix in ("case580-granted", "case580-ungranted"):
            await Namespace(prefix=prefix).insert()
        await NamespaceGrant(
            namespace="case580-granted",
            subject="alice@example.com",
            subject_type="user",
            permission="read",
            granted_by="test-admin",
        ).insert()

        user = UserIdentity(
            user_id="alice",
            username="alice",
            email="alice@example.com",
            groups=[],
            auth_method="jwt",
        )
        accessible = await resolve_accessible_namespaces(user)
        assert accessible is not None
        assert "case580-granted" in accessible
        assert "case580-ungranted" not in accessible


# ──────────────────────────────────────────────────────────────────────
# browse_entries / unified_search — end-to-end scoping


@pytest_asyncio.fixture
async def scoped_client(client: AsyncClient):
    """The shared test client, but with require_api_key overridden to a
    non-privileged key scoped to 'case580-mine' only."""
    app.dependency_overrides[require_api_key] = lambda: _scoped_key_identity(
        ["case580-mine"]
    )
    yield client
    app.dependency_overrides.pop(require_api_key, None)


class TestBrowseScoping:
    @pytest.mark.asyncio
    async def test_unfiltered_browse_returns_only_in_scope(
        self, scoped_client: AsyncClient
    ):
        for prefix in ("case580-mine", "case580-foreign"):
            await Namespace(prefix=prefix).insert()
        await _seed_entry("case580-mine", "MINE")
        await _seed_entry("case580-foreign", "FOREIGN")

        resp = await scoped_client.get("/api/registry/entries?page_size=50")
        assert resp.status_code == 200
        namespaces = {item["namespace"] for item in resp.json()["items"]}
        assert namespaces == {"case580-mine"}

    @pytest.mark.asyncio
    async def test_explicit_foreign_namespace_returns_empty(
        self, scoped_client: AsyncClient
    ):
        for prefix in ("case580-mine", "case580-foreign"):
            await Namespace(prefix=prefix).insert()
        await _seed_entry("case580-foreign", "FOREIGN")

        resp = await scoped_client.get(
            "/api/registry/entries?namespace=case580-foreign"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["items"] == []

    @pytest.mark.asyncio
    async def test_explicit_in_scope_namespace_returns_entries(
        self, scoped_client: AsyncClient
    ):
        await Namespace(prefix="case580-mine").insert()
        await _seed_entry("case580-mine", "MINE")

        resp = await scoped_client.get(
            "/api/registry/entries?namespace=case580-mine"
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 1


class TestUnifiedSearchScoping:
    @pytest.mark.asyncio
    async def test_unified_search_scopes_to_in_scope(
        self, scoped_client: AsyncClient
    ):
        for prefix in ("case580-mine", "case580-foreign"):
            await Namespace(prefix=prefix).insert()
        # Same search token present in both namespaces.
        await _seed_entry("case580-mine", "SHARED")
        await _seed_entry("case580-foreign", "SHARED")

        resp = await scoped_client.get(
            "/api/registry/entries/search?q=SHARED&page_size=50"
        )
        assert resp.status_code == 200
        namespaces = {item["namespace"] for item in resp.json()["items"]}
        assert namespaces == {"case580-mine"}


pytestmark = pytest.mark.asyncio
