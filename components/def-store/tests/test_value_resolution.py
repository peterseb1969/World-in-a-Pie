"""def-store is the single resolver of submitted values to terms.

Document writes resolve term fields through `POST /validate/bulk`, so this
endpoint carries the whole rule: only ACTIVE terms match, a canonical value
beats another term's name, case follows the terminology's `case_sensitive`,
and a batch costs one indexed query per terminology — not one full scan of
the terminology per value.
"""

from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import AsyncClient

from def_store.models.term import Term


async def _terminology(client, headers, value, *, case_sensitive=False, mutable=False):
    resp = await client.post("/api/def-store/terminologies", headers=headers, json=[{
        "value": value, "label": value, "namespace": "wip",
        "case_sensitive": case_sensitive, "mutable": mutable,
    }])
    return resp.json()["results"][0]["id"]


async def _terms(client, headers, tid, terms):
    resp = await client.post(f"/api/def-store/terminologies/{tid}/terms", headers=headers, json=terms)
    body = resp.json()
    assert body["failed"] == 0, body
    return {r["value"]: r["id"] for r in body["results"]}


async def _bulk(client, headers, items):
    resp = await client.post("/api/def-store/validate/bulk", headers=headers, json={"items": items})
    assert resp.status_code == 200, resp.text
    return resp.json()["results"]


@pytest_asyncio.fixture
async def people(client: AsyncClient, auth_headers: dict):
    """Case-insensitive mutable vocabulary: ADA, LOVELACE, BABBAGE."""
    tid = await _terminology(client, auth_headers, "RES_PEOPLE", mutable=True)
    ids = await _terms(client, auth_headers, tid, [
        {"value": "ADA"}, {"value": "LOVELACE"}, {"value": "BABBAGE"},
    ])
    return {"tid": tid, "ids": ids}


@pytest.mark.asyncio
async def test_deprecated_term_does_not_match_and_alias_owner_wins(client, auth_headers, people):
    """Deprecate BABBAGE (replaced by ADA) and give ADA the name BABBAGE:
    the value now resolves to ADA via its name — a deprecated term never
    matches a new write."""
    ids = people["ids"]
    r = await client.post("/api/def-store/terms/deprecate", headers=auth_headers, json=[{
        "term_id": ids["BABBAGE"], "reason": "merged", "replaced_by_term_id": ids["ADA"],
    }])
    assert r.json()["results"][0]["status"] == "updated"
    r = await client.put("/api/def-store/terms", headers=auth_headers, json=[
        {"term_id": ids["ADA"], "aliases": ["BABBAGE"]},
    ])
    assert r.json()["results"][0]["status"] == "updated"

    (res,) = await _bulk(client, auth_headers, [{"terminology_id": people["tid"], "value": "babbage"}])
    assert res["valid"] is True
    assert res["matched_term"]["term_id"] == ids["ADA"]
    assert res["matched_via"] == "alias"


@pytest.mark.asyncio
async def test_deprecated_term_without_successor_name_is_invalid(client, auth_headers, people):
    ids = people["ids"]
    await client.post("/api/def-store/terms/deprecate", headers=auth_headers, json=[{
        "term_id": ids["BABBAGE"], "reason": "retired",
    }])
    (res,) = await _bulk(client, auth_headers, [{"terminology_id": people["tid"], "value": "BABBAGE"}])
    assert res["valid"] is False
    assert res["matched_term"] is None


@pytest.mark.asyncio
async def test_bulk_resolves_mixed_items_in_order(client, auth_headers, people):
    """Values, case variants, names, misses and two terminologies in one
    batch — results come back in submission order."""
    codes = await _terminology(client, auth_headers, "RES_CODES", case_sensitive=True)
    code_ids = await _terms(client, auth_headers, codes, [{"value": "Ab"}, {"value": "aB"}])
    await client.put("/api/def-store/terms", headers=auth_headers, json=[
        {"term_id": people["ids"]["ADA"], "aliases": ["Countess"]},
    ])
    tid = people["tid"]
    results = await _bulk(client, auth_headers, [
        {"terminology_id": tid, "value": "lovelace"},       # case variant of a value
        {"terminology_id": codes, "value": "aB"},           # case-sensitive exact
        {"terminology_id": tid, "value": "COUNTESS"},       # case variant of a name
        {"terminology_id": codes, "value": "AB"},           # case-sensitive miss
        {"terminology_id": tid, "value": "BAB"},            # miss with a prefix suggestion
    ])
    assert [r["valid"] for r in results] == [True, True, True, False, False]
    assert results[0]["matched_term"]["term_id"] == people["ids"]["LOVELACE"]
    assert results[0]["matched_via"] == "value"
    assert results[1]["matched_term"]["term_id"] == code_ids["aB"]
    assert results[2]["matched_term"]["term_id"] == people["ids"]["ADA"]
    assert results[2]["matched_via"] == "alias"
    assert results[4]["suggestion"]["value"] == "BABBAGE"


@pytest.mark.asyncio
async def test_value_beats_another_terms_name(client, auth_headers, people):
    await client.put("/api/def-store/terms", headers=auth_headers, json=[
        {"term_id": people["ids"]["ADA"], "aliases": ["lovelace"]},
    ])
    (res,) = await _bulk(client, auth_headers, [{"terminology_id": people["tid"], "value": "Lovelace"}])
    assert res["matched_term"]["term_id"] == people["ids"]["LOVELACE"]
    assert res["matched_via"] == "value"


@pytest.mark.asyncio
async def test_batch_costs_one_term_query_per_terminology(client, auth_headers, people):
    """Fifty values against one terminology load candidate terms once, not
    once per value (the old resolver scanned every active term per value)."""
    calls = []
    real_find = Term.find

    def counting_find(*args, **kwargs):
        calls.append(args)
        return real_find(*args, **kwargs)

    items = [{"terminology_id": people["tid"], "value": v} for v in ["ADA", "lovelace", "Babbage"] * 17][:50]
    with patch.object(Term, "find", side_effect=counting_find):
        results = await _bulk(client, auth_headers, items)
    assert all(r["valid"] for r in results)
    assert len(calls) == 1, calls


@pytest.mark.asyncio
async def test_case_insensitive_resolution_uses_the_collation_indexes(client, auth_headers, people):
    """The candidate query must be an index scan on both $or branches (value
    and names.value) — a collection scan would bring back the per-batch cost
    of reading the whole vocabulary."""
    from def_store.models.term import CASE_INSENSITIVE_COLLATION

    query = {
        "namespace": "wip", "terminology_id": people["tid"], "status": "active",
        "$or": [{"value": {"$in": ["ada"]}}, {"names.value": {"$in": ["ada"]}}],
    }
    plan = await Term.get_motor_collection().find(query, collation=CASE_INSENSITIVE_COLLATION).explain()
    text = str(plan["queryPlanner"]["winningPlan"])
    assert "COLLSCAN" not in text, text
    assert "ns_terminology_value_ci_idx" in text and "ns_terminology_name_values_ci_idx" in text, text
