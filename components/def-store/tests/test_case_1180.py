"""The platform's own vocabularies, relations and traversal.

- System vocabularies are read from `wip` only, and `_`-prefixed terminology
  values are reserved to `wip`: another namespace can neither create nor
  rename to one.
- A retired relation type stops validating at once.
- A reactivated relation's event carries the term values, like a created one.
- Traversal follows one level per query, caps the nodes it returns (and says
  so), reports `max_depth_reached` only when the depth limit cut something,
  and follows parent_term_id links through active terms only, both ways.
- `rollup=root` keeps a hit whose root is not of an included status.
- The bootstrap counts what was created and reports per-item failures.
- `skipped` counts as succeeded on POST /term-relations.
- The namespace-optional relation read refuses an identifier it cannot
  resolve instead of answering an empty 200.
"""

import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from def_store.models.term import Term
from def_store.models.terminology import Terminology
from def_store.services import ontology_service as onto_module

API = "/api/def-store"


async def _terminology(client, auth_headers, value, namespace="wip"):
    r = await client.post(f"{API}/terminologies", headers=auth_headers,
                          json=[{"value": value, "label": value, "namespace": namespace}])
    return r.json()["results"][0]


async def _terms(client, auth_headers, tid, *values, parent=None):
    body = [{"value": v, **({"parent_term_id": parent} if parent else {})} for v in values]
    r = await client.post(f"{API}/terminologies/{tid}/terms", headers=auth_headers, json=body)
    assert r.json()["succeeded"] == len(values), r.text
    return [x["id"] for x in r.json()["results"]]


async def _relate(client, auth_headers, pairs, rel="is_a"):
    r = await client.post(f"{API}/ontology/term-relations", headers=auth_headers, params={"namespace": "wip"},
                          json=[{"source_term_id": s, "target_term_id": t, "relation_type": rel} for s, t in pairs])
    return r.json()


# ---- item 1: reserved values, wip-only lookup ---------------------------------

@pytest.mark.asyncio
async def test_underscore_value_reserved_outside_wip(client: AsyncClient, auth_headers: dict):
    res = await _terminology(client, auth_headers, "_ONTOLOGY_RELATIONSHIP_TYPES", namespace="test-ns")

    assert res["status"] == "error", res
    assert res["error_code"] == "validation_failed"
    assert res["details"]["rule"] == "reserved_value"


@pytest.mark.asyncio
async def test_underscore_rename_reserved_outside_wip(client: AsyncClient, auth_headers: dict):
    tid = (await _terminology(client, auth_headers, "PLAIN", namespace="test-ns"))["id"]

    r = await client.put(f"{API}/terminologies", headers=auth_headers,
                         json=[{"terminology_id": tid, "value": "_NAME_TYPES"}])

    assert r.json()["results"][0]["details"]["rule"] == "reserved_value", r.text


@pytest.mark.asyncio
async def test_underscore_value_allowed_in_wip(client: AsyncClient, auth_headers: dict):
    res = await _terminology(client, auth_headers, "_PROBE_1180")
    assert res["status"] == "created", res


@pytest.mark.asyncio
async def test_bootstrap_seeds_wip_not_a_tenant_copy(client: AsyncClient, auth_headers: dict):
    """With wip's copy missing and a tenant terminology of the same value
    present (planted directly — the door now refuses it), the bootstrap
    creates wip's copy and leaves the tenant's alone."""
    from def_store.services.system_terminologies import ensure_system_terminologies

    wip_copy = await Terminology.find_one({"namespace": "wip", "value": "_TIME_UNITS"})
    await Term.find({"terminology_id": wip_copy.terminology_id}).delete()
    await wip_copy.delete()
    decoy = Terminology(namespace="aaa-decoy", terminology_id=str(uuid.uuid4()),
                        value="_TIME_UNITS", label="decoy")
    await decoy.insert()

    await ensure_system_terminologies()

    seeded = await Terminology.find_one({"namespace": "wip", "value": "_TIME_UNITS"})
    assert seeded is not None
    assert await Term.find({"terminology_id": seeded.terminology_id}).count() > 0
    assert await Term.find({"terminology_id": decoy.terminology_id}).count() == 0


