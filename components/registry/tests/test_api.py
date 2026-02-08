"""API tests for the WIP Registry Service."""

import pytest
from httpx import AsyncClient


class TestHealthEndpoints:
    """Tests for health check endpoints."""

    @pytest.mark.asyncio
    async def test_root_endpoint(self, client: AsyncClient):
        """Test root endpoint returns service info."""
        response = await client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "WIP Registry"
        assert "version" in data

    @pytest.mark.asyncio
    async def test_health_endpoint(self, client: AsyncClient):
        """Test health endpoint."""
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"


class TestIdPoolAPI:
    """Tests for ID pool management API."""

    @pytest.mark.asyncio
    async def test_create_id_pool(self, client: AsyncClient, auth_headers: dict):
        """Test creating an ID pool."""
        response = await client.post(
            "/api/registry/id-pools",
            json=[{
                "pool_id": "test-pool",
                "name": "Test Pool",
                "description": "A test ID pool"
            }],
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["status"] == "created"
        assert data[0]["pool_id"] == "test-pool"

    @pytest.mark.asyncio
    async def test_list_id_pools(self, client: AsyncClient, auth_headers: dict):
        """Test listing ID pools."""
        # First create an ID pool
        await client.post(
            "/api/registry/id-pools",
            json=[{"pool_id": "list-test", "name": "List Test"}],
            headers=auth_headers
        )

        response = await client.get(
            "/api/registry/id-pools",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)


class TestRegistrationAPI:
    """Tests for entry registration API."""

    @pytest.mark.asyncio
    async def test_register_key(self, client: AsyncClient, auth_headers: dict):
        """Test registering a composite key."""
        response = await client.post(
            "/api/registry/entries/register",
            json=[{
                "pool_id": "default",
                "composite_key": {"product_id": "PROD-001", "region": "EU"}
            }],
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["created"] == 1
        assert data["results"][0]["status"] == "created"
        assert data["results"][0]["registry_id"] is not None

    @pytest.mark.asyncio
    async def test_register_duplicate_key(self, client: AsyncClient, auth_headers: dict):
        """Test that duplicate keys are detected."""
        key = {"product_id": "PROD-DUP", "region": "US"}

        # Register first time
        response1 = await client.post(
            "/api/registry/entries/register",
            json=[{"pool_id": "default", "composite_key": key}],
            headers=auth_headers
        )
        assert response1.json()["results"][0]["status"] == "created"

        # Try to register again
        response2 = await client.post(
            "/api/registry/entries/register",
            json=[{"pool_id": "default", "composite_key": key}],
            headers=auth_headers
        )
        assert response2.json()["results"][0]["status"] == "already_exists"


class TestLookupAPI:
    """Tests for lookup API."""

    @pytest.mark.asyncio
    async def test_lookup_by_key(self, client: AsyncClient, auth_headers: dict):
        """Test looking up by composite key."""
        key = {"vendor_sku": "SKU-123"}

        # Register
        reg_response = await client.post(
            "/api/registry/entries/register",
            json=[{"pool_id": "default", "composite_key": key}],
            headers=auth_headers
        )
        registry_id = reg_response.json()["results"][0]["registry_id"]

        # Lookup
        response = await client.post(
            "/api/registry/entries/lookup/by-key",
            json=[{"pool_id": "default", "composite_key": key}],
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["found"] == 1
        assert data["results"][0]["preferred_id"] == registry_id


class TestSynonymAPI:
    """Tests for synonym management API."""

    @pytest.mark.asyncio
    async def test_add_synonym(self, client: AsyncClient, auth_headers: dict):
        """Test adding a synonym to an existing entry."""
        # Register primary key
        reg_response = await client.post(
            "/api/registry/entries/register",
            json=[{
                "pool_id": "default",
                "composite_key": {"internal_id": "INT-001"}
            }],
            headers=auth_headers
        )
        registry_id = reg_response.json()["results"][0]["registry_id"]

        # Add synonym
        response = await client.post(
            "/api/registry/synonyms/add",
            json=[{
                "target_pool_id": "default",
                "target_id": registry_id,
                "synonym_pool_id": "vendor1",
                "synonym_composite_key": {"vendor_code": "V1-001"}
            }],
            headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json()[0]["status"] == "added"

        # Verify synonym can be looked up
        lookup_response = await client.post(
            "/api/registry/entries/lookup/by-key",
            json=[{
                "pool_id": "vendor1",
                "composite_key": {"vendor_code": "V1-001"}
            }],
            headers=auth_headers
        )
        assert lookup_response.json()["results"][0]["preferred_id"] == registry_id


class TestSearchAPI:
    """Tests for search API."""

    @pytest.mark.asyncio
    async def test_search_by_field(self, client: AsyncClient, auth_headers: dict):
        """Test searching by field value across pools."""
        # Register entry with synonym
        reg_response = await client.post(
            "/api/registry/entries/register",
            json=[{
                "pool_id": "default",
                "composite_key": {"city": "Berlin", "type": "office"}
            }],
            headers=auth_headers
        )
        registry_id = reg_response.json()["results"][0]["registry_id"]

        # Search by city
        response = await client.post(
            "/api/registry/search/by-fields",
            json=[{
                "field_criteria": {"city": "Berlin"}
            }],
            headers=auth_headers
        )
        assert response.status_code == 200
        results = response.json()["results"][0]["results"]
        assert len(results) >= 1
        assert any(r["registry_id"] == registry_id for r in results)

    @pytest.mark.asyncio
    async def test_search_across_pools(self, client: AsyncClient, auth_headers: dict):
        """Test that search finds entries across all ID pools."""
        # Register and add synonym in different pool
        reg_response = await client.post(
            "/api/registry/entries/register",
            json=[{
                "pool_id": "default",
                "composite_key": {"product": "Widget", "sku": "W-100"}
            }],
            headers=auth_headers
        )
        registry_id = reg_response.json()["results"][0]["registry_id"]

        # Add synonym in vendor pool
        await client.post(
            "/api/registry/synonyms/add",
            json=[{
                "target_pool_id": "default",
                "target_id": registry_id,
                "synonym_pool_id": "vendor2",
                "synonym_composite_key": {"part": "Widget", "code": "WDG"}
            }],
            headers=auth_headers
        )

        # Search for "Widget" across all pools
        response = await client.post(
            "/api/registry/search/across-namespaces",
            json=[{
                "field_criteria": {"product": "Widget"}
            }],
            headers=auth_headers
        )
        assert response.status_code == 200

        # Also search by synonym field
        response2 = await client.post(
            "/api/registry/search/across-namespaces",
            json=[{
                "field_criteria": {"part": "Widget"}
            }],
            headers=auth_headers
        )
        assert response2.status_code == 200
        # Should find via the synonym
        results = response2.json()["results"][0]["results"]
        assert any(r["registry_id"] == registry_id for r in results)
