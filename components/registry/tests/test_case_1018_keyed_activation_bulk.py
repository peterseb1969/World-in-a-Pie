"""Keyed activation in bulk: one read, one claim insert, one guarded write.

A keyed item finalises a deferred-claim reservation with its final key. The
request's keyed items are read together, claimed together and written
together; the per-item results are those of taking the items in order.
"""

import pytest
from httpx import AsyncClient

from registry.models.composite_key_claim import CompositeKeyClaim
from registry.models.entry import RegistryEntry
from registry.services.hash import HashService
from tests.test_reservation_lifecycle import NAMESPACE, _activate, _lookup_by_key, _provision


def _key(value: str) -> dict:
    return {"ns": NAMESPACE, "value": value}


@pytest.mark.asyncio
async def test_a_batch_activates_without_per_item_reads_and_resolves_by_final_key(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    ids = await _provision(client, auth_headers, count=40, defer_claim=True)

    async def no_per_item_read(*args, **kwargs):
        raise AssertionError("keyed activation must not read entries one by one")

    monkeypatch.setattr(RegistryEntry, "find_one", no_per_item_read)
    keys = [_key(f"BULK-1018-{n}") for n in range(40)]
    result = await _activate(client, auth_headers, ids, composite_keys=keys)
    monkeypatch.undo()

    assert result["activated"] == 40 and result["errors"] == 0
    for entry_id, key in zip(ids[:3] + ids[-2:], keys[:3] + keys[-2:], strict=True):
        found = await _lookup_by_key(client, auth_headers, key)
        assert found["status"] == "found" and found["entry_id"] == entry_id
    entry = await RegistryEntry.find_one(RegistryEntry.entry_id == ids[0])
    assert entry.status == "active" and "BULK-1018-0" in entry.search_values
    claim = await CompositeKeyClaim.find_existing(
        NAMESPACE, "terms", HashService.compute_composite_key_hash(keys[0]),
    )
    assert claim.owner_entry_id == ids[0] and claim.state == "confirmed"


@pytest.mark.asyncio
async def test_two_items_of_one_request_with_one_final_key(
    client: AsyncClient, auth_headers: dict
):
    id_a, id_b = await _provision(client, auth_headers, count=2, defer_claim=True)
    key = _key("SAME-IN-ONE-REQUEST")

    result = await _activate(client, auth_headers, [id_a, id_b], composite_keys=[key, key])

    first, second = result["results"]
    assert first["status"] == "activated"
    assert second["status"] == "error" and second["error_code"] == "already_exists"
    assert second["details"] == {"rule": "key_claimed"}
    entry_b = await RegistryEntry.find_one(RegistryEntry.entry_id == id_b)
    assert entry_b.status == "reserved", "the loser is not written"


@pytest.mark.asyncio
async def test_not_found_already_active_and_a_repeated_id(
    client: AsyncClient, auth_headers: dict
):
    (done,) = await _provision(client, auth_headers, defer_claim=True)
    await _activate(client, auth_headers, [done], composite_keys=[_key("DONE-1018")])
    (fresh,) = await _provision(client, auth_headers, defer_claim=True)

    result = await _activate(
        client, auth_headers,
        ["no-such-entry", done, fresh, fresh],
        composite_keys=[_key("X-1018"), _key("DONE-1018"), _key("FRESH-1018"), _key("FRESH-1018")],
    )

    statuses = [r["status"] for r in result["results"]]
    assert statuses == ["not_found", "already_active", "activated", "already_active"]
    assert result["activated"] == 1 and result["errors"] == 1


@pytest.mark.asyncio
async def test_keyed_and_keyless_items_in_one_request(
    client: AsyncClient, auth_headers: dict
):
    (keyless_id,) = await _provision(client, auth_headers, composite_keys=[_key("KEYLESS-1018")])
    (keyed_id,) = await _provision(client, auth_headers, defer_claim=True)

    result = await _activate(
        client, auth_headers, [keyless_id, keyed_id], composite_keys=[None, _key("KEYED-1018")],
    )

    assert [r["status"] for r in result["results"]] == ["activated", "activated"]
    assert (await _lookup_by_key(client, auth_headers, _key("KEYED-1018")))["entry_id"] == keyed_id
