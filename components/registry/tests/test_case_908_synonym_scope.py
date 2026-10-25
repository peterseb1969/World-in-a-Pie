"""A synonym takes its entry's scope; an unknown entity type is an error.

Provenance: CASE-908 — /synonyms/add stored the namespace and entity type the
caller sent. A singular "document" made a synonym no lookup could ever match
(WIP-RC's form did this for every synonym it added, reporting success). A
valid but foreign scope was worse: the synonym resolved, to the wrong entity —
another namespace's key, or a term id answering for a document. Lookups
reported an unknown entity type as not_found, indistinguishable from a
missing key.
"""

import pytest
from httpx import AsyncClient

from registry.models.composite_key_claim import CompositeKeyClaim
from registry.models.entry import RegistryEntry, Synonym
from registry.services.hash import HashService

BASE = "/api/registry"


async def _register(
    client: AsyncClient, headers: dict, value: str,
    namespace: str = "default", entity_type: str = "documents",
) -> str:
    resp = await client.post(
        f"{BASE}/entries/register",
        json=[{"namespace": namespace, "entity_type": entity_type, "composite_key": {"value": value}}],
        headers=headers,
    )
    result = resp.json()["results"][0]
    assert result["status"] == "created", result
    return result["registry_id"]


async def _add(client: AsyncClient, headers: dict, **item) -> dict:
    resp = await client.post(f"{BASE}/synonyms/add", json=[item], headers=headers)
    assert resp.status_code == 200
    return resp.json()["results"][0]


async def _lookup(client: AsyncClient, headers: dict, namespace: str, entity_type: str, key: dict) -> dict:
    resp = await client.post(
        f"{BASE}/entries/lookup/by-key",
        json=[{"namespace": namespace, "entity_type": entity_type, "composite_key": key}],
        headers=headers,
    )
    assert resp.status_code == 200
    return resp.json()["results"][0]


async def _plant_foreign_synonym(entry_id: str, namespace: str, entity_type: str, key: dict) -> None:
    """Store a synonym outside its entry's scope, as /synonyms/add once did."""
    key_hash = HashService.compute_composite_key_hash(key)
    entry = await RegistryEntry.find_one({"entry_id": entry_id})
    assert entry is not None
    entry.synonyms.append(Synonym(
        namespace=namespace, entity_type=entity_type,
        composite_key=key, composite_key_hash=key_hash,
    ))
    entry.rebuild_search_values()
    await entry.save()
    await CompositeKeyClaim.claim(namespace, entity_type, key_hash, entry_id, "synonym")


# ---------------------------------------------------------------------------
# /synonyms/add
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_synonym_without_scope_takes_its_entrys(client: AsyncClient, auth_headers: dict):
    doc = await _register(client, auth_headers, "c908-noscope")
    key = {"erp_id": "c908-noscope"}
    assert (await _add(client, auth_headers, target_id=doc, synonym_composite_key=key))["status"] == "added"

    entry = await RegistryEntry.find_one({"entry_id": doc})
    synonym = entry.find_synonym_by_hash(HashService.compute_composite_key_hash(key))
    assert (synonym.namespace, synonym.entity_type) == ("default", "documents")
    assert (await _lookup(client, auth_headers, "default", "documents", key))["entry_id"] == doc


@pytest.mark.asyncio
async def test_the_entrys_own_scope_sent_explicitly_still_works(client: AsyncClient, auth_headers: dict):
    """Existing callers send namespace and type; correct ones are unaffected."""
    doc = await _register(client, auth_headers, "c908-explicit")
    result = await _add(
        client, auth_headers, target_id=doc, synonym_namespace="default",
        synonym_entity_type="documents", synonym_composite_key={"erp_id": "c908-explicit"},
    )
    assert result["status"] == "added"


