"""Bulk composite-key claims: the two-phase protocol for a whole request.

A request of N entries claims all their keys in one unordered insert,
confirms them in one write and releases them in a few. Each entry's keys stay
all-or-nothing, a key its owner already holds is fine, a stale holder is
healed, a genuine owner blocks, and the outcome per entry matches taking the
entries one at a time. Confirm and release name exact claims, so another
request's in-flight claim on the same entry is never confirmed or deleted.
"""

import pytest

from registry.models.composite_key_claim import CompositeKeyClaim
from registry.models.entry import RegistryEntry
from registry.services import claims as claims_service
from registry.services.claims import (
    claim_entries_keys_pending,
    claim_keys_pending_bulk,
    confirm_keys_bulk,
    release_keys_bulk,
)
from registry.services.hash import HashService

NS, ET = "ns1017", "terms"


def _h(name: str) -> str:
    return HashService.compute_composite_key_hash({"k": name})


def _key(name: str, owner: str, kind: str = "primary") -> tuple:
    return (NS, ET, _h(name), owner, kind)


async def _claim_doc(name: str) -> dict | None:
    return await CompositeKeyClaim.get_motor_collection().find_one(
        {"namespace": NS, "entity_type": ET, "composite_key_hash": _h(name)}
    )


async def _genuine_owner(name: str, owner: str = "outsider") -> None:
    """A claim backed by its owner's entry: the owner genuinely holds the key."""
    await RegistryEntry(
        entry_id=owner, namespace=NS, entity_type=ET,
        primary_composite_key={"k": name}, primary_composite_key_hash=_h(name),
        status="active", created_by="test",
    ).insert()
    await CompositeKeyClaim.claim(NS, ET, _h(name), owner, "primary")


@pytest.mark.asyncio
async def test_free_keys_are_claimed_in_one_insert_without_the_per_key_path(client, monkeypatch):
    async def per_key_path_must_not_run(*args, **kwargs):
        raise AssertionError("no duplicate, so the per-key path must not run")

    monkeypatch.setattr(CompositeKeyClaim, "claim", per_key_path_must_not_run)
    groups = [[_key(f"a{i}", f"e{i}"), _key(f"s{i}", f"e{i}", "synonym")] for i in range(3)]

    assert await claim_keys_pending_bulk(groups) == [None, None, None]
    for i in range(3):
        doc = await _claim_doc(f"a{i}")
        assert doc["owner_entry_id"] == f"e{i}" and doc["state"] == "pending"

    await confirm_keys_bulk([k for g in groups for k in g])
    assert (await _claim_doc("s2"))["state"] == "confirmed"


@pytest.mark.asyncio
async def test_a_genuine_owner_blocks_only_its_group_and_the_group_is_rolled_back(client):
    await _genuine_owner("taken")

    blocked = await claim_keys_pending_bulk([
        [_key("x1", "e1"), _key("taken", "e1", "synonym")],
        [_key("y1", "e2")],
    ])

    assert blocked == ["outsider", None]
    assert await _claim_doc("x1") is None, "the blocked group's other key is released"
    assert (await _claim_doc("taken"))["owner_entry_id"] == "outsider"
    assert (await _claim_doc("y1"))["owner_entry_id"] == "e2"


@pytest.mark.asyncio
async def test_a_key_its_owner_already_holds_is_not_a_conflict(client):
    await CompositeKeyClaim.claim(NS, ET, _h("mine"), "e1", "primary")

    assert await claim_keys_pending_bulk([[_key("mine", "e1"), _key("new", "e1")]]) == [None]
    assert (await _claim_doc("new"))["owner_entry_id"] == "e1"


@pytest.mark.asyncio
async def test_a_stale_holder_is_healed_and_the_key_claimed(client):
    # A confirmed claim whose owner entry does not exist: crashed-delete residue.
    await CompositeKeyClaim.claim(NS, ET, _h("stale"), "ghost", "primary")

    assert await claim_keys_pending_bulk([[_key("stale", "e1")]]) == [None]
    assert (await _claim_doc("stale"))["owner_entry_id"] == "e1"


