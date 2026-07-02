"""CASE-567 — /register dedup must honour the matched key's OWN namespace.

Two related namespace-blindness bugs in register_keys:

1. Cross-batch: the dedup map was keyed by hash alone and the check compared
   the parent ENTRY's namespace — so a cross-namespace synonym (entry in A,
   synonym under B) did not dedup a /register for B with that key, minting a
   second live owner for (B, entity_type, hash).
2. Intra-batch: seen_in_batch was keyed by hash alone. Mixed NAMESPACES per
   batch are rejected up front (Phase 0a, 422), so the reachable axis is
   ENTITY_TYPE: two items with the same composite key but different entity
   types in one batch wrongly collapsed to already_exists, though the storage
   index (CASE-18, namespace_entity_keyhash_unique_idx) and the cross-batch
   dedup both scope by (namespace, entity_type).
"""

import pytest
from httpx import AsyncClient

from registry.models.composite_key_claim import CompositeKeyClaim
from registry.models.entry import RegistryEntry
from registry.services.hash import HashService

REGISTER = "/api/registry/entries/register"
ADD_SYNONYM = "/api/registry/synonyms/add"


async def _register_one(client: AsyncClient, headers: dict, namespace: str,
                        composite_key: dict, entity_type: str = "terms") -> dict:
    response = await client.post(
        REGISTER,
        json=[{
            "namespace": namespace,
            "entity_type": entity_type,
            "composite_key": composite_key,
        }],
        headers=headers,
    )
    assert response.status_code == 200
    return response.json()["results"][0]


class TestCrossNamespaceSynonymDedup:
    @pytest.mark.asyncio
    async def test_register_matches_cross_namespace_synonym(
        self, client: AsyncClient, auth_headers: dict
    ):
        """A key owned via a cross-namespace synonym dedups /register in the
        synonym's namespace — already_exists resolving to the owning entry,
        not a second live entry."""
        key = {"sku": "CROSS-001"}

        created = await _register_one(client, auth_headers, "vendor1", {"value": "OWNER"})
        assert created["status"] == "created"
        owner_id = created["registry_id"]

        # Synonym under vendor2 (a namespace other than the entry's own).
        response = await client.post(
            ADD_SYNONYM,
            json=[{
                "target_id": owner_id,
                "synonym_namespace": "vendor2",
                "synonym_entity_type": "terms",
                "synonym_composite_key": key,
            }],
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["results"][0]["status"] == "added"

        # /register in vendor2 with the synonym's key → the synonym owns it.
        result = await _register_one(client, auth_headers, "vendor2", key)
        assert result["status"] == "already_exists"
        assert result["registry_id"] == owner_id

        # No second entry was minted, and the claim still has one owner.
        key_hash = HashService.compute_composite_key_hash(key)
        assert await RegistryEntry.find(
            {"primary_composite_key_hash": key_hash}
        ).count() == 0
        assert await CompositeKeyClaim.find(
            {"composite_key_hash": key_hash}
        ).count() == 1

    @pytest.mark.asyncio
    async def test_same_key_other_namespace_still_creates(
        self, client: AsyncClient, auth_headers: dict
    ):
        """The synonym only owns its key in ITS namespace — the same key in an
        unrelated namespace still creates a distinct entry."""
        key = {"sku": "CROSS-002"}

        created = await _register_one(client, auth_headers, "vendor1", {"value": "OWNER2"})
        owner_id = created["registry_id"]
        response = await client.post(
            ADD_SYNONYM,
            json=[{
                "target_id": owner_id,
                "synonym_namespace": "vendor2",
                "synonym_entity_type": "terms",
                "synonym_composite_key": key,
            }],
            headers=auth_headers,
        )
        assert response.json()["results"][0]["status"] == "added"

        # default ≠ vendor2: no synonym owns (default, terms, hash) → created.
        result = await _register_one(client, auth_headers, "default", key)
        assert result["status"] == "created"
        assert result["registry_id"] != owner_id

    @pytest.mark.asyncio
    async def test_same_namespace_dedup_unchanged(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Regression: plain same-namespace primary-key dedup still works."""
        key = {"sku": "SAME-001"}
        first = await _register_one(client, auth_headers, "vendor1", key)
        assert first["status"] == "created"
        second = await _register_one(client, auth_headers, "vendor1", key)
        assert second["status"] == "already_exists"
        assert second["registry_id"] == first["registry_id"]


class TestIntraBatchScope:
    @pytest.mark.asyncio
    async def test_same_key_two_entity_types_one_batch(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Two items with the same composite key for different entity types in
        one batch are two distinct entities — both created. (Mixed namespaces
        can't occur intra-batch: Phase 0a rejects them with 422.)"""
        key = {"sku": "BATCH-001"}
        response = await client.post(
            REGISTER,
            json=[
                {"namespace": "vendor1", "entity_type": "terms", "composite_key": key},
                {"namespace": "vendor1", "entity_type": "templates", "composite_key": key},
            ],
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert [r["status"] for r in data["results"]] == ["created", "created"]
        assert data["results"][0]["registry_id"] != data["results"][1]["registry_id"]

    @pytest.mark.asyncio
    async def test_mixed_namespace_batch_rejected(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Pin the Phase-0a guard: mixed-namespace register batches are 422 —
        which is why the intra-batch dedup's namespace axis is unreachable."""
        key = {"sku": "BATCH-003"}
        response = await client.post(
            REGISTER,
            json=[
                {"namespace": "vendor1", "entity_type": "terms", "composite_key": key},
                {"namespace": "vendor2", "entity_type": "terms", "composite_key": key},
            ],
            headers=auth_headers,
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_same_key_same_namespace_one_batch_dedups(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Regression: intra-batch dedup within ONE namespace still collapses."""
        key = {"sku": "BATCH-002"}
        response = await client.post(
            REGISTER,
            json=[
                {"namespace": "vendor1", "entity_type": "terms", "composite_key": key},
                {"namespace": "vendor1", "entity_type": "terms", "composite_key": key},
            ],
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["results"][0]["status"] == "created"
        assert data["results"][1]["status"] == "already_exists"
        assert data["results"][1]["registry_id"] == data["results"][0]["registry_id"]
