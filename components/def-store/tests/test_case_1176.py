"""A terminology's retirement and restore leave each term's own lifecycle alone.

Soft-deleting a terminology deactivates its ACTIVE terms; a deprecated term
stays deprecated (reason and replacement kept) and keeps its released
synonyms released. Restoring brings back the inactive terms — the ones
deactivated with the terminology and, as documented, terms soft-deleted on
their own before it — and never a deprecated one, so a deprecated value does
not start matching new documents again. A retired terminology takes no new
terms. term_count (active terms) is kept true across the delete, and the
derived system-vocabulary caches are dropped whenever a delete or restore
changes what they hold.
"""

import pytest
from httpx import AsyncClient

from def_store.services.terminology_service import TerminologyService

API = "/api/def-store"


async def _terminology(client, auth_headers, value):
    response = await client.post(
        f"{API}/terminologies", headers=auth_headers,
        json=[{"value": value, "label": value.title(), "namespace": "wip"}],
    )
    data = response.json()
    assert data["succeeded"] == 1, data
    return data["results"][0]["id"]


async def _terms(client, auth_headers, terminology_id, *values):
    response = await client.post(
        f"{API}/terminologies/{terminology_id}/terms", headers=auth_headers,
        json=[{"value": v} for v in values],
    )
    data = response.json()
    assert data["succeeded"] == len(values), data
    return [r["id"] for r in data["results"]]


async def _term(client, auth_headers, term_id):
    response = await client.get(f"{API}/terms/{term_id}", headers=auth_headers)
    assert response.status_code == 200
    return response.json()


