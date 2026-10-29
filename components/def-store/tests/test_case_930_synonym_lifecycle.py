"""CASE-930 — a term's or terminology's retirement and revival reach the Registry.

A deprecated or soft-deleted term, and a soft-deleted terminology (with its
terms), is retired: its caller-added synonyms are released — they stay
recorded as inactive and neither block another entity from claiming the key
nor resolve. The value keys (primary key and {ns, type, …} auto-synonym)
are never released, so a retired term or terminology stays addressable by
value. Restoring a terminology claims its own and its restored terms'
synonyms again. A merge's deprecation is not a retirement: the absorbed
term's synonyms move to the survivor and stay live.
"""

import os

import pytest
from httpx import ASGITransport, AsyncClient

from registry.main import app as registry_app
from registry.models.entry import RegistryEntry

VENDOR = {"vendor_code": "V-930"}


async def _vocab(client, headers, value: str) -> tuple[str, dict[str, str]]:
    r = await client.post("/api/def-store/terminologies", headers=headers, json=[{
        "value": value, "label": value, "namespace": "wip",
    }])
    tid = r.json()["results"][0]["id"]
    r = await client.post(f"/api/def-store/terminologies/{tid}/terms", headers=headers, json=[
        {"value": "ADA"}, {"value": "BABBAGE"},
    ])
    body = r.json()
    assert body["failed"] == 0, body
    return tid, {x["value"]: x["id"] for x in body["results"]}


async def _add_vendor_synonym(entry_id: str, entity_type: str, key: dict = VENDOR) -> None:
    async with AsyncClient(transport=ASGITransport(app=registry_app), base_url="http://registry") as r:
        resp = await r.post(
            "/api/registry/synonyms/add",
            headers={"X-API-Key": os.environ["MASTER_API_KEY"]},
            json=[{
                "target_id": entry_id, "synonym_namespace": "wip",
                "synonym_entity_type": entity_type, "synonym_composite_key": key,
            }],
        )
    assert resp.json()["results"][0]["status"] == "added", resp.text


async def _vendor_state(entry_id: str) -> str:
    entry = await RegistryEntry.find_one(RegistryEntry.entry_id == entry_id)
    assert entry is not None
    [state] = [s.status for s in entry.synonyms if "vendor_code" in s.composite_key]
    return state


async def _auto_states(entry_id: str) -> set[str]:
    entry = await RegistryEntry.find_one(RegistryEntry.entry_id == entry_id)
    assert entry is not None
    return {s.status for s in entry.synonyms if "type" in s.composite_key}


@pytest.mark.asyncio
async def test_deprecating_a_term_releases_its_synonyms(client, auth_headers):
    _, ids = await _vocab(client, auth_headers, "LIFE930_DEP")
    await _add_vendor_synonym(ids["ADA"], "terms")
    r = await client.post("/api/def-store/terms/deprecate", headers=auth_headers, json=[{
        "term_id": ids["ADA"], "reason": "retired",
    }])
    assert r.json()["succeeded"] == 1, r.text
    assert await _vendor_state(ids["ADA"]) == "inactive"
    assert await _auto_states(ids["ADA"]) == {"active"}


@pytest.mark.asyncio
async def test_soft_deleting_a_term_releases_its_synonyms(client, auth_headers):
    _, ids = await _vocab(client, auth_headers, "LIFE930_DEL")
    await _add_vendor_synonym(ids["ADA"], "terms")
    r = await client.request("DELETE", "/api/def-store/terms", headers=auth_headers, json=[{"id": ids["ADA"]}])
    assert r.json()["succeeded"] == 1, r.text
    assert await _vendor_state(ids["ADA"]) == "inactive"


@pytest.mark.asyncio
async def test_terminology_delete_and_restore_cascade_to_terms(client, auth_headers):
    tid, ids = await _vocab(client, auth_headers, "LIFE930_VOCAB")
    await _add_vendor_synonym(tid, "terminologies", {"vendor_code": "V-930-vocab"})
    await _add_vendor_synonym(ids["ADA"], "terms")

    r = await client.request("DELETE", "/api/def-store/terminologies", headers=auth_headers, json=[{"id": tid}])
    assert r.json()["succeeded"] == 1, r.text
    assert await _vendor_state(tid) == "inactive"
    assert await _vendor_state(ids["ADA"]) == "inactive"

    r = await client.post(f"/api/def-store/terminologies/{tid}/restore", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert await _vendor_state(tid) == "active"
    assert await _vendor_state(ids["ADA"]) == "active"


@pytest.mark.asyncio
async def test_a_merge_keeps_the_absorbed_terms_synonyms_live(client, auth_headers):
    _, ids = await _vocab(client, auth_headers, "LIFE930_MERGE")
    await _add_vendor_synonym(ids["BABBAGE"], "terms")
    r = await client.post("/api/def-store/terms/merge", headers=auth_headers, json=[{
        "survivor_term_id": ids["ADA"], "absorbed_term_id": ids["BABBAGE"],
    }])
    assert r.json()["results"][0]["status"] == "updated", r.text
    # the synonym moved to the survivor, still claimed and resolving
    assert await _vendor_state(ids["ADA"]) == "active"
    async with AsyncClient(transport=ASGITransport(app=registry_app), base_url="http://registry") as reg:
        resp = await reg.post(
            "/api/registry/entries/lookup/by-key",
            headers={"X-API-Key": os.environ["MASTER_API_KEY"]},
            json=[{"namespace": "wip", "entity_type": "terms", "composite_key": VENDOR}],
        )
    assert resp.json()["results"][0]["entry_id"] == ids["ADA"]


@pytest.mark.asyncio
async def test_reconcile_releases_terms_retired_before(client, auth_headers):
    from def_store.models.term import Term

    _, ids = await _vocab(client, auth_headers, "LIFE930_RECON")
    await _add_vendor_synonym(ids["ADA"], "terms")
    # deprecated without the Registry hearing of it (as before this change)
    term = await Term.find_one({"term_id": ids["ADA"]})
    term.status = "deprecated"
    await term.save()

    async def _reconcile(dry_run: bool) -> dict:
        resp = await client.post(
            "/api/def-store/maintenance/reconcile-synonym-lifecycle",
            headers=auth_headers, json={"namespace": "wip", "dry_run": dry_run},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    dry = await _reconcile(True)
    released = [i for i in dry["items"] if i["status"] == "released"]
    assert released == [{"entry_id": ids["ADA"], "status": "released", "keys": [VENDOR], "detail": None}]
    assert await _vendor_state(ids["ADA"]) == "active"

    await _reconcile(False)
    assert await _vendor_state(ids["ADA"]) == "inactive"
    assert not [i for i in (await _reconcile(False))["items"] if i["status"] == "released"]
