"""An entry's synonym note is the text of the change that landed, in both
directions.

Review of 6775e205 (CASE-1272) found the note taken from a planned position:
/synonyms/add picked the first addition it planned, so when another writer
added that synonym first and the re-read answered the item already_exists, the
entry was stamped with the words of the addition that did not land and dropped
the words of the one that did. /synonyms/remove picked the last removal it
planned, which a re-read can also un-land. The note now comes from the same
outcomes the items are answered from, and remove follows add in taking the
first that landed.

Provenance: CASE-1335.
"""

import uuid

import pytest

from registry.api import synonyms as synonyms_api
from registry.models.entry import RegistryEntry

from .test_case_1019_synonyms_bulk import _add_item
from .test_case_1161_remove_synonyms import _rm
from .test_case_1272_entry_updated_by import BASE, _entry, _namespace, _ok, _stamp


@pytest.fixture
async def caller(client, auth_headers) -> tuple[dict, str]:
    """A privileged runtime key: (its headers, the identity it writes as)."""
    name = f"c1335-{uuid.uuid4().hex[:8]}"
    r = await client.post(f"{BASE}/api-keys", headers=auth_headers, json={
        "name": name, "groups": ["wip-admins"],
    })
    assert r.status_code == 201, r.text
    return {"X-API-Key": r.json()["plaintext_key"]}, f"apikey:{name}"


def _lose_the_first_write(monkeypatch, rival) -> list[str]:
    """Make the request's first write lose a race, the way a concurrent writer
    does: ``rival(entry, fields, guard, real)`` changes the stored entry with
    ``real``, and this request's own write over the revision it read does not."""
    real = synonyms_api.write_entry
    lost: list[str] = []

    async def lose_once(entry, fields, guard):
        if not lost:
            lost.append(entry.entry_id)
            await rival(entry, fields, guard, real)
            return False
        return await real(entry, fields, guard)

    monkeypatch.setattr(synonyms_api, "write_entry", lose_once)
    return lost


async def _held(entry_id: str) -> list[dict]:
    entry = await RegistryEntry.find_one(RegistryEntry.entry_id == entry_id)
    return [s.composite_key for s in entry.synonyms if not entry.is_auto_synonym(s)]


@pytest.mark.asyncio
async def test_the_note_of_several_additions_is_the_one_that_landed(
    client, auth_headers, caller, monkeypatch,
):
    """A adds A for E then B for E, sending its own words with each. A
    concurrent request adds A to E first, so this one lands B only: the note is
    B's text, not the text it had planned to use for A."""
    headers, who = caller
    entry_id = await _entry(client, auth_headers, await _namespace(client, auth_headers))
    a, b = {"ext": uuid.uuid4().hex}, {"ext": uuid.uuid4().hex}

    async def add_a_first(entry, fields, guard, real):
        # The other request's write: A, and its own words on it.
        rival = entry.model_copy(deep=True)
        rival.synonyms = [s for s in rival.synonyms if s.composite_key != b]
        rival.updated_by, rival.actor_note = "apikey:rival", "the other request's words"
        assert await real(rival, fields, guard) is True

    lost = _lose_the_first_write(monkeypatch, add_a_first)
    r = await client.post(f"{BASE}/synonyms/add", headers=headers, json=[
        {**_add_item(entry_id, a), "created_by": "alice"},
        {**_add_item(entry_id, b), "created_by": "bob"},
    ])
    assert r.status_code == 200, r.text
    assert [x["status"] for x in r.json()["results"]] == ["already_exists", "added"], r.text
    assert lost == [entry_id]
    assert await _held(entry_id) == [a, b]
    assert await _stamp(entry_id) == (who, "bob")


@pytest.mark.asyncio
async def test_the_note_of_several_removals_is_the_first_that_landed(
    client, auth_headers, caller,
):
    """Both removals land: the note is the first item's text, the rule add
    follows, not the last item's."""
    headers, who = caller
    prefix = await _namespace(client, auth_headers)
    entry_id = await _entry(client, auth_headers, prefix)
    a, b = {"ext": uuid.uuid4().hex}, {"ext": uuid.uuid4().hex}
    for key in (a, b):
        _ok(await client.post(f"{BASE}/synonyms/add", headers=headers, json=[_add_item(entry_id, key)]),
            "added")

    r = await client.post(f"{BASE}/synonyms/remove", headers=headers, json=[
        {**_rm(entry_id, a), "updated_by": "ann"},
        {**_rm(entry_id, b), "updated_by": "ben"},
    ])
    assert r.status_code == 200, r.text
    assert [x["status"] for x in r.json()["results"]] == ["removed", "removed"], r.text
    assert await _held(entry_id) == []
    assert await _stamp(entry_id) == (who, "ann")


