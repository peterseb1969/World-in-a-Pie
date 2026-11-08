"""/search/by-fields matches an entry only when one key holds every field.

The query had one branch per field, so {value: X, terminology: T} found
entries holding either value, even one field in the primary key and the
other in a synonym; the match check then could not find a key holding both
and labelled the entry a primary-key match. Values were also passed to the
query as they came, so a value like {"$ne": …} worked as an operator.
Provenance: CASE-1131.
"""

import uuid

import pytest

BASE = "/api/registry"


async def _register(client, headers, key: dict) -> str:
    r = await client.post(f"{BASE}/entries/register", headers=headers, json=[{
        "namespace": "default", "entity_type": "terms", "composite_key": key,
    }])
    result = r.json()["results"][0]
    assert result["status"] == "created", result
    return result["registry_id"]


async def _search(client, headers, criteria: dict) -> dict:
    r = await client.post(f"{BASE}/search/by-fields", headers=headers, json=[{
        "field_criteria": criteria, "restrict_to_namespaces": ["default"],
    }])
    assert r.status_code == 200, r.text
    return r.json()["results"][0]


@pytest.fixture
def tag() -> str:
    return uuid.uuid4().hex[:8]


@pytest.mark.asyncio
async def test_every_field_must_be_in_the_key(client, auth_headers, tag):
    x, t = f"X-{tag}", f"T-{tag}"
    await _register(client, auth_headers, {"value": x, "terminology": f"other-{tag}"})
    await _register(client, auth_headers, {"value": f"Y-{tag}", "terminology": t})
    both = await _register(client, auth_headers, {"value": x, "terminology": t})
    found = await _search(client, auth_headers, {"value": x, "terminology": t})
    assert [r["registry_id"] for r in found["results"]] == [both]
    assert found["results"][0]["matched_in"] == "primary"
    assert found["total_matches"] == 1


@pytest.mark.asyncio
async def test_fields_split_across_two_keys_are_no_match(client, auth_headers, tag):
    entry = await _register(client, auth_headers, {"value": f"P-{tag}", "terminology": f"A-{tag}"})
    r = await client.post(f"{BASE}/synonyms/add", headers=auth_headers, json=[{
        "target_id": entry, "synonym_composite_key": {"value": f"S-{tag}", "terminology": f"B-{tag}"},
    }])
    assert r.json()["results"][0]["status"] == "added", r.text
    split = await _search(client, auth_headers, {"value": f"P-{tag}", "terminology": f"B-{tag}"})
    assert split["results"] == []
    whole = await _search(client, auth_headers, {"value": f"S-{tag}", "terminology": f"B-{tag}"})
    assert [(r["registry_id"], r["matched_in"]) for r in whole["results"]] == [(entry, "synonym")]


@pytest.mark.asyncio
async def test_a_value_is_matched_as_a_value_not_an_operator(client, auth_headers, tag):
    await _register(client, auth_headers, {"value": f"V-{tag}", "probe": tag})
    found = await _search(client, auth_headers, {"value": {"$ne": "nothing"}, "probe": tag})
    assert found["results"] == []


@pytest.mark.asyncio
async def test_empty_criteria_and_dotted_names_are_refused(client, auth_headers):
    r = await client.post(f"{BASE}/search/by-fields", headers=auth_headers, json=[{"field_criteria": {}}])
    assert r.status_code == 422, r.text
    dotted = await _search(client, auth_headers, {"value.inner": "x"})
    assert dotted["status"] == "error", dotted