@pytest.mark.asyncio
async def test_an_unknown_entity_type_is_refused(client: AsyncClient, auth_headers: dict):
    """The live failure: RC sent the singular type and got 'added'."""
    doc = await _register(client, auth_headers, "c908-singular")
    key = {"erp_id": "c908-singular"}
    result = await _add(
        client, auth_headers, target_id=doc, synonym_namespace="default",
        synonym_entity_type="document", synonym_composite_key=key,
    )
    assert result["status"] == "error"
    assert result["error_code"] == "invalid_request"
    assert result["details"] == {"rule": "invalid_entity_type"}
    assert "documents" in result["error"]  # names the valid types
    entry = await RegistryEntry.find_one({"entry_id": doc})
    assert entry.synonyms == []


@pytest.mark.asyncio
@pytest.mark.parametrize("namespace,entity_type", [
    ("default", "terms"),          # a valid type, not the entry's
    ("vendor1", "documents"),      # another namespace
], ids=["other-type", "other-namespace"])
async def test_a_scope_other_than_the_entrys_is_refused(
    client: AsyncClient, auth_headers: dict, namespace: str, entity_type: str,
):
    """A whitelist alone would accept these, and the synonym would resolve —
    to an entity of another type or namespace."""
    doc = await _register(client, auth_headers, f"c908-{namespace}-{entity_type}")
    key = {"erp_id": f"c908-{namespace}-{entity_type}"}
    result = await _add(
        client, auth_headers, target_id=doc, synonym_namespace=namespace,
        synonym_entity_type=entity_type, synonym_composite_key=key,
    )
    assert result["status"] == "error"
    assert result["error_code"] == "invalid_request"
    assert result["details"] == {"rule": "synonym_scope_mismatch"}
    assert result["registry_id"] == doc
    assert (await _lookup(client, auth_headers, namespace, entity_type, key))["status"] == "not_found"
    assert await CompositeKeyClaim.find_existing(
        namespace, entity_type, HashService.compute_composite_key_hash(key)
    ) is None


# ---------------------------------------------------------------------------
# /synonyms/remove
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_remove_without_scope_uses_the_entrys(client: AsyncClient, auth_headers: dict):
    doc = await _register(client, auth_headers, "c908-rm")
    key = {"erp_id": "c908-rm"}
    await _add(client, auth_headers, target_id=doc, synonym_composite_key=key)
    resp = await client.post(
        f"{BASE}/synonyms/remove", json=[{"target_id": doc, "synonym_composite_key": key}],
        headers=auth_headers,
    )
    assert resp.json()["results"][0]["status"] == "removed"
    assert (await _lookup(client, auth_headers, "default", "documents", key))["status"] == "not_found"


@pytest.mark.asyncio
async def test_a_synonym_stored_under_a_foreign_scope_can_still_be_removed(
    client: AsyncClient, auth_headers: dict,
):
    """Remove honours an explicit scope as sent — even the singular type RC's
    form once stored — so data written before the rule is not stranded."""
    doc = await _register(client, auth_headers, "c908-legacy-rm")
    key = {"erp_id": "c908-legacy-rm"}
    await _plant_foreign_synonym(doc, "default", "document", key)
    resp = await client.post(
        f"{BASE}/synonyms/remove",
        json=[{"target_id": doc, "synonym_namespace": "default",
               "synonym_entity_type": "document", "synonym_composite_key": key}],
        headers=auth_headers,
    )
    assert resp.json()["results"][0]["status"] == "removed"

    await _plant_foreign_synonym(doc, "vendor1", "documents", {"erp_id": "c908-legacy-ns"})
    resp = await client.post(
        f"{BASE}/synonyms/remove",
        json=[{"target_id": doc, "synonym_namespace": "vendor1",
               "synonym_entity_type": "documents", "synonym_composite_key": {"erp_id": "c908-legacy-ns"}}],
        headers=auth_headers,
    )
    assert resp.json()["results"][0]["status"] == "removed"
    assert await CompositeKeyClaim.find_existing(
        "vendor1", "documents", HashService.compute_composite_key_hash({"erp_id": "c908-legacy-ns"})
    ) is None


