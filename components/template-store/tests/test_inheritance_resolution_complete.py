"""Inheritance resolution serves the whole child, or refuses.

The resolved template is the child with three properties merged along the
chain (fields, rules, identity_fields); every other property is the child's
own. A relationship or versioned:false template that extends another must
still be served as one, because document-store and reporting-sync read the
resolved form. A chain that cannot be resolved is a 409
``inheritance_unresolved`` on the resolving routes (never the child's own
fields served as if they were the schema), and the stored template stays
readable at /raw. The latest-by-value route resolves, as it says it does.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from .test_relationship_templates import API, _ensure_endpoint_templates, _post_template

READER = {"X-API-Key": "test_xns_reader_key"}
WRITER = {"X-API-Key": "test_xns_writer_key"}

# The three properties resolution merges; everything else is the child's.
MERGED = {"fields", "rules", "identity_fields"}


async def _parent(client: AsyncClient, auth_headers: dict, value: str, namespace: str = "wip") -> str:
    item = await _post_template(client, auth_headers, {
        "namespace": namespace,
        "value": value,
        "label": value.title(),
        "fields": [{"name": "inherited_note", "type": "string", "label": "Note"}],
        "rules": [{"type": "dependency", "target_field": "inherited_note",
                   "conditions": [{"field": "inherited_note", "operator": "exists"}]}],
    })
    assert item["status"] in ("created", "unchanged"), item
    return item["id"]


async def _edge_child(client: AsyncClient, auth_headers: dict, parent_id: str) -> str:
    await _ensure_endpoint_templates(client, auth_headers, "EXPERIMENT", "MOLECULE")
    item = await _post_template(client, auth_headers, {
        "namespace": "wip",
        "value": "INH_FULL_EDGE",
        "label": "Inheriting edge",
        "extends": parent_id,
        "extends_version": 1,
        "usage": "relationship",
        "versioned": False,
        "source_templates": ["EXPERIMENT"],
        "target_templates": ["MOLECULE"],
        "identity_fields": ["source_ref", "target_ref"],
        "header_fields": ["weight"],
        "reporting": {"sync_enabled": False, "table_name": "inh_full_edge_custom"},
        "fields": [
            {"name": "source_ref", "type": "reference", "label": "Source",
             "reference_type": "document", "mandatory": True},
            {"name": "target_ref", "type": "reference", "label": "Target",
             "reference_type": "document", "mandatory": True},
            {"name": "weight", "type": "integer", "label": "Weight"},
        ],
    })
    assert item["status"] in ("created", "unchanged"), item
    return item["id"]


@pytest.mark.asyncio
async def test_every_child_property_survives_resolution(client: AsyncClient, auth_headers: dict):
    parent_id = await _parent(client, auth_headers, "INH_FULL_PARENT")
    child_id = await _edge_child(client, auth_headers, parent_id)

    raw = (await client.get(f"{API}/templates/{child_id}/raw", headers=auth_headers)).json()
    resp = await client.get(f"{API}/templates/{child_id}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    resolved = resp.json()

    # Every served key, so a property added to the model later is covered.
    for key in raw.keys() - MERGED:
        assert resolved[key] == raw[key], key
    assert resolved["usage"] == "relationship"
    assert resolved["versioned"] is False
    assert resolved["extends_version"] == 1

    # The merged three: the parent's field and rule arrive, the child's stay.
    names = [f["name"] for f in resolved["fields"]]
    assert names == ["inherited_note", "source_ref", "target_ref", "weight"]
    assert len(resolved["rules"]) == 1
    assert resolved["identity_fields"] == ["source_ref", "target_ref"]
    # The edge's derived endpoint constraint is still served on its refs.
    by_name = {f["name"]: f for f in resolved["fields"]}
    raw_by_name = {f["name"]: f for f in raw["fields"]}
    for ref in ("source_ref", "target_ref"):
        assert by_name[ref]["target_templates"] == raw_by_name[ref]["target_templates"]
        assert by_name[ref]["target_templates"]


@pytest.mark.asyncio
async def test_latest_by_value_serves_the_resolved_template(client: AsyncClient, auth_headers: dict):
    parent_id = await _parent(client, auth_headers, "INH_FULL_PARENT")
    await _edge_child(client, auth_headers, parent_id)

    resp = await client.get(
        f"{API}/templates/by-value/INH_FULL_EDGE", params={"namespace": "wip"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "inherited_note" in [f["name"] for f in body["fields"]]
    assert body["usage"] == "relationship"


async def _orphan(client: AsyncClient, auth_headers: dict, namespace: str, value: str) -> str:
    """A draft child pinned to a parent version that does not exist (drafts
    skip reference validation, which is one way a chain breaks)."""
    parent_id = await _parent(client, auth_headers, f"{value}_PARENT", namespace)
    item = await _post_template(client, auth_headers, {
        "namespace": namespace,
        "value": value,
        "label": value.title(),
        "status": "draft",
        "extends": parent_id,
        "extends_version": 7,
        "fields": [{"name": "own", "type": "string", "label": "Own"}],
    })
    assert item["status"] == "created", item
    return item["id"]


@pytest.mark.asyncio
async def test_an_unresolvable_chain_is_a_409_on_every_resolving_route(
    client: AsyncClient, auth_headers: dict
):
    orphan_id = await _orphan(client, auth_headers, "wip", "INH_ORPHAN")

    for url, params in (
        (f"{API}/templates/{orphan_id}", None),
        (f"{API}/templates/by-value/INH_ORPHAN", {"namespace": "wip"}),
        (f"{API}/templates/by-value/INH_ORPHAN/versions/1", {"namespace": "wip"}),
    ):
        resp = await client.get(url, params=params, headers=auth_headers)
        assert resp.status_code == 409, (url, resp.text)
        body = resp.json()
        assert body["error_code"] == "inheritance_unresolved"
        assert body["details"] == {"rule": "missing_parent"}
        assert "INH_ORPHAN" in body["detail"]

    # The stored form stays readable.
    raw = await client.get(f"{API}/templates/{orphan_id}/raw", headers=auth_headers)
    assert raw.status_code == 200
    assert [f["name"] for f in raw.json()["fields"]] == ["own"]


@pytest.mark.asyncio
async def test_the_409_does_not_reveal_a_template_the_caller_cannot_read(
    client: AsyncClient, auth_headers: dict
):
    orphan_id = await _orphan(client, auth_headers, "xns-closed", "INH_CLOSED_ORPHAN")

    hidden = await client.get(f"{API}/templates/{orphan_id}", headers=WRITER)
    assert hidden.status_code == 404, hidden.text

    seen = await client.get(f"{API}/templates/{orphan_id}", headers=READER)
    assert seen.status_code == 409, seen.text
    assert seen.json()["error_code"] == "inheritance_unresolved"
