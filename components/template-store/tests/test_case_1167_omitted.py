"""A re-post that omits a property keeps the existing value.

The create request defaults fields, identity_fields, header_fields, rules and
the endpoint lists to []. The upsert (POST /templates on an existing value)
forwarded those defaults as if they had been sent, so a re-post meant to change
a label minted an active version with no fields, wiped the rules and header
projection, or was refused for "changing" identity or endpoints to [].
An omitted property now means "keep the existing value", as it already did for
usage and versioned (model_fields_set tells omitted from sent).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from template_store.models.template import Template

API = "/api/template-store/templates"


async def _post(client, headers, payload) -> dict:
    resp = await client.post(API, headers=headers, json=[payload])
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


def _full(value: str) -> dict:
    return {
        "namespace": "wip", "value": value, "label": "First",
        "identity_fields": ["code"], "header_fields": ["code"],
        "fields": [{"name": "code", "type": "string", "label": "Code", "mandatory": True},
                   {"name": "n", "type": "number", "label": "N"}],
        "rules": [{"type": "range", "target_field": "n", "minimum": 0}],
    }


async def _version(template_id: str, version: int) -> Template:
    return await Template.find_one({"template_id": template_id, "version": version})


@pytest.mark.asyncio
async def test_a_label_only_repost_keeps_everything_else(client: AsyncClient, auth_headers):
    first = await _post(client, auth_headers, _full("C1167_LABEL"))
    item = await _post(client, auth_headers,
                       {"namespace": "wip", "value": "C1167_LABEL", "label": "Second"})
    assert item["status"] == "updated", item
    v1, v2 = await _version(first["id"], 1), await _version(first["id"], 2)
    assert v2.label == "Second"
    assert [f.name for f in v2.fields] == [f.name for f in v1.fields]
    assert v2.identity_fields == ["code"]
    assert v2.header_fields == ["code"]
    assert len(v2.rules) == 1


@pytest.mark.asyncio
async def test_an_unchanged_repost_without_lists_is_unchanged(client: AsyncClient, auth_headers):
    await _post(client, auth_headers, _full("C1167_SAME"))
    item = await _post(client, auth_headers,
                       {"namespace": "wip", "value": "C1167_SAME", "label": "First"})
    assert item["status"] == "unchanged", item


@pytest.mark.asyncio
async def test_an_edge_type_repost_without_endpoints_is_not_refused(
    client: AsyncClient, auth_headers,
):
    for value in ("C1167_ES", "C1167_ET"):
        await _post(client, auth_headers, {
            "namespace": "wip", "value": value, "label": value,
            "fields": [{"name": "id", "type": "string", "label": "Id"}]})
    edge = {
        "namespace": "wip", "value": "C1167_EDGE", "label": "Edge", "usage": "relationship",
        "identity_fields": ["source_ref", "target_ref"],
        "source_templates": ["C1167_ES"], "target_templates": ["C1167_ET"],
        "fields": [
            {"name": "source_ref", "label": "S", "type": "reference",
             "reference_type": "document", "mandatory": True},
            {"name": "target_ref", "label": "T", "type": "reference",
             "reference_type": "document", "mandatory": True},
        ],
    }
    created = await _post(client, auth_headers, edge)
    assert created["status"] == "created", created
    item = await _post(client, auth_headers, {
        "namespace": "wip", "value": "C1167_EDGE", "label": "Edge, relabelled",
        "usage": "relationship"})
    assert item["status"] == "updated", item
    v2 = await _version(created["id"], 2)
    assert [e.lookup_value for e in v2.target_templates] == ["C1167_ET"]


@pytest.mark.asyncio
async def test_an_explicit_value_still_counts(client: AsyncClient, auth_headers):
    """Sending a property, even as [], is a request; only omission keeps."""
    first = await _post(client, auth_headers, _full("C1167_EXPLICIT"))
    item = await _post(client, auth_headers, {**_full("C1167_EXPLICIT"), "rules": []})
    assert item["status"] == "updated", item
    assert (await _version(first["id"], 2)).rules == []
