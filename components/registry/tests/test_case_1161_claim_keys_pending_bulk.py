"""Bulk pending claims, pinned before claim_keys_pending_bulk's refactor.

Provenance: CASE-1161. The suite never reached these branches: a key with no
hash is not claimed (and does not block its group), an insert failure other
than a duplicate key propagates, and a group with two keys held by genuine
owners is blocked once, by the first, with the per-key path run once.
"""

import pytest
from pymongo.errors import BulkWriteError

from registry.models.composite_key_claim import CompositeKeyClaim
from registry.models.entry import RegistryEntry
from registry.services import claims as claims_service
from registry.services.claims import claim_keys_pending_bulk
from registry.services.hash import HashService

NS, ET = "ns1161c", "terms"


def _h(name: str) -> str:
    return HashService.compute_composite_key_hash({"k": name})


def _key(name: str, owner: str, kind: str = "primary") -> tuple:
    return (NS, ET, _h(name), owner, kind)


async def _claim_doc(name: str) -> dict | None:
    return await CompositeKeyClaim.get_motor_collection().find_one(
        {"namespace": NS, "entity_type": ET, "composite_key_hash": _h(name)}
    )


async def _genuine_owner(name: str, owner: str) -> None:
    await RegistryEntry(
        entry_id=owner, namespace=NS, entity_type=ET,
        primary_composite_key={"k": name}, primary_composite_key_hash=_h(name),
        status="active", created_by="test",
    ).insert()
    await CompositeKeyClaim.claim(NS, ET, _h(name), owner, "primary")


@pytest.mark.asyncio
async def test_a_key_without_a_hash_is_not_claimed(client):
    blocked = await claim_keys_pending_bulk([[(NS, ET, "", "e1", "synonym"), _key("a", "e1")]])
    assert blocked == [None]
    assert (await _claim_doc("a"))["owner_entry_id"] == "e1"
    assert await CompositeKeyClaim.get_motor_collection().count_documents(
        {"namespace": NS, "composite_key_hash": ""}
    ) == 0


@pytest.mark.asyncio
async def test_an_insert_failure_other_than_a_duplicate_propagates(client, monkeypatch):
    class Collection:
        async def insert_many(self, docs, ordered):
            raise BulkWriteError({"writeErrors": [{"index": 0, "code": 121, "errmsg": "invalid"}]})

    monkeypatch.setattr(CompositeKeyClaim, "get_motor_collection", classmethod(lambda cls: Collection()))
    with pytest.raises(BulkWriteError):
        await claim_keys_pending_bulk([[_key("a", "e1")]])


@pytest.mark.asyncio
async def test_a_group_with_two_held_keys_is_blocked_once_by_the_first(client, monkeypatch):
    await _genuine_owner("t1", "owner1")
    await _genuine_owner("t2", "owner2")
    calls: list[tuple] = []
    real = claims_service._claim_one_pending

    async def counting(key, token):
        calls.append(key)
        return await real(key, token)

    monkeypatch.setattr(claims_service, "_claim_one_pending", counting)
    blocked = await claim_keys_pending_bulk([
        [_key("x", "e1"), _key("t1", "e1", "synonym"), _key("t2", "e1", "synonym")],
        [_key("y", "e2")],
    ])
    assert blocked == ["owner1", None]
    assert [k[2] for k in calls] == [_h("t1")]
    assert await _claim_doc("x") is None
    assert (await _claim_doc("y"))["owner_entry_id"] == "e2"
