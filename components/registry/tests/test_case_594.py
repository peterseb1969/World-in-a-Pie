"""CASE-594 — GET /api-keys/sync must gate on wip-services/wip-admins.

The endpoint returns key_hash values for every enabled key. Before the
fix its only dependency was require_api_key, which checks authentication
only (no group inspection), so any valid API key — including a
namespace-scoped or wip-users key — could pull the full enabled-key
inventory (names, owners, groups, namespaces, bcrypt hashes).

The fix gates it with require_groups(["wip-services", "wip-admins"]).
These tests pin both directions: an unprivileged authenticated key is
403'd, and a privileged (wip-services) key still gets the hashes so the
legitimate consumer — each service's key-sync poller — keeps working.

Auth model note: create_api_key registers the new runtime key with the
live APIKeyProvider immediately, so the plaintext returned at creation
authenticates in-process without the (prod-only) key-sync poll.
"""

import pytest
from httpx import AsyncClient

BASE = "/api/registry/api-keys"


async def _create_key_with_groups(
    client: AsyncClient, auth_headers: dict, name: str, groups: list[str]
) -> str:
    """Create a runtime key with the given groups; return its plaintext."""
    resp = await client.post(
        BASE,
        json={"name": name, "groups": groups, "namespaces": ["wip"]},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    plaintext = resp.json()["plaintext_key"]
    assert plaintext
    return plaintext


class TestSyncRequiresPrivilegedGroup:
    """The /sync endpoint gates on wip-services or wip-admins (CASE-594)."""

    @pytest.mark.asyncio
    async def test_unprivileged_key_is_forbidden(
        self, client: AsyncClient, auth_headers: dict
    ):
        # A wip-users key authenticates but must NOT reach key hashes.
        plaintext = await _create_key_with_groups(
            client, auth_headers, "case594-unpriv", ["wip-users"]
        )
        resp = await client.get(f"{BASE}/sync", headers={"X-API-Key": plaintext})
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_namespace_scoped_key_with_no_groups_is_forbidden(
        self, client: AsyncClient, auth_headers: dict
    ):
        # A bare namespace-scoped key (no privileged group) is also blocked.
        plaintext = await _create_key_with_groups(
            client, auth_headers, "case594-scoped", []
        )
        resp = await client.get(f"{BASE}/sync", headers={"X-API-Key": plaintext})
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_wip_services_key_still_gets_hashes(
        self, client: AsyncClient, auth_headers: dict
    ):
        # The legitimate consumer (service key-sync poller) must still work.
        plaintext = await _create_key_with_groups(
            client, auth_headers, "case594-service", ["wip-services"]
        )
        resp = await client.get(f"{BASE}/sync", headers={"X-API-Key": plaintext})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        # The privileged caller sees enabled keys with their hashes.
        record = next(r for r in data if r["name"] == "case594-service")
        assert record["key_hash"].startswith("$2b$")

    @pytest.mark.asyncio
    async def test_admin_config_key_still_gets_hashes(
        self, client: AsyncClient, auth_headers: dict
    ):
        # The master/config key (wip-admins) — the fixture's auth_headers —
        # remains allowed.
        resp = await client.get(f"{BASE}/sync", headers=auth_headers)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @pytest.mark.asyncio
    async def test_no_auth_is_rejected(self, client: AsyncClient):
        resp = await client.get(f"{BASE}/sync")
        assert resp.status_code in (401, 403)