@pytest.mark.asyncio
async def test_the_note_of_a_removal_another_request_made_first_is_the_one_that_landed(
    client, auth_headers, caller, monkeypatch,
):
    """A removes A for E then B for E. A concurrent request removes B first, so
    this one lands A only: the note is A's text, never the text sent with the
    removal this request answers not_found."""
    headers, who = caller
    prefix = await _namespace(client, auth_headers)
    entry_id = await _entry(client, auth_headers, prefix)
    a, b = {"ext": uuid.uuid4().hex}, {"ext": uuid.uuid4().hex}
    for key in (a, b):
        _ok(await client.post(f"{BASE}/synonyms/add", headers=headers, json=[_add_item(entry_id, key)]),
            "added")

    async def drop_b(entry, fields, guard, real):
        stored = await RegistryEntry.find_one(RegistryEntry.entry_id == entry.entry_id)
        stored.synonyms = [s for s in stored.synonyms if s.composite_key != b]
        assert await real(stored, fields, guard) is True

    lost = _lose_the_first_write(monkeypatch, drop_b)
    r = await client.post(f"{BASE}/synonyms/remove", headers=headers, json=[
        {**_rm(entry_id, a), "updated_by": "ann"},
        {**_rm(entry_id, b), "updated_by": "ben"},
    ])
    assert r.status_code == 200, r.text
    assert [x["status"] for x in r.json()["results"]] == ["removed", "not_found"], r.text
    assert lost == [entry_id]
    assert await _held(entry_id) == []  # B went in the other request, A in this one
    assert await _stamp(entry_id) == (who, "ann")


@pytest.mark.asyncio
async def test_a_write_that_landed_before_its_error_keeps_the_note_of_its_own_first_addition(
    client, auth_headers, caller, monkeypatch,
):
    """The first write raises without saying whether it landed. The re-read
    finds both additions made, which are this request's own: both items answer
    added, the note is the first of them, and the entry is not written a
    second time (the first write's stamp stands)."""
    headers, who = caller
    entry_id = await _entry(client, auth_headers, await _namespace(client, auth_headers))
    a, b = {"ext": uuid.uuid4().hex}, {"ext": uuid.uuid4().hex}
    real = synonyms_api.write_entry
    first: list[str] = []

    async def land_then_error(entry, fields, guard):
        if not first:
            first.append(entry.entry_id)
            await real(entry, fields, guard)
            landed = await RegistryEntry.find_one(RegistryEntry.entry_id == entry.entry_id)
            first.append(landed.revision)
            raise RuntimeError("the answer was lost")
        return await real(entry, fields, guard)

    monkeypatch.setattr(synonyms_api, "write_entry", land_then_error)
    r = await client.post(f"{BASE}/synonyms/add", headers=headers, json=[
        {**_add_item(entry_id, a), "created_by": "alice"},
        {**_add_item(entry_id, b), "created_by": "bob"},
    ])
    assert r.status_code == 200, r.text
    assert [x["status"] for x in r.json()["results"]] == ["added", "added"], r.text
    assert await _held(entry_id) == [a, b]
    assert await _stamp(entry_id) == (who, "alice")
    after = await RegistryEntry.find_one(RegistryEntry.entry_id == entry_id)
    assert after.revision == first[1], "the re-read wrote the entry again"


@pytest.mark.asyncio
async def test_a_single_synonym_change_keeps_naming_the_caller(client, auth_headers, caller):
    """The paths that planned one change per entry are unaffected: the one
    addition, or removal, that lands supplies the note."""
    headers, who = caller
    prefix = await _namespace(client, auth_headers)
    entry_id = await _entry(client, auth_headers, prefix)
    key = {"ext": uuid.uuid4().hex}

    _ok(await client.post(f"{BASE}/synonyms/add", headers=headers, json=[
        {**_add_item(entry_id, key), "created_by": "added by ann"},
    ]), "added")
    assert await _stamp(entry_id) == (who, "added by ann")

    _ok(await client.post(f"{BASE}/synonyms/remove", headers=headers, json=[
        {**_rm(entry_id, key), "updated_by": "cleanup"},
    ]), "removed")
    assert await _stamp(entry_id) == (who, "cleanup")
