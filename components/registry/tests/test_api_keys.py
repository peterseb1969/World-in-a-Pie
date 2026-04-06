"""Tests for runtime API key management endpoints."""

import pytest
from httpx import AsyncClient

BASE = "/api/registry/api-keys"


class TestCreateAPIKey:
    """Tests for POST /api-keys."""

    @pytest.mark.asyncio
    async def test_create_key(self, client: AsyncClient, auth_headers: dict):
        response = await client.post(
            BASE,
            json={"name": "test-app", "owner": "ci", "groups": [], "namespaces": ["wip"]},
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "test-app"
        assert data["source"] == "runtime"
        assert data["owner"] == "ci"
        assert data["namespaces"] == ["wip"]
        assert "plaintext_key" in data
        assert len(data["plaintext_key"]) > 20

    @pytest.mark.asyncio
    async def test_create_duplicate_name_rejected(self, client: AsyncClient, auth_headers: dict):
        await client.post(
            BASE,
            json={"name": "dup-key"},
            headers=auth_headers,
        )
        response = await client.post(
            BASE,
            json={"name": "dup-key"},
            headers=auth_headers,
        )
        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_create_config_name_collision_rejected(self, client: AsyncClient, auth_headers: dict):
        # "legacy" is the name of the config-file key from MASTER_API_KEY
        response = await client.post(
            BASE,
            json={"name": "legacy"},
            headers=auth_headers,
        )
        assert response.status_code == 409
        assert "config-file" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_create_with_expiry(self, client: AsyncClient, auth_headers: dict):
        response = await client.post(
            BASE,
            json={"name": "expiring-key", "expires_at": "2099-12-31T23:59:59Z"},
            headers=auth_headers,
        )
        assert response.status_code == 201
        assert response.json()["expires_at"] is not None


class TestListAPIKeys:
    """Tests for GET /api-keys."""

    @pytest.mark.asyncio
    async def test_list_includes_config_and_runtime(self, client: AsyncClient, auth_headers: dict):
        # Create a runtime key
        await client.post(
            BASE,
            json={"name": "list-test"},
            headers=auth_headers,
        )

        response = await client.get(BASE, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        names = [k["name"] for k in data]
        sources = {k["name"]: k["source"] for k in data}

        assert "legacy" in names
        assert sources["legacy"] == "config"
        assert "list-test" in names
        assert sources["list-test"] == "runtime"

    @pytest.mark.asyncio
    async def test_list_no_hashes_exposed(self, client: AsyncClient, auth_headers: dict):
        response = await client.get(BASE, headers=auth_headers)
        assert response.status_code == 200
        for key in response.json():
            assert "key_hash" not in key
            assert "plaintext_key" not in key


class TestGetAPIKey:
    """Tests for GET /api-keys/{name}."""

    @pytest.mark.asyncio
    async def test_get_runtime_key(self, client: AsyncClient, auth_headers: dict):
        await client.post(
            BASE,
            json={"name": "get-test", "description": "a test key"},
            headers=auth_headers,
        )
        response = await client.get(f"{BASE}/get-test", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["description"] == "a test key"

    @pytest.mark.asyncio
    async def test_get_config_key(self, client: AsyncClient, auth_headers: dict):
        response = await client.get(f"{BASE}/legacy", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["source"] == "config"

    @pytest.mark.asyncio
    async def test_get_not_found(self, client: AsyncClient, auth_headers: dict):
        response = await client.get(f"{BASE}/nonexistent", headers=auth_headers)
        assert response.status_code == 404


class TestUpdateAPIKey:
    """Tests for PATCH /api-keys/{name}."""

    @pytest.mark.asyncio
    async def test_update_runtime_key(self, client: AsyncClient, auth_headers: dict):
        await client.post(
            BASE,
            json={"name": "update-test", "groups": []},
            headers=auth_headers,
        )
        response = await client.patch(
            f"{BASE}/update-test",
            json={"groups": ["wip-users"], "description": "updated"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["groups"] == ["wip-users"]
        assert data["description"] == "updated"

    @pytest.mark.asyncio
    async def test_update_config_key_rejected(self, client: AsyncClient, auth_headers: dict):
        response = await client.patch(
            f"{BASE}/legacy",
            json={"description": "nope"},
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert "config-file" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_disable_key(self, client: AsyncClient, auth_headers: dict):
        await client.post(
            BASE,
            json={"name": "disable-test"},
            headers=auth_headers,
        )
        response = await client.patch(
            f"{BASE}/disable-test",
            json={"enabled": False},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["enabled"] is False


class TestDeleteAPIKey:
    """Tests for DELETE /api-keys/{name}."""

    @pytest.mark.asyncio
    async def test_delete_runtime_key(self, client: AsyncClient, auth_headers: dict):
        await client.post(
            BASE,
            json={"name": "delete-test"},
            headers=auth_headers,
        )
        response = await client.delete(f"{BASE}/delete-test", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["status"] == "deleted"

        # Confirm gone
        response = await client.get(f"{BASE}/delete-test", headers=auth_headers)
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_config_key_rejected(self, client: AsyncClient, auth_headers: dict):
        response = await client.delete(f"{BASE}/legacy", headers=auth_headers)
        assert response.status_code == 400
        assert "config-file" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_delete_not_found(self, client: AsyncClient, auth_headers: dict):
        response = await client.delete(f"{BASE}/nonexistent", headers=auth_headers)
        assert response.status_code == 404


class TestSyncEndpoint:
    """Tests for GET /api-keys/sync."""

    @pytest.mark.asyncio
    async def test_sync_returns_hashes(self, client: AsyncClient, auth_headers: dict):
        await client.post(
            BASE,
            json={"name": "sync-test", "namespaces": ["wip"]},
            headers=auth_headers,
        )
        response = await client.get(f"{BASE}/sync", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        record = next(r for r in data if r["name"] == "sync-test")
        assert "key_hash" in record
        assert record["key_hash"].startswith("$2b$")
