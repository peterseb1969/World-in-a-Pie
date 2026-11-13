"""An entry's updated_by names the authenticated caller, at every write.

Every Registry write that changes an entry took updated_by from the request
item, an optional free-text field: a caller that sent none stored null (the
synonym-lifecycle reconcile pass sends none), and a caller could write any
name it liked. Entries now follow the namespace rule: updated_by is the
identity the request authenticated as, and the caller's text is kept as
actor_note, set on every such write.
"""

import uuid

import pytest

from registry.models.entry import RegistryEntry

BASE = "/api/registry"


@pytest.fixture
async def caller(client, auth_headers) -> tuple[dict, str]:
    """A privileged runtime key: (its headers, the identity it writes as)."""
    name = f"c1272-{uuid.uuid4().hex[:8]}"
    r = await client.post(f"{BASE}/api-keys", headers=auth_headers, json={
        "name": name, "groups": ["wip-admins"],
    })
    assert r.status_code == 201, r.text
    return {"X-API-Key": r.json()["plaintext_key"]}, f"apikey:{name}"


async def _namespace(client, headers) -> str:
    prefix = f"c1272-{uuid.uuid4().hex[:8]}"
    r = await client.put(f"{BASE}/namespaces/{prefix}", headers=headers, json={})
    assert r.status_code == 200, r.text
    return prefix


async def _entry(
    client, headers, prefix, synonym: dict | None = None, entity_type: str = "terms",
) -> str:
    item = {"namespace": prefix, "entity_type": entity_type, "composite_key": {"v": uuid.uuid4().hex}}
    r = await client.post(f"{BASE}/entries/register", headers=headers, json=[item])
    assert r.status_code == 200, r.text
    result = r.json()["results"][0]
    assert result["status"] == "created", result
    if synonym:
        r = await client.post(f"{BASE}/synonyms/add", headers=headers, json=[
            {"target_id": result["registry_id"], "synonym_composite_key": synonym},
        ])
        _ok(r, "added")
    return result["registry_id"]


async def _stamp(entry_id: str) -> tuple[str | None, str | None]:
    entry = await RegistryEntry.find_one({"entry_id": entry_id})
    return entry.updated_by, entry.actor_note


def _ok(r, status: str) -> None:
    assert r.status_code == 200, r.text
    assert r.json()["results"][0]["status"] == status, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("note", [None, "ops on behalf of ann@example.org"])
async def test_an_entry_update_names_the_caller(client, auth_headers, caller, note):
    headers, who = caller
    entry_id = await _entry(client, auth_headers, await _namespace(client, auth_headers))
    item = {"entry_id": entry_id, "metadata": {"k": "v"}}
    if note:
        item["updated_by"] = note
    _ok(await client.put(f"{BASE}/entries", headers=headers, json=[item]), "updated")
    assert await _stamp(entry_id) == (who, note)


@pytest.mark.asyncio
async def test_a_later_write_without_a_note_clears_it(client, auth_headers, caller):
    headers, who = caller
    entry_id = await _entry(client, auth_headers, await _namespace(client, auth_headers))
    _ok(await client.put(f"{BASE}/entries", headers=headers, json=[
        {"entry_id": entry_id, "metadata": {"a": 1}, "updated_by": "first"},
    ]), "updated")
    _ok(await client.put(f"{BASE}/entries", headers=headers, json=[
        {"entry_id": entry_id, "metadata": {"a": 2}},
    ]), "updated")
    assert await _stamp(entry_id) == (who, None)


@pytest.mark.asyncio
async def test_a_soft_delete_names_the_caller(client, auth_headers, caller):
    headers, who = caller
    entry_id = await _entry(client, auth_headers, await _namespace(client, auth_headers))
    r = await client.request("DELETE", f"{BASE}/entries", headers=headers, json=[{"entry_id": entry_id}])
    _ok(r, "deactivated")
    assert await _stamp(entry_id) == (who, None)


@pytest.mark.asyncio
async def test_releasing_and_reclaiming_synonyms_name_the_caller(client, auth_headers, caller):
    headers, who = caller
    prefix = await _namespace(client, auth_headers)
    entry_id = await _entry(client, auth_headers, prefix, synonym={"ext": uuid.uuid4().hex})

    _ok(await client.post(f"{BASE}/entries/deactivate", headers=headers, json=[
        {"entry_id": entry_id, "updated_by": "doc retired by ann"},
    ]), "deactivated")
    assert await _stamp(entry_id) == (who, "doc retired by ann")

    # The reconcile pass sends no updater: the caller is still named.
    _ok(await client.post(f"{BASE}/entries/reactivate", headers=headers, json=[
        {"entry_id": entry_id},
    ]), "reactivated")
    assert await _stamp(entry_id) == (who, None)


