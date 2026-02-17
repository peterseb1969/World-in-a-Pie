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


class TestRegistrationAPI:
    """Tests for entry registration API."""

    @pytest.mark.asyncio
    async def test_register_key(self, client: AsyncClient, auth_headers: dict):
        """Test registering a composite key."""
        response = await client.post(
            "/api/registry/entries/register",
            json=[{
                "namespace": "default",
                "entity_type": "terms",
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
    async def test_register_empty_composite_key(self, client: AsyncClient, auth_headers: dict):
        """Test registering with empty composite key always generates unique IDs."""
        # Register twice with empty composite key
        response1 = await client.post(
            "/api/registry/entries/register",
            json=[{
                "namespace": "default",
                "entity_type": "templates",
                "composite_key": {}
            }],
            headers=auth_headers
        )
        assert response1.status_code == 200
        data1 = response1.json()
        assert data1["created"] == 1
        id1 = data1["results"][0]["registry_id"]

        response2 = await client.post(
            "/api/registry/entries/register",
            json=[{
                "namespace": "default",
                "entity_type": "templates",
                "composite_key": {}
            }],
            headers=auth_headers
        )
        assert response2.status_code == 200
        data2 = response2.json()
        assert data2["created"] == 1
        id2 = data2["results"][0]["registry_id"]

        # Must be different IDs — no dedup for empty composite keys
        assert id1 != id2

    @pytest.mark.asyncio
    async def test_register_omitted_composite_key(self, client: AsyncClient, auth_headers: dict):
        """Test registering without composite_key field (defaults to empty)."""
        response = await client.post(
            "/api/registry/entries/register",
            json=[{
                "namespace": "default",
                "entity_type": "templates"
            }],
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["created"] == 1
        assert data["results"][0]["status"] == "created"

    @pytest.mark.asyncio
    async def test_register_duplicate_key(self, client: AsyncClient, auth_headers: dict):
        """Test that duplicate keys are detected."""
        key = {"product_id": "PROD-DUP", "region": "US"}

        # Register first time
        response1 = await client.post(
            "/api/registry/entries/register",
            json=[{"namespace": "default", "entity_type": "terms", "composite_key": key}],
            headers=auth_headers
        )
        assert response1.json()["results"][0]["status"] == "created"

        # Try to register again
        response2 = await client.post(
            "/api/registry/entries/register",
            json=[{"namespace": "default", "entity_type": "terms", "composite_key": key}],
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
            json=[{"namespace": "default", "entity_type": "terms", "composite_key": key}],
            headers=auth_headers
        )
        registry_id = reg_response.json()["results"][0]["registry_id"]

        # Lookup
        response = await client.post(
            "/api/registry/entries/lookup/by-key",
            json=[{"namespace": "default", "entity_type": "terms", "composite_key": key}],
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
                "namespace": "default",
                "entity_type": "terms",
                "composite_key": {"internal_id": "INT-001"}
            }],
            headers=auth_headers
        )
        registry_id = reg_response.json()["results"][0]["registry_id"]

        # Add synonym
        response = await client.post(
            "/api/registry/synonyms/add",
            json=[{
                "target_id": registry_id,
                "synonym_namespace": "vendor1",
                "synonym_entity_type": "terms",
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
                "namespace": "vendor1",
                "entity_type": "terms",
                "composite_key": {"vendor_code": "V1-001"}
            }],
            headers=auth_headers
        )
        assert lookup_response.json()["results"][0]["preferred_id"] == registry_id


class TestUnifiedSearchAPI:
    """Tests for the unified search endpoint."""

    @pytest.mark.asyncio
    async def test_search_by_entry_id(self, client: AsyncClient, auth_headers: dict):
        """Test that unified search finds entries by entry_id substring."""
        reg_response = await client.post(
            "/api/registry/entries/register",
            json=[{
                "namespace": "default",
                "entity_type": "terms",
                "composite_key": {"name": "Alpha Widget"}
            }],
            headers=auth_headers
        )
        entry_id = reg_response.json()["results"][0]["registry_id"]

        # Search by a substring of the entry_id
        search_q = entry_id[:6]
        response = await client.get(
            "/api/registry/entries/search",
            params={"q": search_q},
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        found = [item for item in data["items"] if item["entry_id"] == entry_id]
        assert len(found) == 1
        assert found[0]["matched_via"] == "entry_id"

    @pytest.mark.asyncio
    async def test_search_by_composite_key_value(self, client: AsyncClient, auth_headers: dict):
        """Test that unified search finds entries by composite key value."""
        await client.post(
            "/api/registry/entries/register",
            json=[{
                "namespace": "default",
                "entity_type": "terms",
                "composite_key": {"product_name": "Titanium Gizmo"}
            }],
            headers=auth_headers
        )

        response = await client.get(
            "/api/registry/entries/search",
            params={"q": "Titanium"},
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        assert any("Titanium" in item["matched_value"] for item in data["items"])

    @pytest.mark.asyncio
    async def test_search_by_synonym_value(self, client: AsyncClient, auth_headers: dict):
        """Test that unified search finds entries via synonym composite key values."""
        reg_response = await client.post(
            "/api/registry/entries/register",
            json=[{
                "namespace": "default",
                "entity_type": "terms",
                "composite_key": {"code": "MAIN-X1"}
            }],
            headers=auth_headers
        )
        entry_id = reg_response.json()["results"][0]["registry_id"]

        # Add a synonym
        await client.post(
            "/api/registry/synonyms/add",
            json=[{
                "target_id": entry_id,
                "synonym_namespace": "vendor1",
                "synonym_entity_type": "terms",
                "synonym_composite_key": {"vendor_code": "VENDOR-SPECIAL-99"}
            }],
            headers=auth_headers
        )

        response = await client.get(
            "/api/registry/entries/search",
            params={"q": "VENDOR-SPECIAL"},
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        found = [item for item in data["items"] if item["entry_id"] == entry_id]
        assert len(found) == 1
        assert found[0]["matched_via"] == "synonym_key_value"
        assert "VENDOR-SPECIAL" in found[0]["resolution_path"]

    @pytest.mark.asyncio
    async def test_search_with_namespace_filter(self, client: AsyncClient, auth_headers: dict):
        """Test search filtering by namespace."""
        await client.post(
            "/api/registry/entries/register",
            json=[
                {"namespace": "default", "entity_type": "terms", "composite_key": {"item": "FilterTestItem"}},
                {"namespace": "vendor1", "entity_type": "terms", "composite_key": {"item": "FilterTestItem"}},
            ],
            headers=auth_headers
        )

        response = await client.get(
            "/api/registry/entries/search",
            params={"q": "FilterTestItem", "namespace": "vendor1"},
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert all(item["namespace"] == "vendor1" for item in data["items"])

    @pytest.mark.asyncio
    async def test_search_pagination(self, client: AsyncClient, auth_headers: dict):
        """Test search pagination."""
        # Register 3 entries
        for i in range(3):
            await client.post(
                "/api/registry/entries/register",
                json=[{
                    "namespace": "default",
                    "entity_type": "terms",
                    "composite_key": {"batch_item": f"PaginationTest-{i}"}
                }],
                headers=auth_headers
            )

        response = await client.get(
            "/api/registry/entries/search",
            params={"q": "PaginationTest", "page": 1, "page_size": 2},
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert len(data["items"]) == 2
        assert data["page"] == 1
        assert data["page_size"] == 2

    @pytest.mark.asyncio
    async def test_search_by_additional_id(self, client: AsyncClient, auth_headers: dict):
        """Test that unified search finds entries by merged additional_id."""
        # Register two entries and merge them
        reg1 = await client.post(
            "/api/registry/entries/register",
            json=[{
                "namespace": "default",
                "entity_type": "terms",
                "composite_key": {"name": "Preferred Entry For Merge Search"}
            }],
            headers=auth_headers
        )
        preferred_id = reg1.json()["results"][0]["registry_id"]

        reg2 = await client.post(
            "/api/registry/entries/register",
            json=[{
                "namespace": "default",
                "entity_type": "terms",
                "composite_key": {"name": "Deprecated Entry For Merge Search"}
            }],
            headers=auth_headers
        )
        deprecated_id = reg2.json()["results"][0]["registry_id"]

        # Merge
        await client.post(
            "/api/registry/synonyms/merge",
            json=[{"preferred_id": preferred_id, "deprecated_id": deprecated_id}],
            headers=auth_headers
        )

        # Search for the deprecated ID — should find the preferred entry
        response = await client.get(
            "/api/registry/entries/search",
            params={"q": deprecated_id},
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        found = [item for item in data["items"] if item["entry_id"] == preferred_id]
        assert len(found) == 1
        assert found[0]["matched_via"] == "additional_id"


class TestEntryDetailAPI:
    """Tests for the entry detail endpoint."""

    @pytest.mark.asyncio
    async def test_get_entry_detail(self, client: AsyncClient, auth_headers: dict):
        """Test fetching full entry details."""
        reg_response = await client.post(
            "/api/registry/entries/register",
            json=[{
                "namespace": "default",
                "entity_type": "terms",
                "composite_key": {"detail_test": "DetailValue123"}
            }],
            headers=auth_headers
        )
        entry_id = reg_response.json()["results"][0]["registry_id"]

        response = await client.get(
            f"/api/registry/entries/{entry_id}",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["entry_id"] == entry_id
        assert data["namespace"] == "default"
        assert data["entity_type"] == "terms"
        assert data["status"] == "active"
        assert data["is_preferred"] is True
        assert data["primary_composite_key"]["detail_test"] == "DetailValue123"
        assert "primary_composite_key_hash" in data
        assert isinstance(data["synonyms"], list)
        assert isinstance(data["additional_ids"], list)
        assert isinstance(data["search_values"], list)
        assert "DetailValue123" in data["search_values"]

    @pytest.mark.asyncio
    async def test_get_entry_detail_not_found(self, client: AsyncClient, auth_headers: dict):
        """Test 404 for non-existent entry."""
        response = await client.get(
            "/api/registry/entries/NONEXISTENT-ID-999",
            headers=auth_headers
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_entry_detail_with_synonyms(self, client: AsyncClient, auth_headers: dict):
        """Test detail includes synonyms."""
        reg_response = await client.post(
            "/api/registry/entries/register",
            json=[{
                "namespace": "default",
                "entity_type": "terms",
                "composite_key": {"main_key": "DetailSynTest"}
            }],
            headers=auth_headers
        )
        entry_id = reg_response.json()["results"][0]["registry_id"]

        # Add synonym
        await client.post(
            "/api/registry/synonyms/add",
            json=[{
                "target_id": entry_id,
                "synonym_namespace": "vendor1",
                "synonym_entity_type": "terms",
                "synonym_composite_key": {"alt_key": "VendorDetailSynTest"}
            }],
            headers=auth_headers
        )

        response = await client.get(
            f"/api/registry/entries/{entry_id}",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["synonyms"]) == 1
        assert data["synonyms"][0]["namespace"] == "vendor1"
        assert data["synonyms"][0]["composite_key"]["alt_key"] == "VendorDetailSynTest"


class TestSearchAPI:
    """Tests for search API."""

    @pytest.mark.asyncio
    async def test_search_by_field(self, client: AsyncClient, auth_headers: dict):
        """Test searching by field value across namespaces."""
        # Register entry with synonym
        reg_response = await client.post(
            "/api/registry/entries/register",
            json=[{
                "namespace": "default",
                "entity_type": "terms",
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
    async def test_search_across_namespaces(self, client: AsyncClient, auth_headers: dict):
        """Test that search finds entries across all namespaces."""
        # Register and add synonym in different namespace
        reg_response = await client.post(
            "/api/registry/entries/register",
            json=[{
                "namespace": "default",
                "entity_type": "terms",
                "composite_key": {"product": "Widget", "sku": "W-100"}
            }],
            headers=auth_headers
        )
        registry_id = reg_response.json()["results"][0]["registry_id"]

        # Add synonym in vendor namespace
        await client.post(
            "/api/registry/synonyms/add",
            json=[{
                "target_id": registry_id,
                "synonym_namespace": "vendor2",
                "synonym_entity_type": "terms",
                "synonym_composite_key": {"part": "Widget", "code": "WDG"}
            }],
            headers=auth_headers
        )

        # Search for "Widget" across all namespaces
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
