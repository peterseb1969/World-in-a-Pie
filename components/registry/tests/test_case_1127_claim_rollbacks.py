"""A composite key stays claimed by the entry that holds it, whatever fails
around it (CASE-1127).

The claims collection is the uniqueness gate for composite keys: an entry
whose key has no claim lets a second entry take that key as a synonym, after
which lookups by it are ambiguous. These tests drive the ways a write could
leave a held key unclaimed — a rollback that releases a key its owner already
held, a re-key that moves the primary key without its claim, a failed batch
insert that releases the claims of entries it did write, a request that
releases a claim another request on the same entry took over, and an orphan
audit that deletes an in-flight claim — and check that the key stays claimed.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from registry.models.composite_key_claim import CompositeKeyClaim
from registry.models.entry import RegistryEntry
from registry.services.claims import (
    claim_keys_pending_bulk,
    new_claim_token,
    reconcile_orphan_claims,
    release_keys_bulk,
)
from registry.services.hash import HashService

NS, ET = "ns1127", "terms"


def _h(name: str) -> str:
    return HashService.compute_composite_key_hash({"k": name})


def _key(name: str, owner: str, kind: str = "primary") -> tuple:
    return (NS, ET, _h(name), owner, kind)


async def _claim_doc(key_hash: str, namespace: str = NS, entity_type: str = ET) -> dict | None:
    return await CompositeKeyClaim.get_motor_collection().find_one(
        {"namespace": namespace, "entity_type": entity_type, "composite_key_hash": key_hash}
    )


async def _genuine_owner(name: str, owner: str) -> None:
    """An entry holding the key, with its confirmed claim."""
    await RegistryEntry(
        entry_id=owner, namespace=NS, entity_type=ET,
        primary_composite_key={"k": name}, primary_composite_key_hash=_h(name),
        status="active", created_by="test",
    ).insert()
    await CompositeKeyClaim.claim(NS, ET, _h(name), owner, "primary")


async def _age(key_hash: str, seconds: int = 3600) -> None:
    await CompositeKeyClaim.get_motor_collection().update_one(
        {"namespace": NS, "entity_type": ET, "composite_key_hash": key_hash},
        {"$set": {"created_at": datetime.now(UTC) - timedelta(seconds=seconds)}},
    )


async def _post(client, auth_headers, path: str, body) -> dict:
    r = await client.post(f"/api/registry{path}", json=body, headers=auth_headers)
    assert r.status_code == 200, r.text
    return r.json()


# ── A rollback releases only what its own request took ──────────────────


@pytest.mark.asyncio
async def test_a_blocked_group_keeps_the_claim_its_owner_already_held(client):
    await _genuine_owner("mine", "e1")
    await _genuine_owner("theirs", "outsider")

    blocked = await claim_keys_pending_bulk([[_key("mine", "e1"), _key("theirs", "e1", "synonym")]])

    assert blocked == ["outsider"]
    doc = await _claim_doc(_h("mine"))
    assert doc is not None and doc["owner_entry_id"] == "e1"


@pytest.mark.asyncio
async def test_a_blocked_group_still_releases_the_keys_it_took(client):
    await _genuine_owner("theirs", "outsider")

    await claim_keys_pending_bulk([[_key("fresh", "e1"), _key("theirs", "e1", "synonym")]])

    assert await _claim_doc(_h("fresh")) is None


@pytest.mark.asyncio
async def test_activation_refused_by_a_taken_synonym_keeps_the_reserved_key_claimed(
    client, auth_headers,
):
    """A reservation made with its key, activated with a synonym another
    entry holds: the activation fails, and the reservation keeps its key."""
    key = {"k1127": "reserved"}
    provisioned = await _post(client, auth_headers, "/entries/provision", {
        "namespace": "default", "entity_type": "terms", "count": 1, "composite_keys": [key],
    })
    entry_id = provisioned["ids"][0]["entry_id"]
    taken = {"k1127": "taken"}
    other = await _post(client, auth_headers, "/entries/register", [{
        "namespace": "default", "entity_type": "terms", "composite_key": taken,
    }])
    assert other["results"][0]["status"] == "created"

    activated = await _post(client, auth_headers, "/entries/activate", [{
        "entry_id": entry_id, "composite_key": key,
        "synonyms": [{"synonym_composite_key": taken}],
    }])

    assert activated["results"][0]["status"] == "error"
    doc = await _claim_doc(HashService.compute_composite_key_hash(key), "default", "terms")
    assert doc is not None and doc["owner_entry_id"] == entry_id


@pytest.mark.asyncio
async def test_a_release_cannot_delete_a_claim_another_request_took_over(client):
    """Two requests for one entry: the first inserts the claim, the second
    finds it already its entry's. When the first rolls back, the second
    still needs the key — the first's release must leave it."""
    first, second = new_claim_token(), new_claim_token()
    assert await claim_keys_pending_bulk([[_key("shared", "e1")]], first) == [None]
    assert await claim_keys_pending_bulk([[_key("shared", "e1")]], second) == [None]

    await release_keys_bulk([_key("shared", "e1")], first)

    assert await _claim_doc(_h("shared")) is not None


