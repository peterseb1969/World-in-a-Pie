"""Synonym additions the suite did not reach, pinned before add_synonyms'
refactor.

Provenance: CASE-1161, CASE-1268. A synonym the entry already holds, or its
own primary key, is already_exists with no claim; an item that raises is an
error of its own; a repeat of an item that fails shares its error; and
re-adding a synonym whose inactive copy is still recorded replaces that copy
on a re-read after a lost race. The same replacement on the first write was
already pinned by test_case_1019_synonyms_bulk (test_an_inactive_copy_is_replaced).
"""

import uuid

import pytest

from registry.api import synonyms as synonyms_api
from registry.models.entry import RegistryEntry
from tests.test_case_1019_synonyms_bulk import _add_item, _post, _register_many


def _tag() -> str:
    return f"s1161{uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
async def test_a_synonym_the_entry_holds_or_its_own_key_is_already_exists(client, auth_headers):
    tag = _tag()
    [eid] = await _register_many(client, auth_headers, 1, tag)
    first = await _post(client, auth_headers, "add", [_add_item(eid, {"alias": tag})])
    assert first["results"][0]["status"] == "added"

    again = await _post(client, auth_headers, "add", [
        _add_item(eid, {"alias": tag}),
        _add_item(eid, {tag: 0}),  # the entry's own primary key
    ])
    assert [(r["status"], r["registry_id"]) for r in again["results"]] == [
        ("already_exists", eid), ("already_exists", eid),
    ]
    assert (again["succeeded"], again["failed"]) == (0, 0)


@pytest.mark.asyncio
async def test_an_item_that_raises_is_an_error_of_its_own(client, auth_headers, monkeypatch):
    tag = _tag()
    [eid] = await _register_many(client, auth_headers, 1, tag)
    real = synonyms_api._scope_mismatch

    def boom_once(entry, ns, et):
        if ns == "boom":
            raise RuntimeError("item failed")
        return real(entry, ns, et)

    monkeypatch.setattr(synonyms_api, "_scope_mismatch", boom_once)
    body = await _post(client, auth_headers, "add", [
        {**_add_item(eid, {"alias": tag}), "synonym_namespace": "boom"},
        _add_item(eid, {"alias2": tag}),
    ])
    assert [r["status"] for r in body["results"]] == ["error", "added"]
    assert "item failed" in body["results"][0]["error"]
    assert (body["succeeded"], body["failed"]) == (1, 1)


@pytest.mark.asyncio
async def test_a_repeat_of_an_item_that_fails_shares_its_error(client, auth_headers):
    tag = _tag()
    holder, other = await _register_many(client, auth_headers, 2, tag)
    held = await _post(client, auth_headers, "add", [_add_item(holder, {"taken": tag})])
    assert held["results"][0]["status"] == "added"

    body = await _post(client, auth_headers, "add", [
        _add_item(other, {"taken": tag}),
        _add_item(other, {"taken": tag}),
    ])
    assert [(r["status"], r.get("error_code")) for r in body["results"]] == [
        ("error", "synonym_conflict"), ("error", "synonym_conflict"),
    ]
    assert [r["index"] for r in body["results"]] == [0, 1]
    assert (body["succeeded"], body["failed"]) == (0, 2)


@pytest.mark.asyncio
async def test_re_adding_a_released_synonym_replaces_its_inactive_copy(client, auth_headers):
    tag = _tag()
    [eid] = await _register_many(client, auth_headers, 1, tag)
    await _post(client, auth_headers, "add", [_add_item(eid, {"alias": tag})])
    await RegistryEntry.get_motor_collection().update_one(
        {"entry_id": eid}, {"$set": {"synonyms.0.status": "inactive"}},
    )

    body = await _post(client, auth_headers, "add", [_add_item(eid, {"alias": tag})])
    assert body["results"][0]["status"] == "added"
    entry = await RegistryEntry.find_one(RegistryEntry.entry_id == eid)
    assert [(s.composite_key, s.status) for s in entry.synonyms] == [({"alias": tag}, "active")]


@pytest.mark.asyncio
async def test_a_write_that_lost_a_race_reapplies_and_replaces_an_inactive_copy(client, auth_headers, monkeypatch):
    """The entry's first write finds another writer was first (its revision
    moved on): the entry is read again and the addition re-applied, where the
    inactive copy still recorded is replaced, not doubled."""
    tag = _tag()
    [eid] = await _register_many(client, auth_headers, 1, tag)
    await _post(client, auth_headers, "add", [_add_item(eid, {"alias": tag})])
    await RegistryEntry.get_motor_collection().update_one(
        {"entry_id": eid}, {"$set": {"synonyms.0.status": "inactive"}},
    )
    real = synonyms_api.write_entry
    lost: list[str] = []

    async def lose_once(entry, fields, guard):
        if not lost:
            lost.append(entry.entry_id)
            return False
        return await real(entry, fields, guard)

    monkeypatch.setattr(synonyms_api, "write_entry", lose_once)
    body = await _post(client, auth_headers, "add", [_add_item(eid, {"alias": tag})])
    assert lost == [eid]
    assert body["results"][0]["status"] == "added"
    entry = await RegistryEntry.find_one(RegistryEntry.entry_id == eid)
    assert [(s.composite_key, s.status) for s in entry.synonyms] == [({"alias": tag}, "active")]
