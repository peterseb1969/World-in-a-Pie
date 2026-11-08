"""A merge makes the deprecated entry an alias of the preferred one, completely.

After merging D into P:
- D's canonical ID resolves to P on /entries/resolve, the path every service
  resolves through — not only on by-id lookup and search;
- D's keys (its primary key and its synonyms) resolve to P, and re-registering
  D's identity answers P, never the merged-away D;
- the merge takes only keys D or P hold: a key another entry holds is a
  conflict, and nothing is merged;
- a merge that fails part-way leaves D as it was and can simply be retried;
- retiring P later does not drop D's redirect.
"""

import pytest

from registry.models.composite_key_claim import CompositeKeyClaim
from registry.models.entry import RegistryEntry
from registry.services import merge as merge_service
from registry.services.hash import HashService

NS = "default"
TYPE = "templates"


def _hash(key: dict) -> str:
    return HashService.compute_composite_key_hash(key)


async def _register(client, headers, key: dict) -> str:
    r = await client.post(
        "/api/registry/entries/register",
        json=[{"namespace": NS, "entity_type": TYPE, "composite_key": key}],
        headers=headers,
    )
    assert r.status_code == 200
    result = r.json()["results"][0]
    assert result["status"] in ("created", "already_exists"), result
    return result["registry_id"]


async def _add_synonym(client, headers, target_id: str, key: dict) -> dict:
    r = await client.post(
        "/api/registry/synonyms/add",
        json=[{
            "target_id": target_id, "synonym_namespace": NS,
            "synonym_entity_type": TYPE, "synonym_composite_key": key,
        }],
        headers=headers,
    )
    assert r.status_code == 200
    return r.json()["results"][0]


async def _merge(client, headers, preferred: str, deprecated: str) -> dict:
    r = await client.post(
        "/api/registry/synonyms/merge",
        json=[{"preferred_id": preferred, "deprecated_id": deprecated}],
        headers=headers,
    )
    assert r.status_code == 200
    return r.json()["results"][0]


async def _resolve(client, headers, item: dict) -> dict:
    r = await client.post("/api/registry/entries/resolve", json=[item], headers=headers)
    assert r.status_code == 200
    return r.json()["results"][0]


@pytest.mark.asyncio
async def test_the_merged_away_id_resolves_to_the_survivor(client, auth_headers):
    p = await _register(client, auth_headers, {"value": "M-P1"})
    d = await _register(client, auth_headers, {"value": "M-D1"})
    assert (await _merge(client, auth_headers, p, d))["status"] == "merged"

    for item in ({"entry_id": d}, {"entry_id": d, "entity_type": TYPE}):
        result = await _resolve(client, auth_headers, item)
        assert result["status"] == "found", result
        assert result["entry_id"] == p


@pytest.mark.asyncio
async def test_a_chain_of_merges_resolves_to_the_last_survivor(client, auth_headers):
    a = await _register(client, auth_headers, {"value": "M-CHAIN-A"})
    b = await _register(client, auth_headers, {"value": "M-CHAIN-B"})
    c = await _register(client, auth_headers, {"value": "M-CHAIN-C"})
    assert (await _merge(client, auth_headers, b, a))["status"] == "merged"
    assert (await _merge(client, auth_headers, c, b))["status"] == "merged"

    for old in (a, b):
        result = await _resolve(client, auth_headers, {"entry_id": old})
        assert result["status"] == "found" and result["entry_id"] == c, result


@pytest.mark.asyncio
async def test_a_planted_entry_id_synonym_does_not_capture_resolution(client, auth_headers):
    """The redirect is the merge's own record, not whatever synonym is lying
    around: an {"entry_id": X} synonym anyone with write can add does not
    make X resolve to its holder."""
    victim = await _register(client, auth_headers, {"value": "M-VICTIM"})
    thief = await _register(client, auth_headers, {"value": "M-THIEF"})
    unminted = "019a0000-0000-7000-8000-00000000dead"
    assert (await _add_synonym(client, auth_headers, thief, {"entry_id": unminted}))["status"] == "added"

    result = await _resolve(client, auth_headers, {"entry_id": unminted})
    assert result["status"] == "not_found", result
    # The victim is untouched by the planted synonym.
    result = await _resolve(client, auth_headers, {"entry_id": victim})
    assert result["entry_id"] == victim


@pytest.mark.asyncio
async def test_a_key_another_entry_holds_is_a_conflict_and_nothing_merges(client, auth_headers):
    p = await _register(client, auth_headers, {"value": "M-P2"})
    d = await _register(client, auth_headers, {"value": "M-D2"})
    c = await _register(client, auth_headers, {"value": "M-C2"})
    # C holds the key the merge would mint for D's id.
    assert (await _add_synonym(client, auth_headers, c, {"entry_id": d}))["status"] == "added"

    result = await _merge(client, auth_headers, p, d)
    assert result["status"] == "error", result
    assert result["error_code"] == "already_exists"
    assert result["details"]["holder"] == c

    claim = await CompositeKeyClaim.find_existing(NS, TYPE, _hash({"entry_id": d}))
    assert claim is not None and claim.owner_entry_id == c
    entry_d = await RegistryEntry.find_one({"entry_id": d})
    assert entry_d.status == "active"
    entry_p = await RegistryEntry.find_one({"entry_id": p})
    assert all(s.composite_key != {"entry_id": d} for s in entry_p.synonyms)


