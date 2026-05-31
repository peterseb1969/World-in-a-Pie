"""CASE-430 — identity-values synonym must be suppressible for edge types.

Registry-level coverage of the `skip_identity_value_synonym` flag: with it set,
the registry still computes + injects identity_hash into the primary key (so
dedup/versioning is unaffected) but does NOT mint the bare identity-values
synonym (which omits the template and collides across edge types between the
same source/target pair). document-store sets this flag for relationship
templates.
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


class TestSkipIdentityValueSynonym:
    @pytest.mark.asyncio
    async def test_flag_suppresses_synonym_but_keeps_identity_hash(self, client: AsyncClient, auth_headers: dict):
        idv = {"source_ref": "DOC-A", "target_ref": "DOC-B"}
        res = await _register(client, auth_headers, {
            "namespace": "default",
            "entity_type": "documents",
            "composite_key": {"ns": "default", "template_id": "REFERENCES"},
            "identity_values": idv,
            "skip_identity_value_synonym": True,
        })
        assert res["status"] == "created"
        # identity_hash still computed (primary dedup intact).
        assert res.get("identity_hash")
        # The bare identity-values synonym was NOT created → lookup by those
        # raw values does not resolve.
        looked = await _lookup_by_key(client, auth_headers, "default", "documents", idv)
        assert looked["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_default_still_creates_synonym(self, client: AsyncClient, auth_headers: dict):
        # Regression guard: without the flag, the identity-values synonym is
        # created and resolves (normal-document behavior, unchanged).
        idv = {"case_number": "777"}
        res = await _register(client, auth_headers, {
            "namespace": "default",
            "entity_type": "documents",
            "composite_key": {"ns": "default", "template_id": "CASE_RECORD"},
            "identity_values": idv,
        })
        assert res["status"] == "created"
        looked = await _lookup_by_key(client, auth_headers, "default", "documents", idv)
        assert looked["status"] == "found"
        assert looked["entry_id"] == res["registry_id"]

    @pytest.mark.asyncio
    async def test_two_edge_types_same_pair_coexist(self, client: AsyncClient, auth_headers: dict):
        # The CASE-430 scenario: a REFERENCES and a SUPERSEDES edge between the
        # same {source_ref, target_ref}. With the flag, neither mints the bare
        # synonym, so they don't collide on it — both register cleanly.
        idv = {"source_ref": "S1", "target_ref": "T1"}
        ref = await _register(client, auth_headers, {
            "namespace": "default", "entity_type": "documents",
            "composite_key": {"ns": "default", "template_id": "REFERENCES"},
            "identity_values": idv, "skip_identity_value_synonym": True,
        })
        sup = await _register(client, auth_headers, {
            "namespace": "default", "entity_type": "documents",
            "composite_key": {"ns": "default", "template_id": "SUPERSEDES"},
            "identity_values": idv, "skip_identity_value_synonym": True,
        })
        assert ref["status"] == "created"
        assert sup["status"] == "created"
        # Distinct entries (different template_id in the primary key).
        assert ref["registry_id"] != sup["registry_id"]
        # Same identity_hash (both derive it from {source_ref, target_ref}) —
        # but no synonym collision because neither created the bare synonym.
        assert ref["identity_hash"] == sup["identity_hash"]
