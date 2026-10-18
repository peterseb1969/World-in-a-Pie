"""Merging terms: absorb one term into another of the same terminology.

After a merge the absorbed term's value and names resolve to the survivor
everywhere — validation, document writes (which resolve through def-store)
and the Registry (its id and `ns:terminology:value` key). The absorbed term
is deprecated with replaced_by = survivor. A merge that cannot complete
changes nothing. Merging term entries in the Registry alone is refused.
"""

import os
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from def_store.services.registry_client import RegistryError
from registry.main import app as registry_app

MERGE = "/api/def-store/terms/merge"


async def _vocab(client, headers, value="MERGE_PEOPLE", **flags):
    r = await client.post("/api/def-store/terminologies", headers=headers, json=[{
        "value": value, "label": value, "namespace": "wip", **flags,
    }])
    tid = r.json()["results"][0]["id"]
    r = await client.post(f"/api/def-store/terminologies/{tid}/terms", headers=headers, json=[
        {"value": "ADA", "aliases": ["Countess"]},
        {"value": "BABBAGE", "aliases": ["Charles"]},
        {"value": "TURING"},
    ])
    body = r.json()
    assert body["failed"] == 0, body
    return tid, {x["value"]: x["id"] for x in body["results"]}


async def _validate(client, headers, tid, value):
    r = await client.post("/api/def-store/validate", headers=headers, json={"terminology_id": tid, "value": value})
    return r.json()


async def _term(client, headers, term_id):
    return (await client.get(f"/api/def-store/terms/{term_id}", headers=headers)).json()


@pytest.mark.asyncio
async def test_merge_moves_value_and_names_and_deprecates_absorbed(client, auth_headers):
    tid, ids = await _vocab(client, auth_headers)
    r = await client.post(MERGE, headers=auth_headers, json=[{
        "survivor_term_id": ids["ADA"], "absorbed_term_id": ids["BABBAGE"], "reason": "same person",
    }])
    item = r.json()["results"][0]
    assert item["status"] == "updated", item
    assert item["details"]["names_added"] == ["BABBAGE", "Charles"]

    for spelling in ("BABBAGE", "babbage", "Charles"):
        res = await _validate(client, auth_headers, tid, spelling)
        assert res["matched_term"]["term_id"] == ids["ADA"], (spelling, res)
        assert res["matched_via"] == "alias"
    assert (await _validate(client, auth_headers, tid, "Countess"))["matched_term"]["term_id"] == ids["ADA"]

    absorbed = await _term(client, auth_headers, ids["BABBAGE"])
    assert absorbed["status"] == "deprecated"
    assert absorbed["replaced_by_term_id"] == ids["ADA"]
    assert absorbed["deprecated_reason"] == "same person"


@pytest.mark.asyncio
async def test_merge_redirects_the_absorbed_identity_in_the_registry(client, auth_headers):
    _tid, ids = await _vocab(client, auth_headers, "MERGE_IDS")
    await client.post(MERGE, headers=auth_headers, json=[{
        "survivor_term_id": ids["ADA"], "absorbed_term_id": ids["BABBAGE"],
    }])
    # the absorbed term's qualified value key now names the survivor
    by_key = await _term(client, auth_headers, "wip:MERGE_IDS:BABBAGE")
    assert by_key["term_id"] == ids["ADA"]


@pytest.mark.asyncio
async def test_registry_alone_refuses_to_merge_term_entries(client, auth_headers):
    _tid, ids = await _vocab(client, auth_headers, "MERGE_RAW")
    headers = {"X-API-Key": os.environ["MASTER_API_KEY"]}
    async with AsyncClient(transport=ASGITransport(app=registry_app), base_url="http://registry") as reg:
        r = await reg.post("/api/registry/synonyms/merge", headers=headers, json=[{
            "preferred_id": ids["ADA"], "deprecated_id": ids["BABBAGE"],
        }])
    item = r.json()["results"][0]
    assert item["status"] == "error"
    assert "/api/def-store/terms/merge" in item["error"]
    assert (await _term(client, auth_headers, ids["BABBAGE"]))["status"] == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["self", "deprecated", "other_terminology"])
