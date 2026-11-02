"""/synonyms/add and /synonyms/remove in bulk.

One read of the target entries, one claim insert for the new keys, one
write per changed entry, one confirm or release, with the per-item results
of taking the items in order: a later item for the same entry sees what the
earlier ones changed.
"""

import pytest

from registry.models.composite_key_claim import CompositeKeyClaim
from registry.models.entry import RegistryEntry
from registry.services.hash import HashService

NS, ET = "default", "terms"


async def _register_many(client, auth_headers, n: int, tag: str) -> list[str]:
    r = await client.post(
        "/api/registry/entries/register",
        json=[{"namespace": NS, "entity_type": ET, "composite_key": {tag: i}} for i in range(n)],
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    return [x["registry_id"] for x in r.json()["results"]]


def _add_item(target_id: str, key: dict) -> dict:
    return {"target_id": target_id, "synonym_composite_key": key}


async def _post(client, auth_headers, path: str, items: list[dict]) -> dict:
    r = await client.post(f"/api/registry/synonyms/{path}", json=items, headers=auth_headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _claim(key: dict):
    return await CompositeKeyClaim.find_existing(NS, ET, HashService.compute_composite_key_hash(key))


@pytest.mark.asyncio
async def test_a_batch_is_added_without_per_item_reads_or_claims(client, auth_headers, monkeypatch):
    ids = await _register_many(client, auth_headers, 120, "bulk1019")

    async def must_not_run(*args, **kwargs):
        raise AssertionError("the bulk path must not read or claim per item")

    monkeypatch.setattr(RegistryEntry, "find_one", must_not_run)
    monkeypatch.setattr(CompositeKeyClaim, "claim", must_not_run)
    body = await _post(client, auth_headers, "add",
                       [_add_item(eid, {"alias1019": n}) for n, eid in enumerate(ids)])
    monkeypatch.undo()

    assert body["succeeded"] == 120 and body["failed"] == 0
    claim = await _claim({"alias1019": 7})
    assert claim.owner_entry_id == ids[7] and claim.state == "confirmed"
    entry = await RegistryEntry.find_one(RegistryEntry.entry_id == ids[7])
    assert any(s.composite_key == {"alias1019": 7} for s in entry.synonyms)


@pytest.mark.asyncio
async def test_several_synonyms_for_one_entry_and_a_repeat(client, auth_headers):
    (eid,) = await _register_many(client, auth_headers, 1, "multi1019")

    body = await _post(client, auth_headers, "add", [
        _add_item(eid, {"m1019": "a"}),
        _add_item(eid, {"m1019": "b"}),
        _add_item(eid, {"m1019": "a"}),
    ])

    assert [r["status"] for r in body["results"]] == ["added", "added", "already_exists"]
    entry = await RegistryEntry.find_one(RegistryEntry.entry_id == eid)
    keys = [s.composite_key for s in entry.synonyms if s.composite_key.get("m1019")]
    assert keys == [{"m1019": "a"}, {"m1019": "b"}]
    assert {"a", "b"} <= set(entry.search_values)


@pytest.mark.asyncio
async def test_one_synonym_for_two_entries_in_one_request(client, auth_headers):
    first, second = await _register_many(client, auth_headers, 2, "two1019")

    body = await _post(client, auth_headers, "add", [
        _add_item(first, {"shared1019": "x"}),
        _add_item(second, {"shared1019": "x"}),
    ])

    a, b = body["results"]
    assert a["status"] == "added"
    assert b["status"] == "error" and b["error_code"] == "synonym_conflict"
    assert first in b["error"]
    other = await RegistryEntry.find_one(RegistryEntry.entry_id == second)
    assert not any(s.composite_key == {"shared1019": "x"} for s in other.synonyms)


@pytest.mark.asyncio
async def test_an_inactive_copy_is_replaced(client, auth_headers):
    (eid,) = await _register_many(client, auth_headers, 1, "inactive1019")
    key = {"retired1019": "y"}
    await _post(client, auth_headers, "add", [_add_item(eid, key)])
    entry = await RegistryEntry.find_one(RegistryEntry.entry_id == eid)
    for s in entry.synonyms:
        if s.composite_key == key:
            s.status = "inactive"
    await entry.save()
    await CompositeKeyClaim.release(NS, ET, HashService.compute_composite_key_hash(key), eid)

    body = await _post(client, auth_headers, "add", [_add_item(eid, key)])

    assert body["results"][0]["status"] == "added"
    entry = await RegistryEntry.find_one(RegistryEntry.entry_id == eid)
    copies = [s for s in entry.synonyms if s.composite_key == key]
    assert len(copies) == 1 and copies[0].status != "inactive"
    assert (await _claim(key)).owner_entry_id == eid


@pytest.mark.asyncio
async def test_remove_in_bulk_releases_claims_and_a_repeat_is_not_found(client, auth_headers):
    ids = await _register_many(client, auth_headers, 3, "rm1019")
    await _post(client, auth_headers, "add", [_add_item(e, {"rmkey1019": n}) for n, e in enumerate(ids)])

    body = await _post(client, auth_headers, "remove", [
        _add_item(ids[0], {"rmkey1019": 0}),
        _add_item(ids[1], {"rmkey1019": 1}),
        _add_item(ids[0], {"rmkey1019": 0}),
    ])

    assert [r["status"] for r in body["results"]] == ["removed", "removed", "not_found"]
    assert await _claim({"rmkey1019": 0}) is None
    assert (await _claim({"rmkey1019": 2})).owner_entry_id == ids[2]
    entry = await RegistryEntry.find_one(RegistryEntry.entry_id == ids[0])
    assert not any(s.composite_key == {"rmkey1019": 0} for s in entry.synonyms)


@pytest.mark.asyncio
async def test_an_inactive_entry_is_not_a_target(client, auth_headers):
    (eid,) = await _register_many(client, auth_headers, 1, "gone1019")
    entry = await RegistryEntry.find_one(RegistryEntry.entry_id == eid)
    entry.status = "inactive"
    await entry.save()

    body = await _post(client, auth_headers, "add", [_add_item(eid, {"g1019": 1})])

    assert body["results"][0]["status"] == "target_not_found"
