"""Two writers changing one Registry entry keep each other's changes (CASE-1129).

Synonym add/remove read the entry, changed the list in memory and wrote the
whole list back; update, soft delete, merge, retire/revive and rekey saved a
full stale copy. Concurrent writers to one entry silently dropped each other's
synonyms, and a soft delete could be undone. Now every entry write increments
``revision``, a read-modify-write lands only over the revision it read, and a
writer that lost re-reads the entry and applies its change again.
"""

import asyncio

import pytest

import registry.services.entry_writes as entry_writes
from registry.models.composite_key_claim import CompositeKeyClaim
from registry.models.entry import RegistryEntry
from registry.services.hash import HashService
from tests.test_case_427 import _add_synonym, _register

NS, TYPE = "default", "templates"


def _hash(key: dict) -> str:
    return HashService.compute_composite_key_hash(key)


async def _remove(client, headers, target_id, key) -> dict:
    r = await client.post("/api/registry/synonyms/remove", headers=headers, json=[{
        "target_id": target_id, "synonym_composite_key": key,
    }])
    assert r.status_code == 200, r.text
    return r.json()["results"][0]


async def _entry(entry_id: str) -> RegistryEntry:
    return await RegistryEntry.find_one({"entry_id": entry_id})


def _active_hashes(entry: RegistryEntry) -> set[str]:
    return {s.composite_key_hash for s in entry.synonyms if s.status != "inactive"}


@pytest.mark.asyncio
async def test_parallel_adds_to_one_entry_all_land(client, auth_headers):
    target = await _register(client, auth_headers, NS, TYPE, {"value": "MANY"})
    keys = [{"vendor": f"V-{n}"} for n in range(10)]

    results = await asyncio.gather(*(
        _add_synonym(client, auth_headers, target, NS, TYPE, k) for k in keys
    ))

    assert [r["status"] for r in results] == ["added"] * 10, [(r["status"], r.get("error_code"), r.get("error")) for r in results if r["status"] != "added"]
    entry = await _entry(target)
    assert {_hash(k) for k in keys} <= _active_hashes(entry)
    assert {f"V-{n}" for n in range(10)} <= set(entry.search_values)
    for k in keys:
        claim = await CompositeKeyClaim.find_one({"composite_key_hash": _hash(k)})
        assert claim is not None and claim.owner_entry_id == target


@pytest.mark.asyncio
async def test_a_parallel_add_and_remove_both_hold(client, auth_headers):
    target = await _register(client, auth_headers, NS, TYPE, {"value": "MIXED"})
    old, new = {"vendor": "OLD"}, {"vendor": "NEW"}
    assert (await _add_synonym(client, auth_headers, target, NS, TYPE, old))["status"] == "added"

    added, removed = await asyncio.gather(
        _add_synonym(client, auth_headers, target, NS, TYPE, new),
        _remove(client, auth_headers, target, old),
    )

    assert added["status"] == "added" and removed["status"] == "removed", (added, removed)
    hashes = _active_hashes(await _entry(target))
    assert _hash(new) in hashes
    assert _hash(old) not in hashes
    # The removed key is free again: another entry can take it.
    other = await _register(client, auth_headers, NS, TYPE, {"value": "OTHER"})
    assert (await _add_synonym(client, auth_headers, other, NS, TYPE, old))["status"] == "added"


@pytest.mark.asyncio
async def test_the_same_add_twice_in_parallel_is_added_once(client, auth_headers):
    target = await _register(client, auth_headers, NS, TYPE, {"value": "TWICE"})
    key = {"vendor": "SAME"}

    results = await asyncio.gather(
        _add_synonym(client, auth_headers, target, NS, TYPE, key),
        _add_synonym(client, auth_headers, target, NS, TYPE, key),
    )

    assert sorted(r["status"] for r in results) == ["added", "already_exists"], results
    entry = await _entry(target)
    assert [s.composite_key_hash for s in entry.synonyms].count(_hash(key)) == 1


def _interleave(monkeypatch, competitor):
    """Run ``competitor`` once, between a guarded writer's read and its write."""
    real = entry_writes.write_entry
    state = {"done": False}

    async def write_entry(entry, fields, extra_filter=None):
        if not state["done"]:
            state["done"] = True
            await competitor()
        return await real(entry, fields, extra_filter)

    monkeypatch.setattr(entry_writes, "write_entry", write_entry)


@pytest.mark.asyncio
async def test_an_update_does_not_undo_a_soft_delete(client, auth_headers, monkeypatch):
    target = await _register(client, auth_headers, NS, TYPE, {"value": "GONE"})

    async def soft_delete():
        monkeypatch.undo()  # the competitor itself writes normally
        r = await client.request("DELETE", "/api/registry/entries", headers=auth_headers,
                                 json=[{"entry_id": target}])
        assert r.json()["results"][0]["status"] == "deactivated", r.text

    _interleave(monkeypatch, soft_delete)
    r = await client.put("/api/registry/entries", headers=auth_headers,
                         json=[{"entry_id": target, "metadata": {"note": "late"}}])

    assert r.json()["results"][0]["status"] == "not_found", r.text
    entry = await _entry(target)
    assert entry.status == "inactive"
    assert "note" not in entry.metadata


@pytest.mark.asyncio
async def test_an_update_keeps_a_synonym_added_meanwhile(client, auth_headers, monkeypatch):
    target = await _register(client, auth_headers, NS, TYPE, {"value": "KEEP"})
    key = {"vendor": "MEANWHILE"}

    async def add():
        monkeypatch.undo()
        assert (await _add_synonym(client, auth_headers, target, NS, TYPE, key))["status"] == "added"

    _interleave(monkeypatch, add)
    r = await client.put("/api/registry/entries", headers=auth_headers,
                         json=[{"entry_id": target, "metadata": {"note": "kept"}}])

    assert r.json()["results"][0]["status"] == "updated", r.text
    entry = await _entry(target)
    assert _hash(key) in _active_hashes(entry)
    assert entry.metadata.get("note") == "kept"


@pytest.mark.asyncio
async def test_endless_contention_is_a_concurrency_conflict(client, auth_headers, monkeypatch):
    target = await _register(client, auth_headers, NS, TYPE, {"value": "BUSY"})
    key = {"vendor": "NEVER"}

    async def always_lose(entry, fields, extra_filter=None):
        return False

    monkeypatch.setattr(entry_writes, "write_entry", always_lose)
    monkeypatch.setattr("registry.api.synonyms.write_entry", always_lose)
    result = await _add_synonym(client, auth_headers, target, NS, TYPE, key)

    assert result["status"] == "error", result
    assert result["error_code"] == "concurrency_conflict", result
    monkeypatch.undo()
    assert _hash(key) not in _active_hashes(await _entry(target))
    # The claim the request took is released, not left to block the key.
    claim = await CompositeKeyClaim.find_one({"composite_key_hash": _hash(key)})
    assert claim is None or (claim.state == "pending" and claim.owner_entry_id == target)
