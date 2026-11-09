"""Synonym removals the suite did not reach, pinned before remove_synonyms'
refactor.

Provenance: CASE-1161. A caller without write on the entry is forbidden; an
item that raises is an error of its own; an entry whose write fails fails
its items (its first write and the re-read that would settle it both fail)
and keeps the synonym; and when the write loses a race to a
request that removed the same synonym first, the item is not_found.
"""

import uuid

import pytest

from registry.api import synonyms as synonyms_api
from registry.models.entry import RegistryEntry
from tests.test_case_1019_synonyms_bulk import _add_item, _post, _register_many


def _tag() -> str:
    return f"r1161{uuid.uuid4().hex[:8]}"


def _rm(target: str, key: dict) -> dict:
    return {"target_id": target, "synonym_composite_key": key}


async def _with_synonym(client, auth_headers, tag: str) -> str:
    [eid] = await _register_many(client, auth_headers, 1, tag)
    added = await _post(client, auth_headers, "add", [_add_item(eid, {"alias": tag})])
    assert added["results"][0]["status"] == "added"
    return eid


async def _synonyms(eid: str) -> list[dict]:
    entry = await RegistryEntry.find_one(RegistryEntry.entry_id == eid)
    return [s.composite_key for s in entry.synonyms]


@pytest.mark.asyncio
async def test_a_caller_without_write_is_forbidden_and_an_item_that_raises_is_an_error(
    client, auth_headers, monkeypatch,
):
    a_tag, b_tag = _tag(), _tag()
    a = await _with_synonym(client, auth_headers, a_tag)
    b = await _with_synonym(client, auth_headers, b_tag)

    async def refusal(access, *namespaces):
        raise_for = getattr(refusal, "raise_next", False)
        if raise_for:
            refusal.raise_next = False
            raise RuntimeError("item failed")
        refusal.raise_next = True
        return "no write on 'default'"

    monkeypatch.setattr(synonyms_api, "_write_refusal", refusal)
    body = await _post(client, auth_headers, "remove", [_rm(a, {"alias": a_tag}), _rm(b, {"alias": b_tag})])
    assert [(r["status"], r.get("error")) for r in body["results"]] == [
        ("forbidden", "no write on 'default'"), ("error", "item failed"),
    ]
    assert (body["succeeded"], body["failed"]) == (0, 2)
    assert await _synonyms(a) == [{"alias": a_tag}] and await _synonyms(b) == [{"alias": b_tag}]


@pytest.mark.asyncio
async def test_a_failed_write_fails_its_items_and_keeps_the_synonym(client, auth_headers, monkeypatch):
    tag = _tag()
    eid = await _with_synonym(client, auth_headers, tag)

    async def broken(*a, **kw):
        raise RuntimeError("write failed")

    # The first write fails with an unknown outcome, and so does the re-read
    # that would settle it: nothing is known to have been written.
    monkeypatch.setattr(synonyms_api, "write_entry", broken)
    monkeypatch.setattr(synonyms_api, "apply_to_entry", broken)
    body = await _post(client, auth_headers, "remove", [_rm(eid, {"alias": tag})])
    [result] = body["results"]
    assert (result["status"], result["error"]) == ("error", "write failed")
    assert (body["succeeded"], body["failed"]) == (0, 1)
    assert await _synonyms(eid) == [{"alias": tag}]


@pytest.mark.asyncio
async def test_a_removal_another_request_made_first_is_not_found(client, auth_headers, monkeypatch):
    tag = _tag()
    eid = await _with_synonym(client, auth_headers, tag)
    real = synonyms_api.write_entry
    lost: list[str] = []

    async def lose_once(entry, fields, guard):
        if not lost:
            lost.append(entry.entry_id)
            # The other request removes the synonym, and wins the write.
            await RegistryEntry.get_motor_collection().update_one(
                {"entry_id": eid}, {"$set": {"synonyms": []}, "$inc": {"revision": 1}},
            )
            return False
        return await real(entry, fields, guard)

    monkeypatch.setattr(synonyms_api, "write_entry", lose_once)
    body = await _post(client, auth_headers, "remove", [_rm(eid, {"alias": tag})])
    [result] = body["results"]
    assert (result["status"], result.get("error")) == ("not_found", "Synonym not found in entry")
    assert (body["succeeded"], body["failed"]) == (0, 1)
