"""Register answers every item with an entry that exists, and mints a batch's
prefixed ids in one counter round trip per prefix.

- A second item in a batch carrying the same key as an earlier one is
  answered from what became of the earlier one: already_exists with its id
  when it was created, the earlier item's error otherwise — never an id that
  was minted and then not written.
- Registering a new key while another request registers it too answers
  already_exists with the entry that request wrote, not an error. An item
  whose own key another entry holds — as that entry's primary key or as one
  of its synonyms — is the same entity and is answered already_exists too; a
  conflict on one of the item's own synonyms stays a conflict: that is a
  different entity. The holder wait is only for another request: an item
  blocked by a key this very batch holds for its own later entry is answered
  without waiting.
- A batch's prefixed ids are reserved with one counter increment per prefix,
  and the namespace is read once per request.
- Provisioning with composite keys needs one key per id.
"""

import asyncio
import uuid

import pytest
from httpx import AsyncClient

from registry.api import entries as entries_api
from registry.models.composite_key_claim import CompositeKeyClaim
from registry.models.entry import RegistryEntry
from registry.models.id_counter import IdCounter
from registry.models.namespace import Namespace
from registry.services.claims import new_claim_token
from registry.services.hash import HashService

BASE = "/api/registry"
REGISTER = f"{BASE}/entries/register"


def _tag() -> str:
    return uuid.uuid4().hex[:10]


