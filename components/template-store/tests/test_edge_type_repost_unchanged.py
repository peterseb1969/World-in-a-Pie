"""An identical re-post of an edge type is `unchanged`, like any other template.

Template create is an upsert: a fully identical re-post (the idempotent
bootstrap re-run) returns `unchanged` and mints nothing. An edge type's
source_ref / target_ref constraint is derived from the template-level
endpoint declaration when the template is served, so whatever a caller sends
for those fields' own target_templates / include_subtypes is not persisted.
The re-post comparison used to run on the unstripped submission, so a seed
that repeats the endpoints on the fields (as the served shape shows them)
compared different from what was stored and minted a new version on every
run. The versioning path also persisted the submitted values.
"""

from __future__ import annotations

import copy

import pytest
from httpx import AsyncClient

from template_store.models.template import Template

API = "/api/template-store"


async def _post(client: AsyncClient, auth_headers: dict, payload: dict) -> dict:
    resp = await client.post(f"{API}/templates", headers=auth_headers, json=[payload])
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


def _entity(value: str) -> dict:
    return {
        "namespace": "wip", "value": value, "label": value.title(),
        "fields": [{"name": "id", "type": "string", "label": "Id", "mandatory": True}],
    }


def _edge(value: str) -> dict:
    """A real app seed's shape: the endpoint fields repeat the endpoints."""
    return {
        "namespace": "wip",
        "value": value,
        "label": "Recipe has step",
        "description": "Ordered step membership of a recipe.",
        "identity_fields": ["source_ref", "target_ref"],
        "fields": [
            {"name": "source_ref", "label": "Recipe", "type": "reference", "mandatory": True,
             "reference_type": "document", "target_templates": ["REPOST_RECIPE"],
             "include_subtypes": False},
            {"name": "target_ref", "label": "Step", "type": "reference", "mandatory": True,
             "reference_type": "document", "target_templates": ["REPOST_STEP"],
             "include_subtypes": False},
            {"name": "position", "label": "Position", "type": "integer", "mandatory": True,
             "validation": {"minimum": 1.0}},
        ],
        "usage": "relationship",
        "versioned": False,
        "source_templates": ["REPOST_RECIPE"],
        "target_templates": ["REPOST_STEP"],
    }


@pytest.fixture
async def endpoints(client: AsyncClient, auth_headers: dict) -> None:
    for value in ("REPOST_RECIPE", "REPOST_STEP"):
        assert (await _post(client, auth_headers, _entity(value)))["status"] in ("created", "unchanged")


async def _stored_endpoint_fields(value: str, version: int) -> dict:
    t = await Template.find_one({"namespace": "wip", "value": value, "version": version})
    assert t is not None
    return {
        f.name: (f.target_templates, f.include_subtypes)
        for f in t.fields if f.name in ("source_ref", "target_ref")
    }


@pytest.mark.asyncio
async def test_identical_edge_type_repost_is_unchanged(client, auth_headers, endpoints):
    first = await _post(client, auth_headers, _edge("REPOST_STEP_OF"))
    assert first["status"] == "created", first
    second = await _post(client, auth_headers, _edge("REPOST_STEP_OF"))
    assert second["status"] == "unchanged", second
    assert second["version"] == first["version"]
    assert await Template.find({"namespace": "wip", "value": "REPOST_STEP_OF"}).count() == 1


@pytest.mark.asyncio
async def test_repost_without_field_endpoints_is_unchanged(client, auth_headers, endpoints):
    """The two spellings of one declaration are one template."""
    first = await _post(client, auth_headers, _edge("REPOST_STEP_OF_BARE"))
    bare = copy.deepcopy(_edge("REPOST_STEP_OF_BARE"))
    for f in bare["fields"][:2]:
        del f["target_templates"], f["include_subtypes"]
    second = await _post(client, auth_headers, bare)
    assert second["status"] == "unchanged", second
    assert second["version"] == first["version"]


@pytest.mark.asyncio
async def test_real_change_versions_without_persisting_field_endpoints(client, auth_headers, endpoints):
    first = await _post(client, auth_headers, _edge("REPOST_STEP_OF_EVOLVED"))
    changed = _edge("REPOST_STEP_OF_EVOLVED")
    changed["label"] = "Recipe has ordered step"
    second = await _post(client, auth_headers, changed)
    assert second["status"] == "updated", second
    assert second["version"] == first["version"] + 1
    stored = await _stored_endpoint_fields("REPOST_STEP_OF_EVOLVED", second["version"])
    assert stored == {"source_ref": (None, None), "target_ref": (None, None)}


@pytest.mark.asyncio
async def test_repost_over_a_version_that_stored_field_endpoints_is_unchanged(
    client, auth_headers, endpoints
):
    """Versions minted before the update path stripped the projection carry it
    in storage; an identical re-post must not version against them either."""
    first = await _post(client, auth_headers, _edge("REPOST_STEP_OF_LEGACY"))
    stored = await Template.find_one({"namespace": "wip", "value": "REPOST_STEP_OF_LEGACY"})
    for f in stored.fields:
        if f.name in ("source_ref", "target_ref"):
            f.target_templates = ["0190d000-0000-7000-8000-000000000001"]
            f.include_subtypes = False
    await stored.save()
    second = await _post(client, auth_headers, _edge("REPOST_STEP_OF_LEGACY"))
    assert second["status"] == "unchanged", second
    assert second["version"] == first["version"]


@pytest.mark.asyncio
async def test_identical_edge_type_put_mints_no_version(client, auth_headers, endpoints):
    first = await _post(client, auth_headers, _edge("REPOST_STEP_OF_PUT"))
    edge = _edge("REPOST_STEP_OF_PUT")
    resp = await client.put(
        f"{API}/templates", headers=auth_headers,
        json=[{"template_id": first["id"], "fields": edge["fields"]}],
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()["results"][0]
    assert result["status"] == "unchanged", result
    assert result["is_new_version"] is False, result
    assert result["version"] == first["version"]
    assert await Template.find({"namespace": "wip", "value": "REPOST_STEP_OF_PUT"}).count() == 1
