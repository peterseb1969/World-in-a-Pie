"""Claim bookkeeping says what it did (CASE-1279, e75e6851 R3-R5).

- A confirmed claim carries no request's token, by every confirm path.
- The pending reconcile counts only the claims its filter matched.
- A pending takeover returns the claim as stored.
"""

from datetime import UTC, datetime, timedelta

import pytest

from registry.models.composite_key_claim import CompositeKeyClaim
from registry.services import claims as claims_service
from registry.services.hash import HashService

NS, ET = "ns1279", "terms"


def _h(name: str) -> str:
    return HashService.compute_composite_key_hash({"k1279": name})


async def _doc(name: str) -> dict:
    return await CompositeKeyClaim.get_motor_collection().find_one(
        {"namespace": NS, "entity_type": ET, "composite_key_hash": _h(name)}
    )


@pytest.mark.asyncio
async def test_confirming_clears_the_token(client):
    await CompositeKeyClaim.claim(NS, ET, _h("one"), "e1", "primary", state="pending", token="t1")
    await CompositeKeyClaim.confirm(NS, ET, _h("one"), "e1")
    doc = await _doc("one")
    assert (doc["state"], doc["claim_token"]) == ("confirmed", None)

    await CompositeKeyClaim.claim(NS, ET, _h("bulk"), "e2", "primary", state="pending", token="t2")
    await claims_service.confirm_keys_bulk([(NS, ET, _h("bulk"), "e2", "primary")])
    doc = await _doc("bulk")
    assert (doc["state"], doc["claim_token"]) == ("confirmed", None)


@pytest.mark.asyncio
async def test_the_reconcile_counts_only_what_it_matched(client, monkeypatch):
    old = datetime.now(UTC) - timedelta(hours=1)
    coll = CompositeKeyClaim.get_motor_collection()
    await coll.insert_one({
        "namespace": NS, "entity_type": ET, "composite_key_hash": _h("raced"),
        "owner_entry_id": "nobody", "kind": "primary", "state": "pending",
        "created_at": old, "claim_token": "t3",
    })
    real_delete = coll.delete_one

    async def taken_over_first(*args, **kwargs):
        # A request takes the claim over (re-dating it) just before the delete.
        await coll.update_one({"composite_key_hash": _h("raced")},
                              {"$set": {"created_at": datetime.now(UTC)}})
        return await real_delete(*args, **kwargs)

    monkeypatch.setattr(type(coll), "delete_one", lambda self, *a, **k: taken_over_first(*a, **k))
    summary = await claims_service.reconcile_pending_claims()
    assert summary["deleted"] == 0, summary
    assert await _doc("raced") is not None


@pytest.mark.asyncio
async def test_a_takeover_returns_the_stored_claim(client):
    await CompositeKeyClaim.claim(NS, ET, _h("take"), "e4", "primary", state="pending", token="t4")
    taken = await CompositeKeyClaim.claim(NS, ET, _h("take"), "e4", "primary", state="pending", token="t5")
    stored = await _doc("take")
    assert taken.created_at.replace(tzinfo=None) == stored["created_at"].replace(tzinfo=None)
    assert taken.claim_token is None and stored["claim_token"] is None


@pytest.mark.asyncio
async def test_activation_under_another_key_releases_the_reserved_one(client, auth_headers):
    """A reservation made with its key claims it; activating it under its
    final key gives the old claim back (e75e6851 R2), so the old key can be
    registered by another entry."""
    from tests.test_reservation_lifecycle import _provision

    old, new = {"k1279": "reserved"}, {"k1279": "final"}
    [entry_id] = await _provision(client, auth_headers, composite_keys=[old])
    resp = await client.post("/api/registry/entries/activate", headers=auth_headers, json=[
        {"entry_id": entry_id, "composite_key": new},
    ])
    assert resp.status_code == 200 and resp.json()["results"][0]["status"] == "activated", resp.text

    coll = CompositeKeyClaim.get_motor_collection()
    old_hash = HashService.compute_composite_key_hash(old)
    assert await coll.find_one({"composite_key_hash": old_hash, "owner_entry_id": entry_id}) is None
    taker = await client.post("/api/registry/entries/register", headers=auth_headers, json=[
        {"namespace": "default", "entity_type": "terms", "composite_key": old},
    ])
    assert taker.json()["results"][0]["status"] == "created", taker.text