async def _retire_and_restore(client, auth_headers, terminology_id):
    response = await client.request(
        "DELETE", f"{API}/terminologies", headers=auth_headers,
        json=[{"id": terminology_id, "force": True}],
    )
    assert response.json()["succeeded"] == 1, response.json()
    response = await client.post(
        f"{API}/terminologies/{terminology_id}/restore", headers=auth_headers,
        params={"restore_terms": True},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _validate(client, auth_headers, terminology_id, value):
    response = await client.post(
        f"{API}/validate", headers=auth_headers,
        json={"terminology_id": terminology_id, "value": value},
    )
    assert response.status_code == 200, response.text
    return response.json()["valid"]


@pytest.fixture
def registry_calls(monkeypatch):
    """Every retire/revive the def-store tells the Registry, in order."""
    calls: list[tuple[bool, list[str]]] = []
    real = TerminologyService._record_lifecycle

    async def spy(entry_ids, *, retired, actor):
        calls.append((retired, list(entry_ids)))
        await real(entry_ids, retired=retired, actor=actor)

    monkeypatch.setattr(TerminologyService, "_record_lifecycle", staticmethod(spy))
    return calls


@pytest.mark.asyncio
async def test_deprecated_term_survives_retire_and_restore(
    client: AsyncClient, auth_headers: dict, registry_calls
):
    tid = await _terminology(client, auth_headers, "probe_1176_dep")
    old, new = await _terms(client, auth_headers, tid, "old", "new")
    response = await client.post(
        f"{API}/terms/deprecate", headers=auth_headers,
        json=[{"term_id": old, "reason": "renamed", "replaced_by_term_id": new}],
    )
    assert response.json()["succeeded"] == 1, response.json()
    registry_calls.clear()

    restored = await _retire_and_restore(client, auth_headers, tid)

    term = await _term(client, auth_headers, old)
    assert term["status"] == "deprecated"
    assert term["deprecated_reason"] == "renamed"
    assert term["replaced_by_term_id"] == new
    assert (await _term(client, auth_headers, new))["status"] == "active"
    assert restored["term_count"] == 1
    # The deprecated value still does not match a new write
    assert not await _validate(client, auth_headers, tid, "old")
    assert await _validate(client, auth_headers, tid, "new")
    # Neither the retirement nor the restore touched the deprecated term's
    # Registry entry: its synonyms were released at deprecation and stay so.
    for _retired, ids in registry_calls:
        assert old not in ids, registry_calls


@pytest.mark.asyncio
async def test_individually_deleted_term_still_comes_back(
    client: AsyncClient, auth_headers: dict
):
    """Restore keeps reviving every inactive term — documented behaviour,
    the way to bring back a term soft-deleted on its own."""
    tid = await _terminology(client, auth_headers, "probe_1176_del")
    gone, kept = await _terms(client, auth_headers, tid, "gone", "kept")
    response = await client.request(
        "DELETE", f"{API}/terms", headers=auth_headers, json=[{"id": gone}],
    )
    assert response.json()["succeeded"] == 1

    restored = await _retire_and_restore(client, auth_headers, tid)

    assert (await _term(client, auth_headers, gone))["status"] == "active"
    assert (await _term(client, auth_headers, kept))["status"] == "active"
    assert restored["term_count"] == 2


@pytest.mark.asyncio
async def test_soft_delete_zeroes_term_count(client: AsyncClient, auth_headers: dict):
    tid = await _terminology(client, auth_headers, "probe_1176_count")
    await _terms(client, auth_headers, tid, "a", "b")

    response = await client.request(
        "DELETE", f"{API}/terminologies", headers=auth_headers,
        json=[{"id": tid, "force": True}],
    )
    assert response.json()["succeeded"] == 1

    detail = (await client.get(f"{API}/terminologies/{tid}", headers=auth_headers)).json()
    assert detail["status"] == "inactive"
    assert detail["term_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("bulk", [False, True])
async def test_retired_terminology_takes_no_new_terms(
    client: AsyncClient, auth_headers: dict, bulk
):
    tid = await _terminology(client, auth_headers, f"probe_1176_new_{bulk}")
    response = await client.request(
        "DELETE", f"{API}/terminologies", headers=auth_headers,
        json=[{"id": tid, "force": True}],
    )
    assert response.json()["succeeded"] == 1

    body = [{"value": "late"}, {"value": "later"}] if bulk else [{"value": "late"}]
    response = await client.post(
        f"{API}/terminologies/{tid}/terms", headers=auth_headers, json=body,
    )

    data = response.json()
    assert data["succeeded"] == 0, data
    for r in data["results"]:
        assert r["error_code"] == "invalid_state", r
        assert r["details"]["rule"] == "not_active"
    listing = (await client.get(
        f"{API}/terminologies/{tid}/terms", headers=auth_headers, params={"status": "all"},
    )).json()
    assert listing["total"] == 0, listing


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["delete_terminology", "restore_terminology", "delete_term"])
async def test_system_vocabulary_caches_dropped(
    client: AsyncClient, auth_headers: dict, monkeypatch, operation
):
    """The name-type and relation-type caches live for the process; a delete
    or restore that changes their vocabulary must drop them."""
    from def_store.services.ontology_service import OntologyService
    from def_store.services.term_names import NameTypeService

    dropped: list[str] = []
    monkeypatch.setattr(
        NameTypeService, "invalidate_name_type_cache",
        classmethod(lambda cls: dropped.append("name_types")),
    )
    monkeypatch.setattr(
        OntologyService, "invalidate_relation_type_cache",
        classmethod(lambda cls: dropped.append("relation_types")),
    )
    listing = (await client.get(
        f"{API}/terminologies", headers=auth_headers, params={"value": "_NAME_TYPES"},
    )).json()
    name_types = next(t for t in listing["items"] if t["value"] == "_NAME_TYPES")
    tid = name_types["terminology_id"]

    if operation == "delete_term":
        term_ids = (await client.get(
            f"{API}/terminologies/{tid}/terms", headers=auth_headers,
        )).json()["items"]
        dropped.clear()
        await TerminologyService.delete_term(term_ids[0]["term_id"])
    elif operation == "delete_terminology":
        dropped.clear()
        await TerminologyService.delete_terminology(tid)
    else:
        await TerminologyService.delete_terminology(tid)
        dropped.clear()
        await TerminologyService.restore_terminology(tid)

    assert "name_types" in dropped, (operation, dropped)