async def test_invalid_merges_are_refused(client, auth_headers, case):
    _tid, ids = await _vocab(client, auth_headers, f"MERGE_BAD_{case.upper()}")
    survivor, absorbed = ids["ADA"], ids["BABBAGE"]
    if case == "self":
        absorbed = survivor
    elif case == "deprecated":
        await client.post("/api/def-store/terms/deprecate", headers=auth_headers, json=[{"term_id": absorbed, "reason": "x"}])
    else:
        _other, other_ids = await _vocab(client, auth_headers, f"MERGE_OTHER_{case.upper()}")
        absorbed = other_ids["BABBAGE"]
    r = await client.post(MERGE, headers=auth_headers, json=[{"survivor_term_id": survivor, "absorbed_term_id": absorbed}])
    assert r.json()["results"][0]["status"] == "error", r.json()


@pytest.mark.asyncio
async def test_registry_refusal_leaves_everything_unchanged(client, auth_headers):
    tid, ids = await _vocab(client, auth_headers, "MERGE_ATOMIC")
    from def_store.services import terminology_service

    registry = terminology_service.get_registry_client()
    with patch.object(registry, "merge_entries", AsyncMock(side_effect=RegistryError("refused"))):
        r = await client.post(MERGE, headers=auth_headers, json=[{
            "survivor_term_id": ids["ADA"], "absorbed_term_id": ids["BABBAGE"],
        }])
    item = r.json()["results"][0]
    assert item["status"] == "error" and "Merge not applied" in item["error"]
    assert (await _term(client, auth_headers, ids["BABBAGE"]))["status"] == "active"
    assert [n["value"] for n in (await _term(client, auth_headers, ids["ADA"]))["names"]] == ["Countess"]
    assert (await _validate(client, auth_headers, tid, "BABBAGE"))["matched_term"]["term_id"] == ids["BABBAGE"]


async def _share_charles(client, headers, ids):
    """TURING and BABBAGE both bear the name 'Charles'."""
    await client.put("/api/def-store/terms", headers=headers, json=[{"term_id": ids["TURING"], "aliases": ["Charles"]}])


@pytest.mark.asyncio
async def test_collision_with_a_third_term_is_reported(client, auth_headers):
    """Without unique_names the merge goes through; the survivor now shares
    'Charles' with TURING, and the result says so."""
    _tid, ids = await _vocab(client, auth_headers, "MERGE_LOOSE")
    await _share_charles(client, auth_headers, ids)
    r = await client.post(MERGE, headers=auth_headers, json=[{
        "survivor_term_id": ids["ADA"], "absorbed_term_id": ids["BABBAGE"],
    }])
    item = r.json()["results"][0]
    assert item["status"] == "updated", item
    assert any(c["name"] == "Charles" for c in item["details"]["name_collisions"]), item


@pytest.mark.asyncio
async def test_unique_names_collision_refuses_and_rolls_back(client, auth_headers):
    """A collision that predates switching unique_names on: the survivor
    cannot take 'Charles', so the merge is refused and BABBAGE stays active."""
    tid, ids = await _vocab(client, auth_headers, "MERGE_STRICT")
    await _share_charles(client, auth_headers, ids)
    r = await client.put("/api/def-store/terminologies", headers=auth_headers, json=[{"terminology_id": tid, "unique_names": True}])
    assert r.json()["results"][0]["status"] == "updated", r.json()
    r = await client.post(MERGE, headers=auth_headers, json=[{
        "survivor_term_id": ids["ADA"], "absorbed_term_id": ids["BABBAGE"],
    }])
    item = r.json()["results"][0]
    assert item["status"] == "error" and item["error_code"] == "name_collision", item
    assert (await _term(client, auth_headers, ids["BABBAGE"]))["status"] == "active"
    assert [n["value"] for n in (await _term(client, auth_headers, ids["ADA"]))["names"]] == ["Countess"]