@pytest.mark.asyncio
async def test_a_release_deletes_the_claim_its_request_inserted(client):
    token = new_claim_token()
    await claim_keys_pending_bulk([[_key("own", "e1")]], token)

    await release_keys_bulk([_key("own", "e1")], token)

    assert await _claim_doc(_h("own")) is None


@pytest.mark.asyncio
async def test_a_held_claim_taken_again_pending_is_young_and_pending(client):
    """A request that finds its entry already holding a key takes the claim
    as its own in-flight claim: pending and young, so a healer that runs
    meanwhile leaves it alone."""
    await CompositeKeyClaim.claim(NS, ET, _h("residue"), "e1", "primary")
    await _age(_h("residue"))

    assert await claim_keys_pending_bulk([[_key("residue", "e1")]]) == [None]

    doc = await _claim_doc(_h("residue"))
    assert doc["state"] == "pending"
    created = doc["created_at"].replace(tzinfo=UTC)
    assert datetime.now(UTC) - created < timedelta(seconds=30)


# ── A failed batch insert tells written entries from unwritten ones ─────


@pytest.mark.asyncio
async def test_register_keeps_the_claims_of_the_entries_a_failed_insert_wrote(
    client, auth_headers,
):
    """The batch insert is ordered: entries before the failing one are
    written, the rest are not. The written entry is reported created and
    keeps its claim; the others are errors and their claims go."""
    same_id = f"E1127-{uuid.uuid4().hex[:8]}"
    keys = [{"k1127": f"batch-{n}"} for n in range(3)]
    data = await _post(client, auth_headers, "/entries/register", [
        {"namespace": "default", "entity_type": "terms", "composite_key": keys[0], "entry_id": same_id},
        {"namespace": "default", "entity_type": "terms", "composite_key": keys[1], "entry_id": same_id},
        {"namespace": "default", "entity_type": "terms", "composite_key": keys[2],
         "entry_id": f"E1127-{uuid.uuid4().hex[:8]}"},
    ])

    assert [r["status"] for r in data["results"]] == ["created", "error", "error"]
    assert data["created"] == 1 and data["errors"] == 2
    hashes = [HashService.compute_composite_key_hash(k) for k in keys]
    written = await _claim_doc(hashes[0], "default", "terms")
    assert written is not None and written["owner_entry_id"] == same_id
    assert written["state"] == "confirmed"
    assert await _claim_doc(hashes[1], "default", "terms") is None
    assert await _claim_doc(hashes[2], "default", "terms") is None


@pytest.mark.asyncio
async def test_reserve_keeps_the_claims_of_the_entries_a_failed_insert_wrote(
    client, auth_headers,
):
    same_id = str(uuid.uuid4())
    keys = [{"k1127": f"reserve-{n}"} for n in range(2)]
    data = await _post(client, auth_headers, "/entries/reserve", [
        {"namespace": "default", "entity_type": "terms", "composite_key": keys[0], "entry_id": same_id},
        {"namespace": "default", "entity_type": "terms", "composite_key": keys[1], "entry_id": same_id},
    ])

    assert [r["status"] for r in data["results"]] == ["reserved", "error"]
    hashes = [HashService.compute_composite_key_hash(k) for k in keys]
    written = await _claim_doc(hashes[0], "default", "terms")
    assert written is not None and written["owner_entry_id"] == same_id
    assert await _claim_doc(hashes[1], "default", "terms") is None


# ── Re-keying moves the claim with the key ───────────────────────────────


async def _register_doc(client, auth_headers, name: str) -> tuple[str, dict]:
    data = await _post(client, auth_headers, "/entries/register", [{
        "namespace": "default", "entity_type": "documents",
        "composite_key": {"ns": "default", "template_id": "TPL-1127"},
        "identity_values": {"name": name},
    }])
    entry_id = data["results"][0]["registry_id"]
    r = await client.get(f"/api/registry/entries/{entry_id}", headers=auth_headers)
    return entry_id, r.json()["primary_composite_key"]


