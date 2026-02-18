"""Extended tests for Registry entry lifecycle operations.

Covers browse, provision, reserve, activate, lookup, update, delete,
and synonym removal -- operations not covered by the existing test_api.py.
"""

import pytest
from httpx import AsyncClient


# =============================================================================
# Helper
# =============================================================================

async def _register_entry(
    client: AsyncClient,
    auth_headers: dict,
    namespace: str = "default",
    entity_type: str = "terms",
    composite_key: dict | None = None,
    source_info: dict | None = None,
    metadata: dict | None = None,
    created_by: str | None = None,
) -> str:
    """Register a single entry and return its entry_id."""
    payload: dict = {
        "namespace": namespace,
        "entity_type": entity_type,
        "composite_key": composite_key or {},
    }
    if source_info is not None:
        payload["source_info"] = source_info
    if metadata is not None:
        payload["metadata"] = metadata
    if created_by is not None:
        payload["created_by"] = created_by

    resp = await client.post(
        "/api/registry/entries/register",
        json=[payload],
        headers=auth_headers,
    )
    assert resp.status_code == 200
    result = resp.json()["results"][0]
    assert result["status"] == "created"
    return result["registry_id"]


# =============================================================================
# Browse Entries
# =============================================================================

class TestBrowseEntries:
    """Tests for the browse entries endpoint."""

    @pytest.mark.asyncio
    async def test_browse_empty(self, client: AsyncClient, auth_headers: dict):
        """Test browsing when no entries exist returns empty list."""
        response = await client.get(
            "/api/registry/entries",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0
        assert data["page"] == 1

    @pytest.mark.asyncio
    async def test_browse_returns_entries(self, client: AsyncClient, auth_headers: dict):
        """Test that registered entries appear in browse results."""
        await _register_entry(client, auth_headers, composite_key={"name": "Browse1"})
        await _register_entry(client, auth_headers, composite_key={"name": "Browse2"})

        response = await client.get(
            "/api/registry/entries",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

    @pytest.mark.asyncio
    async def test_browse_filter_by_namespace(self, client: AsyncClient, auth_headers: dict):
        """Test filtering browse results by namespace."""
        await _register_entry(client, auth_headers, namespace="default", composite_key={"ns_filter": "1"})
        await _register_entry(client, auth_headers, namespace="vendor1", composite_key={"ns_filter": "2"})

        response = await client.get(
            "/api/registry/entries",
            params={"namespace": "vendor1"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert all(item["namespace"] == "vendor1" for item in data["items"])

    @pytest.mark.asyncio
    async def test_browse_filter_by_entity_type(self, client: AsyncClient, auth_headers: dict):
        """Test filtering browse results by entity type."""
        await _register_entry(client, auth_headers, entity_type="terms", composite_key={"et_filter": "1"})
        await _register_entry(client, auth_headers, entity_type="documents", composite_key={"et_filter": "2"})

        response = await client.get(
            "/api/registry/entries",
            params={"entity_type": "documents"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert all(item["entity_type"] == "documents" for item in data["items"])

    @pytest.mark.asyncio
    async def test_browse_filter_by_status(self, client: AsyncClient, auth_headers: dict):
        """Test filtering browse results by status."""
        entry_id = await _register_entry(client, auth_headers, composite_key={"status_filter": "active_one"})
        await _register_entry(client, auth_headers, composite_key={"status_filter": "active_two"})

        # Soft-delete one
        await client.request(
            "DELETE",
            "/api/registry/entries",
            json=[{"entry_id": entry_id}],
            headers=auth_headers,
        )

        # Browse for active only
        response = await client.get(
            "/api/registry/entries",
            params={"status": "active"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["status"] == "active"

        # Browse for inactive
        response2 = await client.get(
            "/api/registry/entries",
            params={"status": "inactive"},
            headers=auth_headers,
        )
        assert response2.status_code == 200
        assert response2.json()["total"] == 1
        assert response2.json()["items"][0]["status"] == "inactive"

    @pytest.mark.asyncio
    async def test_browse_pagination(self, client: AsyncClient, auth_headers: dict):
        """Test browse pagination with page and page_size."""
        # Create 5 entries
        for i in range(5):
            await _register_entry(client, auth_headers, composite_key={"page_item": f"item-{i}"})

        # Page 1, size 2
        resp1 = await client.get(
            "/api/registry/entries",
            params={"page": 1, "page_size": 2},
            headers=auth_headers,
        )
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["total"] == 5
        assert len(data1["items"]) == 2
        assert data1["page"] == 1
        assert data1["page_size"] == 2
        assert data1["pages"] == 3  # ceil(5/2)

        # Page 3, size 2 (should have 1 item)
        resp3 = await client.get(
            "/api/registry/entries",
            params={"page": 3, "page_size": 2},
            headers=auth_headers,
        )
        assert resp3.status_code == 200
        data3 = resp3.json()
        assert len(data3["items"]) == 1

    @pytest.mark.asyncio
    async def test_browse_search_query(self, client: AsyncClient, auth_headers: dict):
        """Test browse with a text search query parameter."""
        await _register_entry(client, auth_headers, composite_key={"product": "UniqueGadget9000"})
        await _register_entry(client, auth_headers, composite_key={"product": "OtherWidget"})

        response = await client.get(
            "/api/registry/entries",
            params={"q": "UniqueGadget"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert "UniqueGadget9000" in str(data["items"][0]["primary_composite_key"])

    @pytest.mark.asyncio
    async def test_browse_entry_fields(self, client: AsyncClient, auth_headers: dict):
        """Test that browse response items contain the expected fields."""
        await _register_entry(
            client, auth_headers,
            composite_key={"check": "fields"},
            created_by="field-tester",
        )

        response = await client.get(
            "/api/registry/entries",
            headers=auth_headers,
        )
        assert response.status_code == 200
        item = response.json()["items"][0]
        assert "entry_id" in item
        assert "namespace" in item
        assert "entity_type" in item
        assert "primary_composite_key" in item
        assert "synonyms_count" in item
        assert "status" in item
        assert "created_at" in item
        assert "created_by" in item
        assert "updated_at" in item


# =============================================================================
# Provision IDs
# =============================================================================

class TestProvisionIds:
    """Tests for the provision IDs endpoint."""

    @pytest.mark.asyncio
    async def test_provision_single_id(self, client: AsyncClient, auth_headers: dict):
        """Test provisioning a single ID."""
        response = await client.post(
            "/api/registry/entries/provision",
            json={
                "namespace": "default",
                "entity_type": "terms",
                "count": 1,
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["namespace"] == "default"
        assert data["entity_type"] == "terms"
        assert data["total"] == 1
        assert len(data["ids"]) == 1
        assert data["ids"][0]["status"] == "reserved"
        assert data["ids"][0]["entry_id"] is not None

    @pytest.mark.asyncio
    async def test_provision_multiple_ids(self, client: AsyncClient, auth_headers: dict):
        """Test provisioning multiple IDs at once."""
        response = await client.post(
            "/api/registry/entries/provision",
            json={
                "namespace": "default",
                "entity_type": "documents",
                "count": 5,
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 5
        assert len(data["ids"]) == 5
        # All IDs should be unique
        entry_ids = [item["entry_id"] for item in data["ids"]]
        assert len(set(entry_ids)) == 5
        # All should be reserved
        assert all(item["status"] == "reserved" for item in data["ids"])

    @pytest.mark.asyncio
    async def test_provision_with_composite_keys(self, client: AsyncClient, auth_headers: dict):
        """Test provisioning IDs with associated composite keys."""
        response = await client.post(
            "/api/registry/entries/provision",
            json={
                "namespace": "default",
                "entity_type": "terms",
                "count": 2,
                "composite_keys": [
                    {"sku": "SKU-A"},
                    {"sku": "SKU-B"},
                ],
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2

    @pytest.mark.asyncio
    async def test_provision_invalid_entity_type(self, client: AsyncClient, auth_headers: dict):
        """Test provisioning with an invalid entity type returns 400."""
        response = await client.post(
            "/api/registry/entries/provision",
            json={
                "namespace": "default",
                "entity_type": "invalid_type",
                "count": 1,
            },
            headers=auth_headers,
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_provision_invalid_namespace(self, client: AsyncClient, auth_headers: dict):
        """Test provisioning with a non-existent namespace returns 404."""
        response = await client.post(
            "/api/registry/entries/provision",
            json={
                "namespace": "nonexistent-ns",
                "entity_type": "terms",
                "count": 1,
            },
            headers=auth_headers,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_provisioned_ids_are_reserved(self, client: AsyncClient, auth_headers: dict):
        """Test that provisioned IDs appear with reserved status in browse."""
        prov_resp = await client.post(
            "/api/registry/entries/provision",
            json={
                "namespace": "default",
                "entity_type": "terms",
                "count": 1,
            },
            headers=auth_headers,
        )
        entry_id = prov_resp.json()["ids"][0]["entry_id"]

        # Check via entry detail
        detail_resp = await client.get(
            f"/api/registry/entries/{entry_id}",
            headers=auth_headers,
        )
        assert detail_resp.status_code == 200
        assert detail_resp.json()["status"] == "reserved"


# =============================================================================
# Reserve Client-Provided IDs
# =============================================================================

class TestReserveIds:
    """Tests for reserving client-provided IDs."""

    @pytest.mark.asyncio
    async def test_reserve_uuid_id(self, client: AsyncClient, auth_headers: dict):
        """Test reserving a client-provided UUID ID."""
        import uuid
        client_id = str(uuid.uuid4())

        response = await client.post(
            "/api/registry/entries/reserve",
            json=[{
                "entry_id": client_id,
                "namespace": "default",
                "entity_type": "terms",
            }],
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["reserved"] == 1
        assert data["results"][0]["status"] == "reserved"
        assert data["results"][0]["entry_id"] == client_id

    @pytest.mark.asyncio
    async def test_reserve_with_composite_key(self, client: AsyncClient, auth_headers: dict):
        """Test reserving an ID with an associated composite key."""
        import uuid
        client_id = str(uuid.uuid4())

        response = await client.post(
            "/api/registry/entries/reserve",
            json=[{
                "entry_id": client_id,
                "namespace": "default",
                "entity_type": "terms",
                "composite_key": {"vendor_code": "VC-999"},
            }],
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["reserved"] == 1

    @pytest.mark.asyncio
    async def test_reserve_duplicate_id_fails(self, client: AsyncClient, auth_headers: dict):
        """Test that reserving a duplicate ID returns already_exists."""
        import uuid
        client_id = str(uuid.uuid4())

        # First reservation
        resp1 = await client.post(
            "/api/registry/entries/reserve",
            json=[{
                "entry_id": client_id,
                "namespace": "default",
                "entity_type": "terms",
            }],
            headers=auth_headers,
        )
        assert resp1.json()["reserved"] == 1

        # Second reservation with same ID
        resp2 = await client.post(
            "/api/registry/entries/reserve",
            json=[{
                "entry_id": client_id,
                "namespace": "default",
                "entity_type": "terms",
            }],
            headers=auth_headers,
        )
        assert resp2.json()["results"][0]["status"] == "already_exists"
        assert resp2.json()["errors"] == 1

    @pytest.mark.asyncio
    async def test_reserve_invalid_entity_type(self, client: AsyncClient, auth_headers: dict):
        """Test reserving with an invalid entity type returns an error."""
        import uuid
        response = await client.post(
            "/api/registry/entries/reserve",
            json=[{
                "entry_id": str(uuid.uuid4()),
                "namespace": "default",
                "entity_type": "invalid_type",
            }],
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["results"][0]["status"] == "error"

    @pytest.mark.asyncio
    async def test_reserve_bulk(self, client: AsyncClient, auth_headers: dict):
        """Test reserving multiple IDs in bulk."""
        import uuid
        items = [
            {
                "entry_id": str(uuid.uuid4()),
                "namespace": "default",
                "entity_type": "terms",
            }
            for _ in range(3)
        ]

        response = await client.post(
            "/api/registry/entries/reserve",
            json=items,
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert data["reserved"] == 3
        assert data["errors"] == 0


# =============================================================================
# Activate Reserved Entries
# =============================================================================

class TestActivateEntries:
    """Tests for activating reserved entries."""

    @pytest.mark.asyncio
    async def test_activate_reserved_entry(self, client: AsyncClient, auth_headers: dict):
        """Test activating a single reserved entry."""
        # Provision a reserved entry
        prov_resp = await client.post(
            "/api/registry/entries/provision",
            json={
                "namespace": "default",
                "entity_type": "terms",
                "count": 1,
            },
            headers=auth_headers,
        )
        entry_id = prov_resp.json()["ids"][0]["entry_id"]

        # Activate it
        response = await client.post(
            "/api/registry/entries/activate",
            json=[{"entry_id": entry_id}],
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["activated"] == 1
        assert data["results"][0]["status"] == "activated"
        assert data["results"][0]["entry_id"] == entry_id

        # Verify it is now active
        detail_resp = await client.get(
            f"/api/registry/entries/{entry_id}",
            headers=auth_headers,
        )
        assert detail_resp.json()["status"] == "active"

    @pytest.mark.asyncio
    async def test_activate_already_active_entry(self, client: AsyncClient, auth_headers: dict):
        """Test activating an already active entry returns already_active."""
        # Register creates an active entry
        entry_id = await _register_entry(client, auth_headers, composite_key={"act_test": "already_active"})

        response = await client.post(
            "/api/registry/entries/activate",
            json=[{"entry_id": entry_id}],
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["results"][0]["status"] == "already_active"

    @pytest.mark.asyncio
    async def test_activate_nonexistent_entry(self, client: AsyncClient, auth_headers: dict):
        """Test activating a non-existent entry returns not_found."""
        response = await client.post(
            "/api/registry/entries/activate",
            json=[{"entry_id": "NONEXISTENT-ID-000"}],
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["results"][0]["status"] == "not_found"
        assert data["errors"] == 1

    @pytest.mark.asyncio
    async def test_activate_inactive_entry_fails(self, client: AsyncClient, auth_headers: dict):
        """Test that activating a soft-deleted (inactive) entry fails."""
        entry_id = await _register_entry(client, auth_headers, composite_key={"inact": "test"})

        # Soft-delete
        await client.request(
            "DELETE",
            "/api/registry/entries",
            json=[{"entry_id": entry_id}],
            headers=auth_headers,
        )

        # Try to activate
        response = await client.post(
            "/api/registry/entries/activate",
            json=[{"entry_id": entry_id}],
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["results"][0]["status"] == "error"
        assert "inactive" in response.json()["results"][0]["error"].lower()

    @pytest.mark.asyncio
    async def test_activate_bulk(self, client: AsyncClient, auth_headers: dict):
        """Test activating multiple reserved entries in bulk."""
        prov_resp = await client.post(
            "/api/registry/entries/provision",
            json={
                "namespace": "default",
                "entity_type": "terms",
                "count": 3,
            },
            headers=auth_headers,
        )
        entry_ids = [item["entry_id"] for item in prov_resp.json()["ids"]]

        response = await client.post(
            "/api/registry/entries/activate",
            json=[{"entry_id": eid} for eid in entry_ids],
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert data["activated"] == 3
        assert data["errors"] == 0


# =============================================================================
# Lookup Entries by ID
# =============================================================================

class TestLookupById:
    """Tests for looking up entries by ID."""

    @pytest.mark.asyncio
    async def test_lookup_existing_entry(self, client: AsyncClient, auth_headers: dict):
        """Test looking up an existing active entry by its ID."""
        entry_id = await _register_entry(client, auth_headers, composite_key={"lookup": "by-id"})

        response = await client.post(
            "/api/registry/entries/lookup/by-id",
            json=[{"entry_id": entry_id}],
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["found"] == 1
        assert data["results"][0]["status"] == "found"
        assert data["results"][0]["entry_id"] == entry_id
        assert data["results"][0]["matched_via"] == "entry_id"

    @pytest.mark.asyncio
    async def test_lookup_not_found(self, client: AsyncClient, auth_headers: dict):
        """Test looking up a non-existent entry returns not_found."""
        response = await client.post(
            "/api/registry/entries/lookup/by-id",
            json=[{"entry_id": "DOES-NOT-EXIST"}],
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["not_found"] == 1
        assert data["results"][0]["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_lookup_with_namespace_filter(self, client: AsyncClient, auth_headers: dict):
        """Test lookup with a namespace filter."""
        entry_id = await _register_entry(
            client, auth_headers,
            namespace="vendor1",
            composite_key={"ns_lookup": "filtered"},
        )

        # Lookup with correct namespace
        resp_ok = await client.post(
            "/api/registry/entries/lookup/by-id",
            json=[{"entry_id": entry_id, "namespace": "vendor1"}],
            headers=auth_headers,
        )
        assert resp_ok.json()["found"] == 1

        # Lookup with wrong namespace
        resp_miss = await client.post(
            "/api/registry/entries/lookup/by-id",
            json=[{"entry_id": entry_id, "namespace": "vendor2"}],
            headers=auth_headers,
        )
        assert resp_miss.json()["not_found"] == 1

    @pytest.mark.asyncio
    async def test_lookup_bulk(self, client: AsyncClient, auth_headers: dict):
        """Test bulk lookup by ID."""
        id1 = await _register_entry(client, auth_headers, composite_key={"bulk_l": "a"})
        id2 = await _register_entry(client, auth_headers, composite_key={"bulk_l": "b"})

        response = await client.post(
            "/api/registry/entries/lookup/by-id",
            json=[
                {"entry_id": id1},
                {"entry_id": id2},
                {"entry_id": "MISSING-ID"},
            ],
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert data["found"] == 2
        assert data["not_found"] == 1

    @pytest.mark.asyncio
    async def test_lookup_inactive_entry_not_found(self, client: AsyncClient, auth_headers: dict):
        """Test that inactive (soft-deleted) entries are not found by lookup."""
        entry_id = await _register_entry(client, auth_headers, composite_key={"inactive_lookup": "test"})

        # Soft-delete
        await client.request(
            "DELETE",
            "/api/registry/entries",
            json=[{"entry_id": entry_id}],
            headers=auth_headers,
        )

        # Lookup should not find it
        response = await client.post(
            "/api/registry/entries/lookup/by-id",
            json=[{"entry_id": entry_id}],
            headers=auth_headers,
        )
        assert response.json()["not_found"] == 1

    @pytest.mark.asyncio
    async def test_lookup_via_composite_key_value(self, client: AsyncClient, auth_headers: dict):
        """Test lookup by ID finds entries via search_values (merged ID as synonym)."""
        # Register two entries and merge them
        id1 = await _register_entry(client, auth_headers, composite_key={"name": "LookupMerge1"})
        id2 = await _register_entry(client, auth_headers, composite_key={"name": "LookupMerge2"})

        # Merge: id1 is preferred, id2 is deprecated
        await client.post(
            "/api/registry/synonyms/merge",
            json=[{"preferred_id": id1, "deprecated_id": id2}],
            headers=auth_headers,
        )

        # Lookup by the deprecated entry_id should resolve to the preferred entry
        response = await client.post(
            "/api/registry/entries/lookup/by-id",
            json=[{"entry_id": id2}],
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["found"] == 1
        assert data["results"][0]["entry_id"] == id1
        assert data["results"][0]["matched_via"] == "composite_key_value"


# =============================================================================
# Update Entries
# =============================================================================

class TestUpdateEntries:
    """Tests for updating registry entries."""

    @pytest.mark.asyncio
    async def test_update_metadata(self, client: AsyncClient, auth_headers: dict):
        """Test updating an entry's metadata."""
        entry_id = await _register_entry(client, auth_headers, composite_key={"update_meta": "test"})

        response = await client.put(
            "/api/registry/entries",
            json=[{
                "entry_id": entry_id,
                "metadata": {"color": "blue", "priority": 1},
                "updated_by": "updater-user",
            }],
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["succeeded"] == 1
        assert data["results"][0]["status"] == "updated"

        # Verify metadata was saved
        detail = await client.get(
            f"/api/registry/entries/{entry_id}",
            headers=auth_headers,
        )
        assert detail.json()["metadata"]["color"] == "blue"
        assert detail.json()["metadata"]["priority"] == 1

    @pytest.mark.asyncio
    async def test_update_source_info(self, client: AsyncClient, auth_headers: dict):
        """Test updating an entry's source_info."""
        entry_id = await _register_entry(client, auth_headers, composite_key={"update_src": "test"})

        response = await client.put(
            "/api/registry/entries",
            json=[{
                "entry_id": entry_id,
                "source_info": {
                    "system_id": "def-store",
                    "endpoint_url": "http://localhost:8002/api/def-store/terms/T-123",
                },
            }],
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["results"][0]["status"] == "updated"

        # Verify
        detail = await client.get(
            f"/api/registry/entries/{entry_id}",
            headers=auth_headers,
        )
        assert detail.json()["source_info"]["system_id"] == "def-store"
        assert "localhost:8002" in detail.json()["source_info"]["endpoint_url"]

    @pytest.mark.asyncio
    async def test_update_nonexistent_entry(self, client: AsyncClient, auth_headers: dict):
        """Test updating a non-existent entry returns not_found."""
        response = await client.put(
            "/api/registry/entries",
            json=[{
                "entry_id": "DOES-NOT-EXIST",
                "metadata": {"key": "value"},
            }],
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["results"][0]["status"] == "not_found"
        assert response.json()["failed"] == 1

    @pytest.mark.asyncio
    async def test_update_inactive_entry_not_found(self, client: AsyncClient, auth_headers: dict):
        """Test that updating an inactive entry returns not_found."""
        entry_id = await _register_entry(client, auth_headers, composite_key={"inact_upd": "test"})

        # Soft-delete
        await client.request(
            "DELETE",
            "/api/registry/entries",
            json=[{"entry_id": entry_id}],
            headers=auth_headers,
        )

        response = await client.put(
            "/api/registry/entries",
            json=[{"entry_id": entry_id, "metadata": {"new": "data"}}],
            headers=auth_headers,
        )
        assert response.json()["results"][0]["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_update_metadata_merges(self, client: AsyncClient, auth_headers: dict):
        """Test that updating metadata merges with existing metadata."""
        entry_id = await _register_entry(
            client, auth_headers,
            composite_key={"merge_meta": "test"},
            metadata={"existing_key": "existing_value"},
        )

        # Update with additional metadata
        await client.put(
            "/api/registry/entries",
            json=[{
                "entry_id": entry_id,
                "metadata": {"new_key": "new_value"},
            }],
            headers=auth_headers,
        )

        # Both keys should exist
        detail = await client.get(
            f"/api/registry/entries/{entry_id}",
            headers=auth_headers,
        )
        meta = detail.json()["metadata"]
        assert meta["existing_key"] == "existing_value"
        assert meta["new_key"] == "new_value"

    @pytest.mark.asyncio
    async def test_update_bulk(self, client: AsyncClient, auth_headers: dict):
        """Test bulk update of multiple entries."""
        id1 = await _register_entry(client, auth_headers, composite_key={"bulk_upd": "a"})
        id2 = await _register_entry(client, auth_headers, composite_key={"bulk_upd": "b"})

        response = await client.put(
            "/api/registry/entries",
            json=[
                {"entry_id": id1, "metadata": {"tag": "first"}},
                {"entry_id": id2, "metadata": {"tag": "second"}},
            ],
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert data["succeeded"] == 2
        assert data["failed"] == 0


# =============================================================================
# Delete Entries (Soft Delete)
# =============================================================================

class TestDeleteEntries:
    """Tests for soft-deleting registry entries."""

    @pytest.mark.asyncio
    async def test_delete_entry(self, client: AsyncClient, auth_headers: dict):
        """Test soft-deleting an entry."""
        entry_id = await _register_entry(client, auth_headers, composite_key={"del_test": "basic"})

        response = await client.request(
            "DELETE",
            "/api/registry/entries",
            json=[{"entry_id": entry_id, "updated_by": "admin-deleter"}],
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["succeeded"] == 1
        assert data["results"][0]["status"] == "deactivated"
        assert data["results"][0]["registry_id"] == entry_id

    @pytest.mark.asyncio
    async def test_delete_sets_inactive_status(self, client: AsyncClient, auth_headers: dict):
        """Test that soft delete changes status to inactive."""
        entry_id = await _register_entry(client, auth_headers, composite_key={"del_status": "check"})

        await client.request(
            "DELETE",
            "/api/registry/entries",
            json=[{"entry_id": entry_id}],
            headers=auth_headers,
        )

        # Verify status via entry detail
        detail = await client.get(
            f"/api/registry/entries/{entry_id}",
            headers=auth_headers,
        )
        assert detail.json()["status"] == "inactive"

    @pytest.mark.asyncio
    async def test_delete_nonexistent_entry(self, client: AsyncClient, auth_headers: dict):
        """Test deleting a non-existent entry returns not_found."""
        response = await client.request(
            "DELETE",
            "/api/registry/entries",
            json=[{"entry_id": "DOES-NOT-EXIST"}],
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["results"][0]["status"] == "not_found"
        assert response.json()["failed"] == 1

    @pytest.mark.asyncio
    async def test_delete_bulk(self, client: AsyncClient, auth_headers: dict):
        """Test bulk soft-deletion of multiple entries."""
        id1 = await _register_entry(client, auth_headers, composite_key={"bulk_del": "a"})
        id2 = await _register_entry(client, auth_headers, composite_key={"bulk_del": "b"})

        response = await client.request(
            "DELETE",
            "/api/registry/entries",
            json=[
                {"entry_id": id1},
                {"entry_id": id2},
            ],
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert data["succeeded"] == 2
        assert data["failed"] == 0

    @pytest.mark.asyncio
    async def test_delete_entry_no_longer_resolvable_by_key(self, client: AsyncClient, auth_headers: dict):
        """Test that a deleted entry cannot be looked up by composite key."""
        key = {"del_resolve": "test-key"}
        entry_id = await _register_entry(client, auth_headers, composite_key=key)

        # Soft-delete
        await client.request(
            "DELETE",
            "/api/registry/entries",
            json=[{"entry_id": entry_id}],
            headers=auth_headers,
        )

        # Lookup by key should not find it (only active entries are resolvable)
        response = await client.post(
            "/api/registry/entries/lookup/by-key",
            json=[{
                "namespace": "default",
                "entity_type": "terms",
                "composite_key": key,
            }],
            headers=auth_headers,
        )
        assert response.json()["not_found"] == 1


# =============================================================================
# Remove Synonym from Entry
# =============================================================================

class TestRemoveSynonym:
    """Tests for removing synonyms from entries."""

    @pytest.mark.asyncio
    async def test_remove_synonym(self, client: AsyncClient, auth_headers: dict):
        """Test removing a synonym from an entry."""
        # Register entry with a synonym
        entry_id = await _register_entry(
            client, auth_headers,
            composite_key={"primary": "remove-syn-test"},
        )

        synonym_key = {"vendor_code": "VC-REMOVE-001"}
        await client.post(
            "/api/registry/synonyms/add",
            json=[{
                "target_id": entry_id,
                "synonym_namespace": "vendor1",
                "synonym_entity_type": "terms",
                "synonym_composite_key": synonym_key,
            }],
            headers=auth_headers,
        )

        # Verify synonym exists
        detail_before = await client.get(
            f"/api/registry/entries/{entry_id}",
            headers=auth_headers,
        )
        assert len(detail_before.json()["synonyms"]) == 1

        # Remove the synonym
        response = await client.post(
            "/api/registry/synonyms/remove",
            json=[{
                "target_id": entry_id,
                "synonym_namespace": "vendor1",
                "synonym_entity_type": "terms",
                "synonym_composite_key": synonym_key,
                "updated_by": "remover-user",
            }],
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["succeeded"] == 1
        assert data["results"][0]["status"] == "removed"

        # Verify synonym is gone
        detail_after = await client.get(
            f"/api/registry/entries/{entry_id}",
            headers=auth_headers,
        )
        assert len(detail_after.json()["synonyms"]) == 0

    @pytest.mark.asyncio
    async def test_remove_synonym_not_found_entry(self, client: AsyncClient, auth_headers: dict):
        """Test removing a synonym from a non-existent entry."""
        response = await client.post(
            "/api/registry/synonyms/remove",
            json=[{
                "target_id": "NONEXISTENT-ID",
                "synonym_namespace": "default",
                "synonym_entity_type": "terms",
                "synonym_composite_key": {"key": "val"},
            }],
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["results"][0]["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_remove_nonexistent_synonym(self, client: AsyncClient, auth_headers: dict):
        """Test removing a synonym that does not exist on the entry."""
        entry_id = await _register_entry(
            client, auth_headers,
            composite_key={"primary": "no-such-syn"},
        )

        response = await client.post(
            "/api/registry/synonyms/remove",
            json=[{
                "target_id": entry_id,
                "synonym_namespace": "default",
                "synonym_entity_type": "terms",
                "synonym_composite_key": {"nonexistent": "synonym"},
            }],
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["results"][0]["status"] == "not_found"
        assert "not found" in response.json()["results"][0].get("error", "").lower()

    @pytest.mark.asyncio
    async def test_remove_synonym_lookup_no_longer_resolves(self, client: AsyncClient, auth_headers: dict):
        """Test that after removing a synonym, lookup by that key no longer resolves."""
        entry_id = await _register_entry(
            client, auth_headers,
            composite_key={"primary": "syn-resolve-test"},
        )

        synonym_key = {"vendor_code": "VC-RESOLVE-001"}
        await client.post(
            "/api/registry/synonyms/add",
            json=[{
                "target_id": entry_id,
                "synonym_namespace": "vendor1",
                "synonym_entity_type": "terms",
                "synonym_composite_key": synonym_key,
            }],
            headers=auth_headers,
        )

        # Verify lookup works before removal
        lookup_before = await client.post(
            "/api/registry/entries/lookup/by-key",
            json=[{
                "namespace": "vendor1",
                "entity_type": "terms",
                "composite_key": synonym_key,
            }],
            headers=auth_headers,
        )
        assert lookup_before.json()["found"] == 1

        # Remove synonym
        await client.post(
            "/api/registry/synonyms/remove",
            json=[{
                "target_id": entry_id,
                "synonym_namespace": "vendor1",
                "synonym_entity_type": "terms",
                "synonym_composite_key": synonym_key,
            }],
            headers=auth_headers,
        )

        # Verify lookup no longer resolves
        lookup_after = await client.post(
            "/api/registry/entries/lookup/by-key",
            json=[{
                "namespace": "vendor1",
                "entity_type": "terms",
                "composite_key": synonym_key,
            }],
            headers=auth_headers,
        )
        assert lookup_after.json()["not_found"] == 1

    @pytest.mark.asyncio
    async def test_remove_synonym_bulk(self, client: AsyncClient, auth_headers: dict):
        """Test removing multiple synonyms in bulk."""
        entry_id = await _register_entry(
            client, auth_headers,
            composite_key={"primary": "bulk-syn-remove"},
        )

        syn1 = {"code": "SYN-BULK-1"}
        syn2 = {"code": "SYN-BULK-2"}
        await client.post(
            "/api/registry/synonyms/add",
            json=[
                {
                    "target_id": entry_id,
                    "synonym_namespace": "vendor1",
                    "synonym_entity_type": "terms",
                    "synonym_composite_key": syn1,
                },
                {
                    "target_id": entry_id,
                    "synonym_namespace": "vendor2",
                    "synonym_entity_type": "terms",
                    "synonym_composite_key": syn2,
                },
            ],
            headers=auth_headers,
        )

        # Remove both
        response = await client.post(
            "/api/registry/synonyms/remove",
            json=[
                {
                    "target_id": entry_id,
                    "synonym_namespace": "vendor1",
                    "synonym_entity_type": "terms",
                    "synonym_composite_key": syn1,
                },
                {
                    "target_id": entry_id,
                    "synonym_namespace": "vendor2",
                    "synonym_entity_type": "terms",
                    "synonym_composite_key": syn2,
                },
            ],
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["succeeded"] == 2
        assert data["failed"] == 0