# ---- item 2: a retired relation type stops validating --------------------------

@pytest.mark.asyncio
async def test_deprecated_relation_type_stops_validating(client: AsyncClient, auth_headers: dict):
    tid = (await _terminology(client, auth_headers, "REL_PROBE"))["id"]
    a, b = await _terms(client, auth_headers, tid, "a", "b")
    assert (await _relate(client, auth_headers, [(a, b)], rel="related_to"))["succeeded"] == 1
    types_tid = (await client.get(f"{API}/terminologies/by-value/_ONTOLOGY_RELATIONSHIP_TYPES",
                                  headers=auth_headers, params={"namespace": "wip"})).json()["terminology_id"]
    rel_term = (await client.get(f"{API}/terminologies/{types_tid}/terms", headers=auth_headers,
                                 params={"search": "part_of"})).json()["items"]
    rel_term = next(t for t in rel_term if t["value"] == "part_of")
    r = await client.post(f"{API}/terms/deprecate", headers=auth_headers,
                          json=[{"term_id": rel_term["term_id"], "reason": "retired"}])
    assert r.json()["succeeded"] == 1

    res = await _relate(client, auth_headers, [(a, b)], rel="part_of")

    assert res["results"][0]["status"] == "error", res


# ---- item 3: reactivation event carries values ---------------------------------

@pytest.mark.asyncio
async def test_reactivated_relation_event_carries_term_values(client: AsyncClient, auth_headers: dict, monkeypatch):
    tid = (await _terminology(client, auth_headers, "REACT_PROBE"))["id"]
    a, b = await _terms(client, auth_headers, tid, "alpha", "beta")
    await _relate(client, auth_headers, [(a, b)])
    r = await client.request("DELETE", f"{API}/ontology/term-relations", headers=auth_headers,
                             params={"namespace": "wip"},
                             json=[{"source_term_id": a, "target_term_id": b, "relation_type": "is_a"}])
    assert r.json()["succeeded"] == 1, r.text
    events: list[dict] = []

    async def spy(event_type, payload, changed_by=None):
        events.append(payload)

    monkeypatch.setattr(onto_module, "publish_term_relation_event", spy)

    res = await _relate(client, auth_headers, [(a, b)])

    assert res["succeeded"] == 1, res
    assert events and events[-1]["source_term_value"] == "alpha"
    assert events[-1]["target_term_value"] == "beta"


# ---- item 4 (+ 1182 ontology R7/R9): traversal ----------------------------------

async def _chain(client, auth_headers, name, length):
    tid = (await _terminology(client, auth_headers, name))["id"]
    ids = await _terms(client, auth_headers, tid, *[f"n{i}" for i in range(length)])
    await _relate(client, auth_headers, list(zip(ids[1:], ids[:-1], strict=False)))  # n1 is_a n0, …
    return ids


@pytest.mark.asyncio
async def test_traversal_node_cap_is_reported(client: AsyncClient, auth_headers: dict, monkeypatch):
    ids = await _chain(client, auth_headers, "CAP_PROBE", 6)
    monkeypatch.setattr(onto_module, "MAX_TRAVERSAL_NODES", 3)

    r = await client.get(f"{API}/ontology/terms/{ids[0]}/descendants", headers=auth_headers,
                         params={"namespace": "wip", "max_depth": 10})

    body = r.json()
    assert body["total"] == 3, body
    assert body["truncated"] is True


@pytest.mark.asyncio
async def test_max_depth_reached_only_when_something_was_cut(client: AsyncClient, auth_headers: dict):
    ids = await _chain(client, auth_headers, "DEPTH_PROBE", 4)  # depth 0..3

    exact = (await client.get(f"{API}/ontology/terms/{ids[0]}/descendants", headers=auth_headers,
                              params={"namespace": "wip", "max_depth": 3})).json()
    cut = (await client.get(f"{API}/ontology/terms/{ids[0]}/descendants", headers=auth_headers,
                            params={"namespace": "wip", "max_depth": 2})).json()

    assert exact["total"] == 3 and exact["max_depth_reached"] is False, exact
    assert exact["truncated"] is False
    assert cut["total"] == 2 and cut["max_depth_reached"] is True, cut