@pytest.mark.asyncio
async def test_rekey_claims_the_new_key_and_releases_the_old(client, auth_headers):
    entry_id, old_key = await _register_doc(client, auth_headers, "rekeyed")
    new_key = {**old_key, "identity_hash": "rekeyed-1127"}
    before = await RegistryEntry.find_one(RegistryEntry.entry_id == entry_id)

    data = await _post(client, auth_headers, "/entries/rekey-identity", [
        {"entry_id": entry_id, "identity_hash": "rekeyed-1127"},
    ])

    assert data["results"][0]["status"] == "rekeyed"
    new_hash = HashService.compute_composite_key_hash(new_key)
    claim = await _claim_doc(new_hash, "default", "documents")
    assert claim is not None and claim["owner_entry_id"] == entry_id
    assert claim["state"] == "confirmed"
    old_hash = HashService.compute_composite_key_hash(old_key)
    assert await _claim_doc(old_hash, "default", "documents") is None
    after = await RegistryEntry.find_one(RegistryEntry.entry_id == entry_id)
    assert after.updated_at > before.updated_at

    other_id, _ = await _register_doc(client, auth_headers, "bystander")
    added = await _post(client, auth_headers, "/synonyms/add", [{
        "target_id": other_id, "synonym_namespace": "default",
        "synonym_entity_type": "documents", "synonym_composite_key": new_key,
    }])
    assert added["results"][0]["status"] == "error"


@pytest.mark.asyncio
async def test_rekey_refuses_a_key_another_entry_holds_as_a_synonym(client, auth_headers):
    entry_id, old_key = await _register_doc(client, auth_headers, "mover")
    new_key = {**old_key, "identity_hash": "held-1127"}
    holder_id, _ = await _register_doc(client, auth_headers, "holder")
    added = await _post(client, auth_headers, "/synonyms/add", [{
        "target_id": holder_id, "synonym_namespace": "default",
        "synonym_entity_type": "documents", "synonym_composite_key": new_key,
    }])
    assert added["results"][0]["status"] == "added"

    data = await _post(client, auth_headers, "/entries/rekey-identity", [
        {"entry_id": entry_id, "identity_hash": "held-1127"},
    ])

    assert data["results"][0]["status"] == "conflict"
    assert holder_id in data["results"][0]["error"]
    entry = await RegistryEntry.find_one(RegistryEntry.entry_id == entry_id)
    assert entry.primary_composite_key == old_key


# ── The orphan audit leaves in-flight claims alone ───────────────────────


@pytest.mark.asyncio
async def test_orphan_audit_leaves_a_young_pending_claim_alone(client):
    """A claim-first request's claim exists before its entry does; the
    audit must not read it as an orphan while it is in flight."""
    await CompositeKeyClaim.claim(NS, ET, _h("inflight"), "not-yet-written", "primary", state="pending")
    await CompositeKeyClaim.claim(NS, ET, _h("dead"), "never-written", "primary", state="pending")
    await _age(_h("dead"))
    await CompositeKeyClaim.claim(NS, ET, _h("dangling"), "gone", "primary")

    await reconcile_orphan_claims()

    assert await _claim_doc(_h("inflight")) is not None
    assert await _claim_doc(_h("dead")) is None
    assert await _claim_doc(_h("dangling")) is None


# ── An untokened release keeps a claim taken over after its write ───────


@pytest.mark.asyncio
async def test_a_release_dated_before_keeps_a_claim_taken_over_since(client):
    await _genuine_owner("kept", "e1")
    cutoff = datetime.now(UTC)
    # Another request of the same entry takes the claim over after the
    # releasing request's write: the takeover re-dates it.
    await CompositeKeyClaim.claim(NS, ET, _h("kept"), "e1", "primary", state="pending")

    await release_keys_bulk([_key("kept", "e1")], dated_before=cutoff)

    assert (await _claim_doc(_h("kept")))["owner_entry_id"] == "e1"


@pytest.mark.asyncio
async def test_a_release_dated_before_still_deletes_an_older_claim(client):
    await _genuine_owner("gone", "e1")

    await release_keys_bulk([_key("gone", "e1")], dated_before=datetime.now(UTC) + timedelta(seconds=1))

    assert await _claim_doc(_h("gone")) is None


