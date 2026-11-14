"""IDs written without the Registry are never minted again.

A restore and a merge write the Registry entries an archive carries straight
to MongoDB. A Registry process reads a prefix's highest existing ID once (the
first time it mints from it) and after that raises the counter only for IDs it
is given, so one that had minted from a prefix before the restore minted the
restored IDs again. /entries/advance-counters is how such a writer gives it
them. The floor itself orders IDs by number, not by spelling, and a number
past the counter's range moves no counter. An ordered insert that fails
partway answers each item it did not write for itself.
"""

import uuid

import pytest
from httpx import AsyncClient

from registry.models.entry import RegistryEntry
from registry.models.id_counter import IdCounter
from registry.services.id_generator import MAX_SEQUENCE, prefix_counter_key
from tests.test_case_1125_prefix_counters import BASE, _mint, _namespace, _ns, _prefix, _register


async def _write_directly(ns: str, *entry_ids: str) -> None:
    """Entries as a restore writes them: into the collection, no Registry call."""
    for entry_id in entry_ids:
        await RegistryEntry(
            entry_id=entry_id, namespace=ns, entity_type="terminologies",
            primary_composite_key={}, primary_composite_key_hash="", status="active",
        ).insert()


async def _advance(client: AsyncClient, headers: dict, *entry_ids: str) -> dict:
    r = await client.post(
        f"{BASE}/entries/advance-counters", headers=headers,
        json=[{"entry_id": i} for i in entry_ids],
    )
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_restored_ids_are_not_minted_by_a_process_that_already_counted(
    client: AsyncClient, auth_headers: dict,
):
    ns, p = _ns(), _prefix()
    await _namespace(client, auth_headers, ns, terminologies=p)
    assert await _mint(client, auth_headers, ns) == f"{p}000001"  # floored in this process
    restored = [f"{p}{n:06d}" for n in (2, 3, 4)]

    body = await _advance(client, auth_headers, *restored, "019a0000-0000-7000-8000-000000000000")
    await _write_directly(ns, *restored)

    assert [r["status"] for r in body["results"]] == ["advanced"] * 3 + ["skipped"]
    assert body["results"][3]["rule"] == "not_prefixed"
    assert body["results"][0]["prefix"] == p
    assert (body["advanced"], body["skipped"]) == (3, 1)
    assert await _mint(client, auth_headers, ns) == f"{p}000005"


@pytest.mark.asyncio
async def test_without_the_advance_a_counted_prefix_mints_a_restored_id(
    client: AsyncClient, auth_headers: dict,
):
    """The defect the route exists for: the next mint is a restored ID."""
    ns, p = _ns(), _prefix()
    await _namespace(client, auth_headers, ns, terminologies=p)
    assert await _mint(client, auth_headers, ns) == f"{p}000001"
    await _write_directly(ns, f"{p}000002")
    (result,) = await _register(client, auth_headers, ns, {})
    assert result["status"] == "error" and result["error_code"] == "already_exists"


@pytest.mark.asyncio
async def test_an_id_past_the_counter_range_moves_no_counter(client: AsyncClient, auth_headers: dict):
    ns, p = _ns(), _prefix()
    await _namespace(client, auth_headers, ns, terminologies=p)
    huge = f"{p}{MAX_SEQUENCE * 10}"
    body = await _advance(client, auth_headers, huge, f"{p}000007")
    assert [(r["status"], r["rule"]) for r in body["results"]] == [
        ("skipped", "beyond_sequence_range"), ("advanced", None),
    ]
    counter = await IdCounter.find_one({"counter_key": prefix_counter_key(p)})
    assert counter is not None and counter.seq == 7
    assert await _mint(client, auth_headers, ns) == f"{p}000008"


@pytest.mark.asyncio
async def test_the_floor_reads_ids_by_number_not_by_length(client: AsyncClient, auth_headers: dict):
    """IDs minted under a wide pad, then a narrower one: the narrow ones are
    shorter and higher. An ID past the counter range is left out."""
    ns, p = _ns(), _prefix()
    await _namespace(client, auth_headers, ns, terminologies=p)
    await _write_directly(ns, f"{p}000099", f"{p}0150", f"{p}{MAX_SEQUENCE * 10}")
    assert await _mint(client, auth_headers, ns) == f"{p}000151"


@pytest.mark.asyncio
async def test_an_insert_that_stops_partway_answers_each_item_for_itself(
    client: AsyncClient, auth_headers: dict,
):
    """Two items of one batch name the same ID: the second fails the ordered
    insert, and the free item after it was never attempted."""
    ns, p = _ns(), _prefix()
    await _namespace(client, auth_headers, ns, terminologies=p)
    taken, free = f"{p}000001", f"{p}000002"
    results = await _register(
        client, auth_headers, ns, {"entry_id": taken}, {"entry_id": taken}, {"entry_id": free},
    )
    assert results[0]["status"] == "created"
    assert results[1]["error_code"] == "already_exists"
    assert results[1]["details"]["entry_id"] == taken
    assert results[2]["error_code"] == "internal_error"
    assert results[2]["details"] == {"rule": "batch_stopped", "failed_index": 1}
    assert free not in results[2]["error"] and taken not in results[2]["error"]


@pytest.mark.asyncio
async def test_advance_counters_needs_a_privileged_key(client: AsyncClient):
    r = await client.post(
        f"{BASE}/entries/advance-counters", headers={"X-API-Key": f"nope-{uuid.uuid4().hex}"},
        json=[{"entry_id": "X-000001"}],
    )
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_a_prefix_no_namespace_configures_any_more_still_advances(
    client: AsyncClient, auth_headers: dict,
):
    """CASE-1297#4 (47b5826c R2): only configured prefixes were considered,
    so an id restored under a prefix its namespace had moved off was
    skipped as not_prefixed, and the prefix, configured again, minted it."""
    ns, p = _ns(), _prefix()
    await _namespace(client, auth_headers, ns, terminologies=p)
    assert await _mint(client, auth_headers, ns) == f"{p}000001"
    await _namespace(client, auth_headers, ns, terminologies=_prefix())  # p is now unconfigured
    body = await _advance(client, auth_headers, f"{p}000009")
    assert (body["results"][0]["status"], body["results"][0]["prefix"]) == ("advanced", p)
    await _write_directly(ns, f"{p}000009")
    await _namespace(client, auth_headers, ns, terminologies=p)
    assert await _mint(client, auth_headers, ns) == f"{p}000010"


@pytest.mark.asyncio
async def test_an_oversized_legacy_counter_moves_no_floor(client: AsyncClient, auth_headers: dict):
    """CASE-1297#4 (47b5826c R3): the older per-namespace counters fed the
    floor with no bound."""
    ns, p = _ns(), _prefix()
    await _namespace(client, auth_headers, ns, terminologies=p)
    await IdCounter(counter_key=f"{ns}-old:terminologies:{p}", seq=2**63 - 1).insert()
    await _write_directly(ns, f"{p}000004")
    assert await _mint(client, auth_headers, ns) == f"{p}000005"
