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
        assert data["results"][0]["entry_id"] == registry_id


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
                "composite_key": {"internal_id": "IN0190b000-0000-7000-0000-000000000001"}
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
        assert response.json()["results"][0]["status"] == "added"

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
        assert lookup_response.json()["results"][0]["entry_id"] == registry_id


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
    async def test_search_by_merged_entry_id(self, client: AsyncClient, auth_headers: dict):
        """Test that unified search finds entries by merged entry_id (now a synonym)."""
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
        merge_resp = await client.post(
            "/api/registry/synonyms/merge",
            json=[{"preferred_id": preferred_id, "deprecated_id": deprecated_id}],
            headers=auth_headers
        )
        assert merge_resp.json()["results"][0]["status"] == "merged"

        # Verify deprecated entry_id is now a synonym on the preferred entry
        detail = await client.get(
            f"/api/registry/entries/{preferred_id}",
            headers=auth_headers
        )
        assert detail.status_code == 200
        entry = detail.json()
        entry_id_synonyms = [
            s for s in entry["synonyms"]
            if s["composite_key"].get("entry_id") == deprecated_id
        ]
        assert len(entry_id_synonyms) == 1

        # Search for the deprecated ID — should find the preferred entry via search_values
        response = await client.get(
            "/api/registry/entries/search",
            params={"q": deprecated_id},
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        found = [item for item in data["items"] if item["entry_id"] == preferred_id]
        assert len(found) == 1
        assert found[0]["matched_via"] == "synonym_key_value"


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
        assert data["primary_composite_key"]["detail_test"] == "DetailValue123"
        assert "primary_composite_key_hash" in data
        assert isinstance(data["synonyms"], list)
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


class TestIdentityValuesRegistration:
    """Tests for the identity_values registration flow."""

    @pytest.mark.asyncio
    async def test_register_with_identity_values_creates_synonym(self, client: AsyncClient, auth_headers: dict):
        """Test that identity_values creates a synonym with raw values."""
        response = await client.post(
            "/api/registry/entries/register",
            json=[{
                "namespace": "default",
                "entity_type": "documents",
                "composite_key": {"namespace": "default", "template_id": "0190c000-0000-7000-0000-000000000001"},
                "identity_values": {"email": "john@example.com", "name": "John Doe"},
            }],
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["created"] == 1
        result = data["results"][0]
        assert result["status"] == "created"
        assert result["identity_hash"] is not None
        entry_id = result["registry_id"]
        identity_hash = result["identity_hash"]

        # Verify the entry has a synonym with raw identity values
        detail = await client.get(
            f"/api/registry/entries/{entry_id}",
            headers=auth_headers
        )
        assert detail.status_code == 200
        entry = detail.json()
        assert len(entry["synonyms"]) == 1
        syn = entry["synonyms"][0]
        assert syn["composite_key"] == {"email": "john@example.com", "name": "John Doe"}

        # Verify identity_hash was injected into primary composite key
        assert entry["primary_composite_key"]["identity_hash"] == identity_hash

        # Verify raw values appear in search_values
        assert "john@example.com" in entry["search_values"]
        assert "John Doe" in entry["search_values"]

    @pytest.mark.asyncio
    async def test_register_same_identity_values_returns_existing(self, client: AsyncClient, auth_headers: dict):
        """Test that registering same identity_values returns already_exists with same identity_hash."""
        identity_values = {"email": "jane@example.com"}
        composite_key = {"namespace": "default", "template_id": "0190c000-0000-7000-0000-000000000002"}

        # First registration
        resp1 = await client.post(
            "/api/registry/entries/register",
            json=[{
                "namespace": "default",
                "entity_type": "documents",
                "composite_key": composite_key.copy(),
                "identity_values": identity_values,
            }],
            headers=auth_headers
        )
        assert resp1.status_code == 200
        r1 = resp1.json()["results"][0]
        assert r1["status"] == "created"
        first_id = r1["registry_id"]
        first_hash = r1["identity_hash"]

        # Second registration with same identity_values
        resp2 = await client.post(
            "/api/registry/entries/register",
            json=[{
                "namespace": "default",
                "entity_type": "documents",
                "composite_key": composite_key.copy(),
                "identity_values": identity_values,
            }],
            headers=auth_headers
        )
        assert resp2.status_code == 200
        r2 = resp2.json()["results"][0]
        assert r2["status"] == "already_exists"
        assert r2["registry_id"] == first_id
        assert r2["identity_hash"] == first_hash

    @pytest.mark.asyncio
    async def test_register_without_identity_values_no_synonym(self, client: AsyncClient, auth_headers: dict):
        """Test backwards compatibility: no identity_values means no synonym created."""
        response = await client.post(
            "/api/registry/entries/register",
            json=[{
                "namespace": "default",
                "entity_type": "terms",
                "composite_key": {"product_id": "PROD-BW-001"},
            }],
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["created"] == 1
        result = data["results"][0]
        assert result["identity_hash"] is None
        entry_id = result["registry_id"]

        # Verify no synonyms
        detail = await client.get(
            f"/api/registry/entries/{entry_id}",
            headers=auth_headers
        )
        assert detail.status_code == 200
        entry = detail.json()
        assert len(entry["synonyms"]) == 0

    @pytest.mark.asyncio
    async def test_identity_values_searchable_via_unified_search(self, client: AsyncClient, auth_headers: dict):
        """Test that identity_values are searchable via unified search."""
        await client.post(
            "/api/registry/entries/register",
            json=[{
                "namespace": "default",
                "entity_type": "documents",
                "composite_key": {"namespace": "default", "template_id": "0190c000-0000-7000-0000-000000000001"},
                "identity_values": {"employee_id": "EMP-SEARCHTEST-42"},
            }],
            headers=auth_headers
        )

        # Search for identity value
        response = await client.get(
            "/api/registry/entries/search",
            params={"q": "EMP-SEARCHTEST"},
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        assert any("EMP-SEARCHTEST-42" in item.get("matched_value", "") for item in data["items"])


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