# ---------------------------------------------------------------------------
# /synonyms/merge
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("namespace,entity_type", [
    ("default", "templates"),
    ("vendor1", "documents"),
], ids=["other-type", "other-namespace"])
async def test_merge_refuses_entries_of_different_scope(
    client: AsyncClient, auth_headers: dict, namespace: str, entity_type: str,
):
    preferred = await _register(client, auth_headers, f"c908-pref-{namespace}-{entity_type}")
    deprecated = await _register(
        client, auth_headers, f"c908-dep-{namespace}-{entity_type}",
        namespace=namespace, entity_type=entity_type,
    )
    resp = await client.post(
        f"{BASE}/synonyms/merge",
        json=[{"preferred_id": preferred, "deprecated_id": deprecated}],
        headers=auth_headers,
    )
    result = resp.json()["results"][0]
    assert result["status"] == "error"
    assert result["error_code"] == "invalid_request"
    assert result["details"] == {"rule": "merge_scope_mismatch"}
    assert (await RegistryEntry.find_one({"entry_id": deprecated})).status == "active"


@pytest.mark.asyncio
async def test_merge_within_one_scope_still_merges(client: AsyncClient, auth_headers: dict):
    preferred = await _register(client, auth_headers, "c908-pref-same")
    deprecated = await _register(client, auth_headers, "c908-dep-same")
    resp = await client.post(
        f"{BASE}/synonyms/merge",
        json=[{"preferred_id": preferred, "deprecated_id": deprecated}],
        headers=auth_headers,
    )
    assert resp.json()["results"][0]["status"] == "merged"


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lookup_by_key_with_an_unknown_type_is_an_error_not_not_found(
    client: AsyncClient, auth_headers: dict,
):
    await _register(client, auth_headers, "c908-lookup")
    result = await _lookup(client, auth_headers, "default", "document", {"value": "c908-lookup"})
    assert result["status"] == "error"
    assert result["details"] == {"rule": "invalid_entity_type"}


@pytest.mark.asyncio
async def test_lookup_by_id_and_resolve_with_an_unknown_type_are_errors(
    client: AsyncClient, auth_headers: dict,
):
    doc = await _register(client, auth_headers, "c908-by-id")
    by_id = await client.post(
        f"{BASE}/entries/lookup/by-id",
        json=[{"entry_id": doc, "entity_type": "document"}], headers=auth_headers,
    )
    assert by_id.json()["results"][0]["details"] == {"rule": "invalid_entity_type"}
    resolve = await client.post(
        f"{BASE}/entries/resolve",
        json=[{"composite_key": {"value": "c908-by-id"}, "entity_type": "document"}],
        headers=auth_headers,
    )
    assert resolve.json()["results"][0]["details"] == {"rule": "invalid_entity_type"}


@pytest.mark.asyncio
async def test_search_restricted_to_an_unknown_type_is_an_error(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        f"{BASE}/search/by-term",
        json=[{"term": "c908", "restrict_to_entity_types": ["document"]}],
        headers=auth_headers,
    )
    assert resp.json()["results"][0]["details"] == {"rule": "invalid_entity_type"}


@pytest.mark.asyncio
@pytest.mark.parametrize("path,params", [
    ("/entries", {"entity_type": "document"}),
    ("/entries/search", {"q": "c908", "entity_type": "document"}),
], ids=["browse", "unified-search"])
async def test_list_filters_with_an_unknown_type_are_400(
    client: AsyncClient, auth_headers: dict, path: str, params: dict,
):
    resp = await client.get(f"{BASE}{path}", params=params, headers=auth_headers)
    assert resp.status_code == 400
    assert "Invalid entity_type: document" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_lookup_never_returns_an_entry_of_another_scope(client: AsyncClient, auth_headers: dict):
    """A synonym stored before the rule under a foreign namespace would hand
    that namespace's lookup this entry. It is refused, not answered."""
    doc = await _register(client, auth_headers, "c908-hijack")
    key = {"value": "c908-hijack-squat"}
    await _plant_foreign_synonym(doc, "vendor1", "documents", key)
    assert (await _lookup(client, auth_headers, "vendor1", "documents", key))["status"] == "not_found"