@pytest.mark.asyncio
async def test_a_removal_keeps_the_claim_of_an_add_that_put_the_synonym_back(
    client, auth_headers, monkeypatch,
):
    """A removal of a synonym and an add of the same synonym to the same
    entry: the add loads the entry after the removal's write, takes the claim
    over, writes the synonym back and confirms, all before the removal
    releases. The synonym is active on the entry, so its claim must stay:
    without it another entry could take the key."""
    import registry.api.synonyms as synonyms_api
    from tests.test_case_427 import _add_synonym, _register

    ns, etype = "default", "templates"
    target = await _register(client, auth_headers, ns, etype, {"value": "RACED"})
    key = {"vendor": "RACED-1"}
    assert (await _add_synonym(client, auth_headers, target, ns, etype, key))["status"] == "added"

    real_release = synonyms_api.release_keys_bulk
    fired = []

    async def add_then_release(keys, token=None, **kw):
        if token is None and not fired:
            fired.append(True)
            added = await _add_synonym(client, auth_headers, target, ns, etype, key)
            assert added["status"] == "added", added
        await real_release(keys, token, **kw)

    monkeypatch.setattr(synonyms_api, "release_keys_bulk", add_then_release)
    r = await client.post("/api/registry/synonyms/remove", headers=auth_headers, json=[{
        "target_id": target, "synonym_composite_key": key,
    }])
    assert r.json()["results"][0]["status"] == "removed", r.text
    assert fired

    key_hash = HashService.compute_composite_key_hash(key)
    entry = await RegistryEntry.find_one({"entry_id": target})
    assert key_hash in {s.composite_key_hash for s in entry.active_synonyms()}
    claim = await _claim_doc(key_hash, ns, etype)
    assert claim is not None and claim["owner_entry_id"] == target
    other = await _register(client, auth_headers, ns, etype, {"value": "INTRUDER"})
    taken = await _add_synonym(client, auth_headers, other, ns, etype, key)
    assert taken["status"] != "added", taken


# ── A re-key or retirement answers by what its write did ────────────────


def _delete_before_write(monkeypatch, module, entry_id: str) -> None:
    """The entry is deleted between the route's read and its write."""
    real = module.apply_to_entry

    async def gone_first(target, *a, **kw):
        await RegistryEntry.get_motor_collection().delete_one({"entry_id": entry_id})
        return await real(target, *a, **kw)

    monkeypatch.setattr(module, "apply_to_entry", gone_first)


@pytest.mark.asyncio
async def test_rekey_of_an_entry_deleted_meanwhile_is_not_found(client, auth_headers, monkeypatch):
    import registry.api.entries as entries_api

    entry_id, old_key = await _register_doc(client, auth_headers, "vanishing")
    _delete_before_write(monkeypatch, entries_api, entry_id)

    data = await _post(client, auth_headers, "/entries/rekey-identity", [
        {"entry_id": entry_id, "identity_hash": "vanished-1127"},
    ])

    assert data["results"][0]["status"] == "not_found"
    assert (data["succeeded"], data["failed"]) == (0, 1)
    new_hash = HashService.compute_composite_key_hash({**old_key, "identity_hash": "vanished-1127"})
    assert await _claim_doc(new_hash, "default", "documents") is None


@pytest.mark.asyncio
async def test_rekey_another_request_already_applied_is_unchanged(client, auth_headers, monkeypatch):
    import registry.api.entries as entries_api

    entry_id, old_key = await _register_doc(client, auth_headers, "raced")
    new_key = {**old_key, "identity_hash": "raced-1127"}
    real = entries_api.apply_to_entry

    async def other_request_first(target, *a, **kw):
        await RegistryEntry.get_motor_collection().update_one(
            {"entry_id": entry_id},
            {"$set": {"primary_composite_key": new_key,
                      "primary_composite_key_hash": HashService.compute_composite_key_hash(new_key)},
             "$inc": {"revision": 1}},
        )
        return await real(target, *a, **kw)

    monkeypatch.setattr(entries_api, "apply_to_entry", other_request_first)
    data = await _post(client, auth_headers, "/entries/rekey-identity", [
        {"entry_id": entry_id, "identity_hash": "raced-1127"},
    ])
    assert data["results"][0]["status"] == "unchanged"


@pytest.mark.asyncio
async def test_deactivating_an_entry_deleted_meanwhile_is_not_found(client, auth_headers, monkeypatch):
    import registry.services.claims as claims_service

    entry_id, _ = await _register_doc(client, auth_headers, "retiring")
    added = await _post(client, auth_headers, "/synonyms/add", [{
        "target_id": entry_id, "synonym_namespace": "default",
        "synonym_entity_type": "documents", "synonym_composite_key": {"vendor": "RETIRING-1127"},
    }])
    assert added["results"][0]["status"] == "added"
    _delete_before_write(monkeypatch, claims_service, entry_id)

    data = await _post(client, auth_headers, "/entries/deactivate", [{"entry_id": entry_id}])

    assert data["results"][0]["status"] == "not_found"