@pytest.mark.asyncio
async def test_adding_and_removing_a_synonym_name_the_caller(client, auth_headers, caller):
    headers, who = caller
    prefix = await _namespace(client, auth_headers)
    entry_id = await _entry(client, auth_headers, prefix)
    key = {"ext": uuid.uuid4().hex}

    r = await client.post(f"{BASE}/synonyms/add", headers=headers, json=[
        {"target_id": entry_id, "synonym_composite_key": key},
    ])
    _ok(r, "added")
    assert await _stamp(entry_id) == (who, None)

    r = await client.post(f"{BASE}/synonyms/remove", headers=headers, json=[
        {"target_id": entry_id, "synonym_composite_key": key, "updated_by": "cleanup"},
    ])
    _ok(r, "removed")
    assert await _stamp(entry_id) == (who, "cleanup")


@pytest.mark.asyncio
async def test_a_merge_names_the_caller_on_both_entries(client, auth_headers, caller):
    headers, who = caller
    prefix = await _namespace(client, auth_headers)
    # Terms are merged through def-store; any other entity type merges here.
    preferred, deprecated = (
        await _entry(client, auth_headers, prefix, entity_type="files"),
        await _entry(client, auth_headers, prefix, entity_type="files"),
    )
    r = await client.post(f"{BASE}/synonyms/merge", headers=headers, json=[
        {"preferred_id": preferred, "deprecated_id": deprecated},
    ])
    _ok(r, "merged")
    assert await _stamp(preferred) == (who, None)
    assert await _stamp(deprecated) == (who, None)
    moved = (await RegistryEntry.find_one({"entry_id": preferred})).synonyms
    assert {s.created_by for s in moved if s.merged_from == deprecated} == {who}


# --- created_by: the same rule at creation -------------------------------


@pytest.mark.asyncio
async def test_a_registered_entry_names_its_creator(client, auth_headers, caller):
    headers, who = caller
    prefix = await _namespace(client, auth_headers)
    r = await client.post(f"{BASE}/entries/register", headers=headers, json=[{
        "namespace": prefix, "entity_type": "documents",
        "composite_key": {"v": uuid.uuid4().hex}, "created_by": "import by ann",
        "identity_values": {"k": "v"}, "synonym_values": {"@template": "T", "k": "v"},
    }])
    assert r.status_code == 200, r.text
    entry_id = r.json()["results"][0]["registry_id"]
    entry = await RegistryEntry.find_one({"entry_id": entry_id})
    assert (entry.created_by, entry.actor_note) == (who, "import by ann")
    assert entry.synonyms and {s.created_by for s in entry.synonyms} == {who}


@pytest.mark.asyncio
async def test_reserved_and_activated_entries_name_their_creator(client, auth_headers, caller):
    headers, who = caller
    prefix = await _namespace(client, auth_headers)
    entry_id = str(uuid.uuid4())
    r = await client.post(f"{BASE}/entries/reserve", headers=headers, json=[{
        "entry_id": entry_id, "namespace": prefix, "entity_type": "terms",
        "created_by": "reserved for ann",
    }])
    _ok(r, "reserved")
    r = await client.post(f"{BASE}/entries/activate", headers=headers, json=[{
        "entry_id": entry_id, "composite_key": {"v": uuid.uuid4().hex},
        "synonyms": [{"synonym_composite_key": {"ext": uuid.uuid4().hex}, "created_by": "text"}],
    }])
    assert r.status_code == 200, r.text
    entry = await RegistryEntry.find_one({"entry_id": entry_id})
    assert entry.status == "active", r.text
    assert (entry.created_by, entry.actor_note) == (who, "reserved for ann")
    assert {s.created_by for s in entry.synonyms} == {who}


@pytest.mark.asyncio
async def test_provisioned_entries_name_their_creator(client, auth_headers, caller):
    headers, who = caller
    prefix = await _namespace(client, auth_headers)
    r = await client.post(f"{BASE}/entries/provision", headers=headers, json={
        "namespace": prefix, "entity_type": "terms", "count": 1, "created_by": "seed",
    })
    assert r.status_code == 200, r.text
    entry_id = r.json()["ids"][0]["entry_id"]
    entry = await RegistryEntry.find_one({"entry_id": entry_id})
    assert (entry.created_by, entry.actor_note) == (who, "seed")


@pytest.mark.asyncio
async def test_an_added_synonym_names_its_creator(client, auth_headers, caller):
    headers, who = caller
    entry_id = await _entry(client, auth_headers, await _namespace(client, auth_headers))
    r = await client.post(f"{BASE}/synonyms/add", headers=headers, json=[{
        "target_id": entry_id, "synonym_composite_key": {"ext": uuid.uuid4().hex},
        "created_by": "added by ann",
    }])
    _ok(r, "added")
    entry = await RegistryEntry.find_one({"entry_id": entry_id})
    assert (entry.updated_by, entry.actor_note) == (who, "added by ann")
    assert [s.created_by for s in entry.synonyms if not entry.is_auto_synonym(s)] == [who]