@pytest.mark.asyncio
async def test_parent_links_followed_through_active_terms_only_both_ways(client: AsyncClient, auth_headers: dict):
    tid = (await _terminology(client, auth_headers, "PARENT_PROBE"))["id"]
    (root,) = await _terms(client, auth_headers, tid, "root")
    (mid,) = await _terms(client, auth_headers, tid, "mid", parent=root)
    (leaf,) = await _terms(client, auth_headers, tid, "leaf", parent=mid)
    r = await client.post(f"{API}/terms/deprecate", headers=auth_headers,
                          json=[{"term_id": mid, "reason": "retired"}])
    assert r.json()["succeeded"] == 1

    up = (await client.get(f"{API}/ontology/terms/{leaf}/ancestors", headers=auth_headers,
                           params={"namespace": "wip"})).json()
    down = (await client.get(f"{API}/ontology/terms/{root}/descendants", headers=auth_headers,
                             params={"namespace": "wip"})).json()

    assert [n["term_id"] for n in up["nodes"]] == [], up
    assert [n["term_id"] for n in down["nodes"]] == [], down


# ---- item 5: rollup honours statuses --------------------------------------------

@pytest.mark.asyncio
async def test_rollup_keeps_hit_when_root_is_not_included(client: AsyncClient, auth_headers: dict):
    tid = (await _terminology(client, auth_headers, "ROLLUP_PROBE"))["id"]
    root, leaf = await _terms(client, auth_headers, tid, "oldroot", "leafy")
    await _relate(client, auth_headers, [(leaf, root)])
    r = await client.post(f"{API}/terms/deprecate", headers=auth_headers,
                          json=[{"term_id": root, "reason": "retired"}])
    assert r.json()["succeeded"] == 1

    r = await client.get(f"{API}/terms/search", headers=auth_headers,
                         params={"q": "leafy", "terminology": tid, "rollup": "root", "namespace": "wip"})

    items = r.json()["items"]
    assert [i["term"]["term_id"] for i in items] == [leaf], r.text


# ---- item 6: bootstrap counts real results --------------------------------------

@pytest.mark.asyncio
async def test_bootstrap_counts_created_and_reports_item_errors(client: AsyncClient, auth_headers: dict):
    from def_store.models.api_models import BulkResultItem
    from def_store.services import system_terminologies as st

    await Term.find({"namespace": "wip", "terminology_value": "_TIME_UNITS"}).delete()

    async def fake_bulk(terminology_id, terms, **kw):
        return [BulkResultItem(index=0, status="created", id="x"),
                *[BulkResultItem(index=i, status="error", error="boom", error_code="registry_error")
                  for i in range(1, len(terms))]]

    with patch.object(st.TerminologyService, "create_terms_bulk", side_effect=fake_bulk):
        summary = await st.ensure_system_terminologies()

    assert summary["terms_created"] == 1, summary
    assert any("_TIME_UNITS" in e for e in summary["errors"]), summary


# ---- item 7: skipped counts as succeeded ----------------------------------------

@pytest.mark.asyncio
async def test_duplicate_relation_counts_as_succeeded(client: AsyncClient, auth_headers: dict):
    tid = (await _terminology(client, auth_headers, "DUP_PROBE"))["id"]
    a, b = await _terms(client, auth_headers, tid, "a", "b")
    await _relate(client, auth_headers, [(a, b)])

    res = await _relate(client, auth_headers, [(a, b)])

    assert res["results"][0]["status"] == "skipped"
    assert (res["succeeded"], res["failed"]) == (1, 0), res


# ---- item 8: the namespace-optional read refuses what it cannot resolve ---------

@pytest.mark.asyncio
async def test_relation_list_refuses_unresolvable_identifier(client: AsyncClient, auth_headers: dict):
    r = await client.get(f"{API}/ontology/term-relations", headers=auth_headers,
                         params={"term_id": "no-such-term-1180"})

    assert r.status_code in (404, 422), r.text