@pytest.mark.asyncio
async def test_a_young_pending_holder_still_blocks(client):
    await CompositeKeyClaim.claim(NS, ET, _h("inflight"), "other", "primary", state="pending")

    assert await claim_keys_pending_bulk([[_key("inflight", "e1")]]) == ["other"]


@pytest.mark.asyncio
async def test_two_groups_of_one_batch_wanting_one_key_the_second_is_blocked(client):
    blocked = await claim_keys_pending_bulk([[_key("dup", "e1")], [_key("dup", "e2")]])

    assert blocked == [None, "e1"]
    assert (await _claim_doc("dup"))["owner_entry_id"] == "e1"


@pytest.mark.asyncio
async def test_a_group_blocked_by_a_rolled_back_group_of_the_batch_is_claimed_again(client):
    # e1 takes "shared" but is blocked on "taken" and rolled back; taken one at
    # a time, e2 would then have found "shared" free.
    await _genuine_owner("taken")

    blocked = await claim_keys_pending_bulk([
        [_key("shared", "e1"), _key("taken", "e1", "synonym")],
        [_key("shared", "e2")],
    ])

    assert blocked == ["outsider", None]
    assert (await _claim_doc("shared"))["owner_entry_id"] == "e2"


@pytest.mark.asyncio
async def test_confirm_names_exact_claims_never_an_owners_other_pending_claims(client):
    # Another request holds a pending claim on the same entry, still in flight.
    await CompositeKeyClaim.claim(NS, ET, _h("theirs"), "e1", "synonym", state="pending")
    await claim_keys_pending_bulk([[_key("ours", "e1")]])

    await confirm_keys_bulk([_key("ours", "e1")])

    assert (await _claim_doc("ours"))["state"] == "confirmed"
    assert (await _claim_doc("theirs"))["state"] == "pending"


@pytest.mark.asyncio
async def test_release_deletes_only_claims_held_by_the_named_owner(client):
    await CompositeKeyClaim.claim(NS, ET, _h("k"), "someone-else", "primary")

    await release_keys_bulk([_key("k", "e1")])

    assert (await _claim_doc("k"))["owner_entry_id"] == "someone-else"


@pytest.mark.asyncio
async def test_entry_form_formats_the_conflict_message(client):
    await _genuine_owner("taken")
    entries = [
        RegistryEntry(
            entry_id=eid, namespace=NS, entity_type=ET,
            primary_composite_key={"k": name},
            primary_composite_key_hash=_h(name), status="active", created_by="test",
        )
        for eid, name in (("e1", "free"), ("e2", "taken"))
    ]

    assert await claim_entries_keys_pending(entries) == [
        None, "Composite key already registered under different entry: outsider",
    ]


@pytest.mark.asyncio
async def test_register_claims_a_batch_in_one_insert(client, auth_headers, monkeypatch):
    calls = {"insert_many": 0}
    real = claims_service.claim_keys_pending_bulk

    async def counting(groups):
        calls["insert_many"] += 1
        return await real(groups)

    monkeypatch.setattr(claims_service, "claim_keys_pending_bulk", counting)

    async def per_key_path_must_not_run(*args, **kwargs):
        raise AssertionError("no duplicate, so the per-key path must not run")

    monkeypatch.setattr(CompositeKeyClaim, "claim", per_key_path_must_not_run)

    items = [
        {"namespace": "default", "entity_type": "terms", "composite_key": {"bulk1017": i}}
        for i in range(25)
    ]
    r = await client.post("/api/registry/entries/register", json=items, headers=auth_headers)

    assert r.status_code == 200
    body = r.json()
    assert body["created"] == 25 and body["errors"] == 0
    assert calls["insert_many"] == 1
    pending = await CompositeKeyClaim.get_motor_collection().count_documents(
        {"namespace": "default", "state": "pending",
         "owner_entry_id": {"$in": [x["registry_id"] for x in body["results"]]}}
    )
    assert pending == 0, "every claim of the batch is confirmed"