@pytest.mark.asyncio
async def test_the_deprecated_entrys_keys_now_belong_to_the_survivor(client, auth_headers):
    p = await _register(client, auth_headers, {"value": "M-P3"})
    d = await _register(client, auth_headers, {"value": "M-D3"})
    vendor = {"vendor": "M-D3-SYN"}
    assert (await _add_synonym(client, auth_headers, d, vendor))["status"] == "added"
    assert (await _merge(client, auth_headers, p, d))["status"] == "merged"

    # Re-registering D's identity answers P, not the merged-away D.
    r = await client.post(
        "/api/registry/entries/register",
        json=[{"namespace": NS, "entity_type": TYPE, "composite_key": {"value": "M-D3"}}],
        headers=auth_headers,
    )
    result = r.json()["results"][0]
    assert result["status"] == "already_exists" and result["registry_id"] == p, result

    # Both of D's keys resolve to P, and their claims are P's.
    for key in ({"value": "M-D3"}, vendor):
        found = await _resolve(client, auth_headers, {"composite_key": key, "namespace": NS})
        assert found["status"] == "found" and found["entry_id"] == p, (key, found)
        claim = await CompositeKeyClaim.find_existing(NS, TYPE, _hash(key))
        assert claim is not None and claim.owner_entry_id == p

    # D keeps a record of its keys, all inactive, and points at P.
    entry_d = await RegistryEntry.find_one({"entry_id": d})
    assert entry_d.status == "inactive"
    assert entry_d.merged_into == p
    assert entry_d.primary_composite_key == {"value": "M-D3"}
    assert entry_d.primary_composite_key_hash == ""
    assert all(s.status == "inactive" for s in entry_d.synonyms)


def _fail_once_writing(monkeypatch, failing_id: str) -> list:
    real = merge_service.apply_to_entry
    failed: list = []

    async def fail_once(entry_id, *args, **kwargs):
        if entry_id == failing_id and not failed:
            failed.append(entry_id)
            raise RuntimeError("database went away")
        return await real(entry_id, *args, **kwargs)

    monkeypatch.setattr(merge_service, "apply_to_entry", fail_once)
    return failed


@pytest.mark.asyncio
async def test_a_merge_that_fails_writing_the_survivor_changes_nothing(client, auth_headers, monkeypatch):
    p = await _register(client, auth_headers, {"value": "M-P4A"})
    d = await _register(client, auth_headers, {"value": "M-D4A"})
    vendor = {"vendor": "M-D4A-SYN"}
    assert (await _add_synonym(client, auth_headers, d, vendor))["status"] == "added"

    failed = _fail_once_writing(monkeypatch, p)
    assert (await _merge(client, auth_headers, p, d))["status"] == "error"
    assert failed

    # D's keys are still D's, claims included.
    for key in ({"value": "M-D4A"}, vendor):
        found = await _resolve(client, auth_headers, {"composite_key": key, "namespace": NS})
        assert found["status"] == "found" and found["entry_id"] == d, (key, found)
        claim = await CompositeKeyClaim.find_existing(NS, TYPE, _hash(key))
        assert claim.owner_entry_id == d
    assert await CompositeKeyClaim.find_existing(NS, TYPE, _hash({"entry_id": d})) is None

    assert (await _merge(client, auth_headers, p, d))["status"] == "merged"
    found = await _resolve(client, auth_headers, {"composite_key": vendor, "namespace": NS})
    assert found["entry_id"] == p


@pytest.mark.asyncio
async def test_a_merge_that_fails_retiring_the_deprecated_entry_can_be_retried(
    client, auth_headers, monkeypatch,
):
    p = await _register(client, auth_headers, {"value": "M-P4"})
    d = await _register(client, auth_headers, {"value": "M-D4"})

    failed = _fail_once_writing(monkeypatch, d)
    assert (await _merge(client, auth_headers, p, d))["status"] == "error"
    assert failed

    # Until the retry, D's key is carried by both entries: a lookup answers
    # ambiguous (naming the survivor), never not_found.
    found = await _resolve(client, auth_headers, {"composite_key": {"value": "M-D4"}, "namespace": NS})
    assert found["status"] == "ambiguous", found
    assert p in {c["entry_id"] for c in found["candidates"]}
    claim = await CompositeKeyClaim.find_existing(NS, TYPE, _hash({"value": "M-D4"}))
    assert claim.owner_entry_id == p

    assert (await _merge(client, auth_headers, p, d))["status"] == "merged"
    found = await _resolve(client, auth_headers, {"entry_id": d})
    assert found["status"] == "found" and found["entry_id"] == p
    found = await _resolve(client, auth_headers, {"composite_key": {"value": "M-D4"}, "namespace": NS})
    assert found["status"] == "found" and found["entry_id"] == p


@pytest.mark.asyncio
async def test_merging_an_already_merged_pair_again_answers_merged(client, auth_headers):
    p = await _register(client, auth_headers, {"value": "M-P5"})
    d = await _register(client, auth_headers, {"value": "M-D5"})
    assert (await _merge(client, auth_headers, p, d))["status"] == "merged"
    again = await _merge(client, auth_headers, p, d)
    assert again["status"] == "merged", again


@pytest.mark.asyncio
async def test_retiring_the_survivor_keeps_the_merged_id_resolving(client, auth_headers):
    p = await _register(client, auth_headers, {"value": "M-P6"})
    d = await _register(client, auth_headers, {"value": "M-D6"})
    assert (await _merge(client, auth_headers, p, d))["status"] == "merged"

    r = await client.post(
        "/api/registry/entries/deactivate", json=[{"entry_id": p}], headers=auth_headers,
    )
    assert r.status_code == 200

    # P's own canonical id keeps resolving after retirement; so does D's.
    for old in (p, d):
        found = await _resolve(client, auth_headers, {"entry_id": old})
        assert found["status"] == "found" and found["entry_id"] == p, (old, found)
    entry_p = await RegistryEntry.find_one({"entry_id": p})
    merged_syn = next(s for s in entry_p.synonyms if s.composite_key == {"entry_id": d})
    assert merged_syn.status != "inactive"
