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


async def _search(client, headers, criteria: dict, status: str = "ok") -> dict:
    """One search item's result, asserting its per-item status: an item that
    failed answers 200 with no results too, so an empty result proves
    nothing unless the status says the search ran."""
    r = await client.post(f"{BASE}/search/by-fields", headers=headers, json=[{
        "field_criteria": criteria, "restrict_to_namespaces": ["default"],
    }])
    assert r.status_code == 200, r.text
    result = r.json()["results"][0]
    assert result["status"] == status, result
    return result


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
@pytest.mark.parametrize("value", [{"$ne": "nothing"}, None, ["a", "b"]])
async def test_a_criterion_value_is_a_scalar(client, auth_headers, value):
    """An operator-shaped object never reaches the query, and a null or a list
    (which MongoDB would match against a missing field or an array element,
    while the match check would not) is refused rather than half-matched."""
    r = await client.post(f"{BASE}/search/by-fields", headers=auth_headers, json=[{
        "field_criteria": {"value": value},
    }])
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["restrict_to_namespaces", "restrict_to_entity_types"])
async def test_an_empty_restriction_is_refused_not_read_as_all(client, auth_headers, field):
    for route, item in (
        ("by-fields", {"field_criteria": {"value": "x"}}),
        ("by-term", {"term": "x"}),
    ):
        r = await client.post(f"{BASE}/search/{route}", headers=auth_headers, json=[{**item, field: []}])
        assert r.status_code == 422, (route, r.text)


@pytest.mark.asyncio
async def test_empty_criteria_and_dotted_names_are_refused(client, auth_headers):
    r = await client.post(f"{BASE}/search/by-fields", headers=auth_headers, json=[{"field_criteria": {}}])
    assert r.status_code == 422, r.text
    await _search(client, auth_headers, {"value.inner": "x"}, status="error")
