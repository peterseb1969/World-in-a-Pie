"""Bulk term create and the Registry.

A bulk-created term is reported created only once its value synonym
(`ns:terminology:value`) is held by it in the Registry — the same rule as
the single-term door. A term the Registry refuses is taken back and is
that item's error; a Registry that does not answer fails the rest of the
request per item, while everything already created stays and is counted.
term_count moves atomically and never writes back a stale terminology.
"""

import uuid

import pytest
from httpx import AsyncClient

from def_store.models.audit_log import TermAuditLog
from def_store.models.term import Term
from def_store.services import terminology_service
from def_store.services.registry_client import RegistryError

TERMS = "/api/def-store/terminologies"


async def _terminology(client: AsyncClient, auth_headers: dict) -> tuple[str, str]:
    value = f"BULKREG_{uuid.uuid4().hex[:8].upper()}"
    resp = await client.post(
        TERMS, headers=auth_headers,
        json=[{"value": value, "label": value, "namespace": "wip"}],
    )
    return resp.json()["results"][0]["id"], value


async def _terminology_doc(client: AsyncClient, auth_headers: dict, tid: str) -> dict:
    return (await client.get(f"{TERMS}/{tid}", headers=auth_headers)).json()


@pytest.mark.asyncio
async def test_a_refused_value_synonym_takes_the_term_back(client: AsyncClient, auth_headers: dict):
    tid, tvalue = await _terminology(client, auth_headers)
    # Another term of the terminology already holds the value key "held"
    holder = await client.post(f"{TERMS}/{tid}/terms", headers=auth_headers, json=[{"value": "holder"}])
    holder_id = holder.json()["results"][0]["id"]
    await terminology_service.get_registry_client().register_auto_synonym(
        target_id=holder_id, namespace="wip", entity_type="terms",
        composite_key={"ns": "wip", "type": "term", "terminology": tvalue, "value": "held"},
    )

    resp = await client.post(
        f"{TERMS}/{tid}/terms", headers=auth_headers,
        json=[{"value": "held"}, {"value": "fresh"}],
    )
    assert resp.status_code == 200
    held, fresh = resp.json()["results"]
    assert held["status"] == "error" and held["error_code"] == "registry_error", held
    assert fresh["status"] == "created", fresh

    stored = {t.value for t in await Term.find({"terminology_id": tid}).to_list()}
    assert stored == {"holder", "fresh"}
    assert (await _terminology_doc(client, auth_headers, tid))["term_count"] == 2

    audited = {
        e.new_values.get("value")
        for e in await TermAuditLog.find({"terminology_id": tid, "action": "created"}).to_list()
        if e.term_id != tid  # the terminology's own creation entry
    }
    assert audited == {"holder", "fresh"}


@pytest.mark.asyncio
async def test_an_unanswered_synonym_claim_fails_the_rest_per_item(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    tid, _ = await _terminology(client, auth_headers)
    registry = terminology_service.get_registry_client()
    real = registry.register_auto_synonyms_per_item
    calls = {"n": 0}

    async def flaky(items, batch_size=100):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RegistryError("registry unreachable")
        return await real(items, batch_size)

    monkeypatch.setattr(registry, "register_auto_synonyms_per_item", flaky)
    resp = await client.post(
        f"{TERMS}/{tid}/terms?batch_size=2", headers=auth_headers,
        json=[{"value": f"t{i}"} for i in range(6)],
    )
    assert resp.status_code == 200, resp.text
    statuses = [(r["status"], r.get("error_code")) for r in resp.json()["results"]]
    assert statuses[:2] == [("created", None)] * 2
    assert statuses[2:] == [("error", "registry_error")] * 4
    assert resp.json()["failed"] == 4

    stored = sorted(t.value for t in await Term.find({"terminology_id": tid}).to_list())
    assert stored == ["t0", "t1"]
    assert (await _terminology_doc(client, auth_headers, tid))["term_count"] == 2


@pytest.mark.asyncio
async def test_an_unanswered_registration_fails_the_rest_per_item(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    tid, _ = await _terminology(client, auth_headers)
    registry = terminology_service.get_registry_client()
    real = registry.register_terms_bulk
    calls = {"n": 0}

    async def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RegistryError("registry unreachable")
        return await real(**kwargs)

    monkeypatch.setattr(registry, "register_terms_bulk", flaky)
    resp = await client.post(
        f"{TERMS}/{tid}/terms?batch_size=2", headers=auth_headers,
        json=[{"value": f"t{i}"} for i in range(6)],
    )
    assert resp.status_code == 200, resp.text
    results = resp.json()["results"]
    assert [r["status"] for r in results] == ["created", "created"] + ["error"] * 4
    assert calls["n"] == 2  # the batch after the failed one is not attempted
    assert (await _terminology_doc(client, auth_headers, tid))["term_count"] == 2


@pytest.mark.asyncio
async def test_term_count_does_not_overwrite_a_concurrent_edit(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    """The import never saves back the terminology it read at the start: an
    edit made while it runs survives, and the count includes both writers."""
    tid, _ = await _terminology(client, auth_headers)
    registry = terminology_service.get_registry_client()
    real = registry.register_terms_bulk
    done = {"edit": False}

    async def edit_meanwhile(**kwargs):
        if not done["edit"]:
            done["edit"] = True
            edit = await client.put(
                TERMS, headers=auth_headers,
                json=[{"terminology_id": tid, "label": "Edited meanwhile"}],
            )
            assert edit.json()["failed"] == 0
            single = await client.post(f"{TERMS}/{tid}/terms", headers=auth_headers, json=[{"value": "side"}])
            assert single.json()["results"][0]["status"] == "created"
        return await real(**kwargs)

    monkeypatch.setattr(registry, "register_terms_bulk", edit_meanwhile)
    resp = await client.post(
        f"{TERMS}/{tid}/terms", headers=auth_headers,
        json=[{"value": "a"}, {"value": "b"}],
    )
    assert resp.json()["succeeded"] == 2
    doc = await _terminology_doc(client, auth_headers, tid)
    assert doc["label"] == "Edited meanwhile"
    assert doc["term_count"] == 3