async def _post_register(client: AsyncClient, headers: dict, items: list[dict]) -> dict:
    r = await client.post(REGISTER, json=items, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _prefixed_namespace(client: AsyncClient, headers: dict, **prefixes: str) -> str:
    name = f"c1130-{uuid.uuid4().hex[:6]}"
    r = await client.put(f"{BASE}/namespaces/{name}", headers=headers, json={
        "id_config": {t: {"algorithm": "prefixed", "prefix": p, "pad": 6} for t, p in prefixes.items()},
    })
    assert r.status_code == 200, r.text
    return name


@pytest.fixture
def count_next_val(monkeypatch):
    """Every counter round trip, as (counter key, numbers taken)."""
    calls: list[tuple[str, int]] = []
    real_next_val = IdCounter.next_val
    real_reserve = getattr(IdCounter, "reserve", None)

    async def counting_next_val(counter_key: str) -> int:
        calls.append((counter_key, 1))
        return await real_next_val(counter_key)

    monkeypatch.setattr(IdCounter, "next_val", counting_next_val)
    if real_reserve is not None:
        async def counting_reserve(counter_key: str, count: int) -> int:
            calls.append((counter_key, count))
            return await real_reserve(counter_key, count)
        monkeypatch.setattr(IdCounter, "reserve", counting_reserve)
    return calls


# ── item 1: a same-batch duplicate of an item that was not written ────────


@pytest.mark.asyncio
async def test_a_duplicate_of_an_item_that_failed_gets_its_error(client, auth_headers):
    tag = _tag()
    # Another entry holds the synonym the first item would claim.
    owner = (await _post_register(client, auth_headers, [{
        "namespace": "default", "entity_type": "documents",
        "composite_key": {"owner": tag},
    }]))["results"][0]["registry_id"]
    r = await client.post(f"{BASE}/synonyms/add", headers=auth_headers, json=[{
        "target_id": owner, "synonym_namespace": "default", "synonym_entity_type": "documents",
        "synonym_composite_key": {"vendor": tag},
    }])
    assert r.json()["results"][0]["status"] == "added"

    key = {"ns": "default", "template": tag}
    body = await _post_register(client, auth_headers, [
        {"namespace": "default", "entity_type": "documents", "composite_key": dict(key),
         "identity_values": {"code": tag}, "synonym_values": {"vendor": tag}},
        {"namespace": "default", "entity_type": "documents", "composite_key": dict(key),
         "identity_values": {"code": tag}},
    ])
    first, second = body["results"]
    assert first["status"] == "error", first
    assert second["status"] == "error", second
    assert second["error_code"] == first["error_code"]
    assert body["errors"] == 2 and body["already_exists"] == 0
    for result in (first, second):
        if result.get("registry_id"):
            assert await RegistryEntry.find_one({"entry_id": result["registry_id"]}) is not None


@pytest.mark.asyncio
async def test_a_duplicate_of_an_item_that_was_created_names_it(client, auth_headers):
    key = {"ns": "default", "value": _tag()}
    body = await _post_register(client, auth_headers, [
        {"namespace": "default", "entity_type": "terminologies", "composite_key": dict(key)},
        {"namespace": "default", "entity_type": "terminologies", "composite_key": dict(key)},
    ])
    first, second = body["results"]
    assert first["status"] == "created"
    assert second["status"] == "already_exists" and second["registry_id"] == first["registry_id"]
    assert body["created"] == 1 and body["already_exists"] == 1


# ── item 2: a concurrent registration of the same new key ─────────────────


@pytest.mark.asyncio
async def test_concurrent_registrations_of_one_new_key_agree_on_its_entry(client, auth_headers):
    for _ in range(3):
        key = {"ns": "default", "value": _tag()}
        bodies = await asyncio.gather(*(
            _post_register(client, auth_headers, [
                {"namespace": "default", "entity_type": "terminologies", "composite_key": dict(key)},
            ])
            for _ in range(10)
        ))
        results = [b["results"][0] for b in bodies]
        assert all(r["status"] in ("created", "already_exists") for r in results), results
        assert sum(r["status"] == "created" for r in results) == 1
        ids = {r["registry_id"] for r in results}
        assert len(ids) == 1
        assert await RegistryEntry.find_one({"entry_id": ids.pop()}) is not None


@pytest.mark.asyncio
async def test_a_key_held_as_another_entrys_synonym_stays_a_conflict(client, auth_headers):
    tag = _tag()
    owner = (await _post_register(client, auth_headers, [{
        "namespace": "default", "entity_type": "documents", "composite_key": {"owner": tag},
    }]))["results"][0]["registry_id"]
    await client.post(f"{BASE}/synonyms/add", headers=auth_headers, json=[{
        "target_id": owner, "synonym_namespace": "default", "synonym_entity_type": "documents",
        "synonym_composite_key": {"vendor": tag},
    }])
    body = await _post_register(client, auth_headers, [{
        "namespace": "default", "entity_type": "documents",
        "composite_key": {"ns": "default", "template": tag},
        "identity_values": {"code": tag}, "synonym_values": {"vendor": tag},
    }])
    result = body["results"][0]
    assert result["status"] == "error", result
    assert result["details"]["rule"] == "key_claimed"


def _holder_pauses(monkeypatch) -> list[float]:
    """Every holder-window pause taken while the patch is in place."""
    pauses: list[float] = []
    real_sleep = asyncio.sleep

    async def recording(delay: float, *args, **kwargs):
        if delay == entries_api._HOLDER_WAIT_PAUSE:
            pauses.append(delay)
        return await real_sleep(delay, *args, **kwargs)

    monkeypatch.setattr(asyncio, "sleep", recording)
    return pauses


def _unwritten_entry(tag: str, key: dict) -> RegistryEntry:
    """An entry as the gate sees it: built, claimed, not written yet."""
    return RegistryEntry(
        entry_id=f"DOC-{tag}", namespace="default", entity_type="documents",
        primary_composite_key=key,
        primary_composite_key_hash=HashService.compute_composite_key_hash(key),
        status="active",
    )


async def _gate_one(entry: RegistryEntry, token: str) -> tuple[list, dict]:
    results: list = [None]
    gated, _ = await entries_api._gate_on_claims(
        [entry], [0], [None], results, token,
    )
    return gated, results[0]


@pytest.mark.asyncio
async def test_a_key_the_same_batch_holds_is_answered_without_the_holder_wait(
    client, auth_headers, monkeypatch
):
    """The wait bridges another request claiming the key and about to write its
    entry. A claim carrying this request's own token is no such request: the
    entry behind it is written only after the gate, so the window can only
    expire. The item is still the conflict it is — answered at once."""
    tag = _tag()
    key = {"vendor": tag}
    token = new_claim_token()
    claim = await CompositeKeyClaim.claim(
        "default", "documents", HashService.compute_composite_key_hash(key),
        f"DOC-{tag}-batch-mate", "synonym", state="pending", token=token,
    )
    assert claim is not None
    pauses = _holder_pauses(monkeypatch)

    gated, answer = await _gate_one(_unwritten_entry(tag, key), token)

    assert gated == []
    assert answer.status == "error" and answer.details["rule"] == "key_claimed"
    assert pauses == [], "a same-batch claim must not wait out the holder window"
    await CompositeKeyClaim.get_motor_collection().delete_many({"claim_token": token})


@pytest.mark.asyncio
async def test_a_key_another_request_holds_is_still_given_the_holder_window(
    client, auth_headers, monkeypatch
):
    """The other side of the same gate: a foreign in-flight claim gets its
    moment to land an entry, and the item is a conflict only once it didn't."""
    tag = _tag()
    key = {"vendor": tag}
    token = new_claim_token()
    await CompositeKeyClaim.claim(
        "default", "documents", HashService.compute_composite_key_hash(key),
        f"DOC-{tag}-other-request", "synonym", state="pending", token=new_claim_token(),
    )
    pauses = _holder_pauses(monkeypatch)

    gated, answer = await _gate_one(_unwritten_entry(tag, key), token)

    assert gated == []
    assert answer.status == "error" and answer.details["rule"] == "key_claimed"
    assert len(pauses) == entries_api._HOLDER_WAIT_ATTEMPTS - 1, pauses
    await CompositeKeyClaim.get_motor_collection().delete_many({
        "owner_entry_id": f"DOC-{tag}-other-request",
    })


# ── item 3: one counter round trip per prefix ─────────────────────────────


@pytest.mark.asyncio
async def test_a_batch_reserves_its_prefixed_ids_in_one_increment_per_prefix(
    client, auth_headers, count_next_val, monkeypatch,
):
    p_lov, p_item = f"L{_tag()[:5].upper()}-", f"I{_tag()[:5].upper()}-"
    ns = await _prefixed_namespace(client, auth_headers, terminologies=p_lov, terms=p_item)

    reads = []
    real_find = Namespace.find

    def counting_find(*args, **kwargs):
        reads.append(args)
        return real_find(*args, **kwargs)

    monkeypatch.setattr(Namespace, "find", counting_find)

    tag = _tag()
    items = (
        [{"namespace": ns, "entity_type": "terminologies", "composite_key": {"ns": ns, "v": f"{tag}-{i}"}}
         for i in range(20)]
        + [{"namespace": ns, "entity_type": "terms", "composite_key": {"ns": ns, "t": f"{tag}-{i}"}}
           for i in range(5)]
    )
    body = await _post_register(client, auth_headers, items)
    assert body["created"] == 25, body
    ids = [r["registry_id"] for r in body["results"]]
    assert ids[:20] == [f"{p_lov}{n:06d}" for n in range(1, 21)]
    assert ids[20:] == [f"{p_item}{n:06d}" for n in range(1, 6)]

    assert sorted(count_next_val) == sorted([
        (f"@prefix:{p_lov}", 20), (f"@prefix:{p_item}", 5),
    ]), count_next_val
    assert len(reads) == 1


@pytest.mark.asyncio
async def test_items_refused_or_existing_burn_no_numbers(client, auth_headers, count_next_val):
    p = f"B{_tag()[:5].upper()}-"
    ns = await _prefixed_namespace(client, auth_headers, terminologies=p)
    tag = _tag()
    first = await _post_register(client, auth_headers, [
        {"namespace": ns, "entity_type": "terminologies", "composite_key": {"ns": ns, "v": f"{tag}-0"}},
    ])
    assert first["results"][0]["registry_id"] == f"{p}000001"
    body = await _post_register(client, auth_headers, [
        {"namespace": ns, "entity_type": "terminologies", "composite_key": {"ns": ns, "v": f"{tag}-0"}},
        {"namespace": ns, "entity_type": "bogus", "composite_key": {"ns": ns, "v": f"{tag}-x"}},
        {"namespace": ns, "entity_type": "terminologies", "composite_key": {"ns": ns, "v": f"{tag}-1"}},
    ])
    assert [r["status"] for r in body["results"]] == ["already_exists", "error", "created"]
    assert body["results"][2]["registry_id"] == f"{p}000002"


@pytest.mark.asyncio
async def test_a_provision_reserves_its_ids_in_one_increment(client, auth_headers, count_next_val):
    p = f"V{_tag()[:5].upper()}-"
    ns = await _prefixed_namespace(client, auth_headers, terminologies=p)
    r = await client.post(f"{BASE}/entries/provision", headers=auth_headers, json={
        "namespace": ns, "entity_type": "terminologies", "count": 7,
    })
    assert r.status_code == 200, r.text
    assert [x["entry_id"] for x in r.json()["ids"]] == [f"{p}{n:06d}" for n in range(1, 8)]
    assert count_next_val == [(f"@prefix:{p}", 7)]


# ── item 4: provision keys must match the count ───────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("count,keys", [(3, 2), (2, 3)])
async def test_a_provision_with_a_key_per_id_count_mismatch_is_refused(client, auth_headers, count, keys):
    tag = _tag()
    r = await client.post(f"{BASE}/entries/provision", headers=auth_headers, json={
        "namespace": "default", "entity_type": "terminologies", "count": count,
        "composite_keys": [{"ns": "default", "value": f"{tag}-{i}"} for i in range(keys)],
    })
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_a_deferred_claim_provision_still_takes_its_keys(client, auth_headers):
    tag = _tag()
    r = await client.post(f"{BASE}/entries/provision", headers=auth_headers, json={
        "namespace": "default", "entity_type": "documents", "count": 2, "defer_claim": True,
        "composite_keys": [{"ns": "default", "value": f"{tag}-{i}"} for i in range(2)],
    })
    assert r.status_code == 200, r.text
