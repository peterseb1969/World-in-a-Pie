"""Keyed activation can carry synonyms: claimed and written with the key."""

import pytest
from httpx import AsyncClient

from registry.models.composite_key_claim import CompositeKeyClaim
from registry.models.entry import RegistryEntry
from registry.services.hash import HashService
from tests.test_reservation_lifecycle import NAMESPACE, _provision


async def _activate(client, auth_headers, items) -> dict:
    resp = await client.post("/api/registry/entries/activate", json=items, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _syn(key: dict) -> dict:
    return {"synonym_composite_key": key}


@pytest.mark.asyncio
async def test_synonyms_are_claimed_and_written_with_the_key(client: AsyncClient, auth_headers):
    (eid,) = await _provision(client, auth_headers, defer_claim=True)
    key, syn = {"ns": NAMESPACE, "value": "K-1020"}, {"alias1020": "a"}

    result = await _activate(client, auth_headers, [
        {"entry_id": eid, "composite_key": key, "synonyms": [_syn(syn), _syn(syn)]},
    ])

    assert result["activated"] == 1
    entry = await RegistryEntry.find_one(RegistryEntry.entry_id == eid)
    assert [s.composite_key for s in entry.synonyms] == [syn], "a repeat is not added twice"
    assert "a" in entry.search_values
    claim = await CompositeKeyClaim.find_existing(
        NAMESPACE, "terms", HashService.compute_composite_key_hash(syn),
    )
    assert claim.owner_entry_id == eid and claim.state == "confirmed"


@pytest.mark.asyncio
async def test_a_synonym_held_elsewhere_fails_the_item_and_it_stays_reserved(
    client: AsyncClient, auth_headers
):
    (holder,) = await _provision(client, auth_headers, defer_claim=True)
    held = {"held1020": "x"}
    await _activate(client, auth_headers, [
        {"entry_id": holder, "composite_key": {"ns": NAMESPACE, "value": "H-1020"},
         "synonyms": [_syn(held)]},
    ])
    (eid,) = await _provision(client, auth_headers, defer_claim=True)

    result = await _activate(client, auth_headers, [
        {"entry_id": eid, "composite_key": {"ns": NAMESPACE, "value": "E-1020"},
         "synonyms": [_syn(held)]},
    ])

    item = result["results"][0]
    assert item["status"] == "error" and item["details"] == {"rule": "key_claimed"}
    entry = await RegistryEntry.find_one(RegistryEntry.entry_id == eid)
    assert entry.status == "reserved"
    key_claim = await CompositeKeyClaim.find_existing(
        NAMESPACE, "terms", HashService.compute_composite_key_hash({"ns": NAMESPACE, "value": "E-1020"}),
    )
    assert key_claim is None, "the failed item's key claim is released with it"


@pytest.mark.asyncio
async def test_a_synonym_scoped_elsewhere_fails_the_item(client: AsyncClient, auth_headers):
    (eid,) = await _provision(client, auth_headers, defer_claim=True)

    result = await _activate(client, auth_headers, [
        {"entry_id": eid, "composite_key": {"ns": NAMESPACE, "value": "S-1020"},
         "synonyms": [{"synonym_namespace": "vendor1", "synonym_composite_key": {"s": 1}}]},
    ])

    item = result["results"][0]
    assert item["status"] == "error"
    assert item["details"] == {"rule": "synonym_scope_mismatch"}


@pytest.mark.asyncio
async def test_synonyms_without_a_key_are_a_request_error(client: AsyncClient, auth_headers):
    (eid,) = await _provision(client, auth_headers, defer_claim=True)

    resp = await client.post(
        "/api/registry/entries/activate",
        json=[{"entry_id": eid, "synonyms": [_syn({"n": 1})]}],
        headers=auth_headers,
    )

    assert resp.status_code == 422
