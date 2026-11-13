"""Activation names who activated; a synonym add's note is the one that landed.

Every entry write names the authenticated caller as updated_by and keeps the
caller's text as actor_note (CASE-1272). Activation, the largest change in
an entry's life (reserved to active, its key claimed), set neither, so the
activator was recorded nowhere. And a synonym-add request took the entry's
note from every item it planned, so a refused item's text overwrote the text
of the addition that landed.
"""

import uuid

import pytest

from registry.models.entry import RegistryEntry

from .test_case_1272_entry_updated_by import BASE, _entry, _namespace, _ok


@pytest.fixture
async def caller(client, auth_headers) -> tuple[dict, str]:
    """A privileged runtime key: (its headers, the identity it writes as)."""
    name = f"c1272r-{uuid.uuid4().hex[:8]}"
    r = await client.post(f"{BASE}/api-keys", headers=auth_headers, json={
        "name": name, "groups": ["wip-admins"],
    })
    assert r.status_code == 201, r.text
    return {"X-API-Key": r.json()["plaintext_key"]}, f"apikey:{name}"


async def _reserve(client, headers, prefix: str, **extra) -> str:
    entry_id = str(uuid.uuid4())
    r = await client.post(f"{BASE}/entries/reserve", headers=headers, json=[{
        "entry_id": entry_id, "namespace": prefix, "entity_type": "terms",
        "created_by": "reserved for ann", **extra,
    }])
    _ok(r, "reserved")
    return entry_id


@pytest.mark.asyncio
async def test_a_keyed_activation_names_the_activator(client, auth_headers, caller):
    headers, who = caller
    prefix = await _namespace(client, auth_headers)
    entry_id = await _reserve(client, auth_headers, prefix)  # reserved by another key
    r = await client.post(f"{BASE}/entries/activate", headers=headers, json=[{
        "entry_id": entry_id, "composite_key": {"v": uuid.uuid4().hex},
    }])
    _ok(r, "activated")
    entry = await RegistryEntry.find_one({"entry_id": entry_id})
    assert entry.created_by != who
    assert (entry.updated_by, entry.actor_note) == (who, None)


@pytest.mark.asyncio
async def test_a_keyless_activation_names_the_activator(client, auth_headers, caller):
    headers, who = caller
    prefix = await _namespace(client, auth_headers)
    entry_id = await _reserve(client, auth_headers, prefix, composite_key={"v": uuid.uuid4().hex})
    r = await client.post(f"{BASE}/entries/activate", headers=headers, json=[{"entry_id": entry_id}])
    _ok(r, "activated")
    entry = await RegistryEntry.find_one({"entry_id": entry_id})
    assert (entry.updated_by, entry.actor_note) == (who, None)


@pytest.mark.asyncio
async def test_a_refused_synonym_add_does_not_write_the_note(client, auth_headers, caller):
    headers, who = caller
    prefix = await _namespace(client, auth_headers)
    taken = {"ext": uuid.uuid4().hex}
    await _entry(client, auth_headers, prefix, synonym=taken)  # another entry holds it
    entry_id = await _entry(client, auth_headers, prefix)

    r = await client.post(f"{BASE}/synonyms/add", headers=headers, json=[
        {"target_id": entry_id, "synonym_composite_key": {"ext": uuid.uuid4().hex},
         "created_by": "alice"},
        {"target_id": entry_id, "synonym_composite_key": taken, "created_by": "bob"},
    ])
    assert r.status_code == 200, r.text
    statuses = [x["status"] for x in sorted(r.json()["results"], key=lambda x: x["index"])]
    assert statuses == ["added", "error"], r.text
    entry = await RegistryEntry.find_one({"entry_id": entry_id})
    assert (entry.updated_by, entry.actor_note) == (who, "alice")


@pytest.mark.asyncio
async def test_entry_responses_describe_who_created_and_changed_it(client):
    schemas = (await client.get("/openapi.json")).json()["components"]["schemas"]
    for name in ("EntryDetailResponse", "UnifiedSearchResultItem"):
        props = schemas[name]["properties"]
        assert "authenticated caller" in props["created_by"]["description"]
        assert "authenticated caller" in props["updated_by"]["description"]
