"""CASE-975 — a value resolves to every entry it names, never an arbitrary one.

/lookup/by-id falls back to one find_one over search_values: when a value is
carried by several entries (the same spelling under two templates, or a
template's value, which every one of its documents carries in its
auto-synonym), it answered with whichever came first. /lookup/candidates
returns them all, each with the key that carried the value, so the caller
can narrow or refuse instead.
"""

import pytest
from httpx import AsyncClient

NS = "default"


async def _post(client, auth_headers, path, body) -> dict:
    r = await client.post(f"/api/registry{path}", json=body, headers=auth_headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _doc(client, auth_headers, template: str, title: str) -> str:
    data = await _post(client, auth_headers, "/entries/register", [{
        "namespace": NS, "entity_type": "documents",
        "composite_key": {"ns": NS, "template_id": f"TPL-{template}"},
        "identity_values": {"title": title},
        "synonym_values": {"@template": template, "title": title},
    }])
    result = data["results"][0]
    assert result["status"] == "created", result
    return result["registry_id"]


async def _candidates(client, auth_headers, value: str, **filters) -> dict:
    data = await _post(client, auth_headers, "/entries/lookup/candidates", [{
        "value": value, "namespace": NS, "entity_type": "documents", **filters,
    }])
    return data["results"][0]


@pytest.mark.asyncio
async def test_a_value_carried_by_two_entries_returns_both(client: AsyncClient, auth_headers: dict):
    chapter = await _doc(client, auth_headers, "CHAPTER", "Low Water")
    scene = await _doc(client, auth_headers, "SCENE", "Low Water")
    found = await _candidates(client, auth_headers, "Low Water")
    assert found["status"] == "found"
    assert found["total"] == 2 and found["truncated"] is False
    assert [c["entry_id"] for c in found["candidates"]] == [chapter, scene]
    assert [c["matched_composite_key"] for c in found["candidates"]] == [
        {"@template": "CHAPTER", "title": "Low Water"},
        {"@template": "SCENE", "title": "Low Water"},
    ]
    assert {c["matched_via"] for c in found["candidates"]} == {"synonym"}


@pytest.mark.asyncio
async def test_a_single_match_is_one_candidate(client: AsyncClient, auth_headers: dict):
    only = await _doc(client, auth_headers, "CHAPTER", "Spring Tide")
    found = await _candidates(client, auth_headers, "Spring Tide")
    assert found["total"] == 1
    assert found["candidates"][0]["entry_id"] == only


@pytest.mark.asyncio
async def test_an_entry_id_is_the_only_candidate(client: AsyncClient, auth_headers: dict):
    doc = await _doc(client, auth_headers, "CHAPTER", "Neap")
    found = await _candidates(client, auth_headers, doc)
    assert found["total"] == 1
    assert found["candidates"] == [{
        "entry_id": doc, "namespace": NS, "entity_type": "documents",
        "matched_via": "entry_id", "matched_composite_key": None,
    }]


@pytest.mark.asyncio
async def test_no_match_is_not_found(client: AsyncClient, auth_headers: dict):
    found = await _candidates(client, auth_headers, "no such value")
    assert found["status"] == "not_found"
    assert found["total"] == 0 and found["candidates"] == []


@pytest.mark.asyncio
async def test_truncation_is_reported(client: AsyncClient, auth_headers: dict):
    for template in ("A", "B", "C"):
        await _doc(client, auth_headers, template, "Ebb")
    found = await _candidates(client, auth_headers, "Ebb", limit=2)
    assert found["total"] == 3
    assert found["truncated"] is True
    assert len(found["candidates"]) == 2


@pytest.mark.asyncio
async def test_a_retired_entitys_spelling_is_not_a_candidate(client: AsyncClient, auth_headers: dict):
    old = await _doc(client, auth_headers, "CHAPTER", "Slack")
    await _post(client, auth_headers, "/entries/deactivate", [{"entry_id": old}])
    assert (await _candidates(client, auth_headers, "Slack"))["status"] == "not_found"


@pytest.mark.asyncio
async def test_an_unknown_entity_type_is_an_error(client: AsyncClient, auth_headers: dict):
    data = await _post(client, auth_headers, "/entries/lookup/candidates", [{
        "value": "x", "entity_type": "document",
    }])
    assert data["results"][0]["status"] == "error"


@pytest.mark.asyncio
async def test_only_readable_namespaces_count(client: AsyncClient, auth_headers: dict):
    import uuid

    shared = f"shared-{uuid.uuid4().hex[:8]}"
    ids = {}
    for ns in ("vendor1", "vendor2"):
        data = await _post(client, auth_headers, "/entries/register", [{
            "namespace": ns, "entity_type": "documents", "composite_key": {"value": shared},
        }])
        ids[ns] = data["results"][0]["registry_id"]
    resp = await client.post("/api/registry/api-keys", headers=auth_headers, json={
        "name": f"case975-{uuid.uuid4().hex[:8]}", "groups": [], "namespaces": ["vendor1"],
    })
    assert resp.status_code == 201, resp.text
    reader = {"X-API-Key": resp.json()["plaintext_key"]}

    unscoped = await _post(client, reader, "/entries/lookup/candidates", [{"value": shared}])
    assert [c["entry_id"] for c in unscoped["results"][0]["candidates"]] == [ids["vendor1"]]
    assert unscoped["results"][0]["total"] == 1
    foreign = await _post(client, reader, "/entries/lookup/candidates", [
        {"value": shared, "namespace": "vendor2"},
        {"value": ids["vendor2"]},
    ])
    assert [r["status"] for r in foreign["results"]] == ["not_found", "not_found"]
    admin = await _post(client, auth_headers, "/entries/lookup/candidates", [{"value": shared}])
    assert admin["results"][0]["total"] == 2
