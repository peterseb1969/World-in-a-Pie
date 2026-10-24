"""CASE-847 — the raw-values synonym splits from the identity-hash feed.

Under resolved-referent identity, document-store sends resolved referent IDs
as identity_values (the hash feed) and the submitted spellings as
synonym_values (the business-key lookup surface). The registry mints the
raw-values synonym from synonym_values only — so human-readable lookups keep
resolving while the hash converges on referents. Without synonym_values no
synonym is minted: identity_values alone would be an unqualified synonym,
claiming the bare values for every template in the namespace.
"""

import pytest
from httpx import AsyncClient


async def _register(client, auth_headers, item) -> dict:
    r = await client.post("/api/registry/entries/register", json=[item], headers=auth_headers)
    assert r.status_code == 200, r.text
    return r.json()["results"][0]


async def _lookup_by_key(client, auth_headers, namespace, entity_type, composite_key) -> dict:
    r = await client.post(
        "/api/registry/entries/lookup/by-key",
        json=[{"namespace": namespace, "entity_type": entity_type, "composite_key": composite_key}],
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["results"][0]


class TestSynonymValuesSplit:
    @pytest.mark.asyncio
    async def test_synonym_minted_from_synonym_values_not_identity_values(
        self, client: AsyncClient, auth_headers: dict,
    ):
        resolved = {"app": "0190b000-0000-7000-8000-000000000123"}  # term_id feed
        submitted = {"app": "ClinTrial"}  # human spelling
        res = await _register(client, auth_headers, {
            "namespace": "default",
            "entity_type": "documents",
            "composite_key": {"ns": "default", "template_id": "PROJECT"},
            "identity_values": resolved,
            "synonym_values": submitted,
        })
        assert res["status"] == "created"
        assert res.get("identity_hash")

        # The human spelling resolves…
        looked = await _lookup_by_key(
            client, auth_headers, "default", "documents", submitted,
        )
        assert looked["status"] == "found"
        assert looked["entry_id"] == res["registry_id"]

        # …and the resolved-ID dict was NOT minted as the synonym.
        looked_resolved = await _lookup_by_key(
            client, auth_headers, "default", "documents", resolved,
        )
        assert looked_resolved["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_without_synonym_values_no_synonym_is_minted(
        self, client: AsyncClient, auth_headers: dict,
    ):
        idv = {"email": "alice@example.com"}
        res = await _register(client, auth_headers, {
            "namespace": "default",
            "entity_type": "documents",
            "composite_key": {"ns": "default", "template_id": "PERSON_847"},
            "identity_values": idv,
        })
        assert res["status"] == "created"
        assert res.get("identity_hash")  # dedup still keys on the identity
        looked = await _lookup_by_key(client, auth_headers, "default", "documents", idv)
        assert looked["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_skip_flag_suppresses_the_synonym_values_synonym_too(
        self, client: AsyncClient, auth_headers: dict,
    ):
        res = await _register(client, auth_headers, {
            "namespace": "default",
            "entity_type": "documents",
            "composite_key": {"ns": "default", "template_id": "EDGE_847"},
            "identity_values": {"source_ref": "D-1", "target_ref": "D-2"},
            "synonym_values": {"source_ref": "alpha", "target_ref": "beta"},
            "skip_identity_value_synonym": True,
        })
        assert res["status"] == "created"
        looked = await _lookup_by_key(
            client, auth_headers, "default", "documents",
            {"source_ref": "alpha", "target_ref": "beta"},
        )
        assert looked["status"] == "not_found"
