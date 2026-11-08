"""A template written active satisfies what activation would demand of it.

Activation held checks that a directly-active create, an update and an upsert
skipped. These tests pin the write doors to them:

- an active template's parent is active: a directly-active create, or an
  update that changes ``extends``, refuses a draft or retired parent;
- a ``reference`` field declares its ``reference_type``, and a term reference
  its ``target_terminologies``, on every write (drafts included);
- an update keeps an edge type's ``source_ref`` / ``target_ref`` contract.
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


def _entity(value: str, status: str | None = None, **extra) -> dict:
    payload = {"namespace": "wip", "value": value, "label": value.title(),
               "fields": [{"name": "id", "type": "string", "label": "Id", "mandatory": True}],
               **extra}
    if status:
        payload["status"] = status
    return payload


async def _put(client, headers, template_id: str, **changes) -> dict:
    resp = await client.put(API, headers=headers, params={"namespace": "wip"},
                            json=[{"template_id": template_id, **changes}])
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


async def _retired(client, headers, value: str) -> str:
    item = await _post(client, headers, _entity(value))
    assert item["status"] == "created", item
    await (await Template.find_one({"template_id": item["id"]})).set({"status": "inactive"})
    return item["id"]


@pytest.mark.asyncio
async def test_an_active_create_refuses_a_draft_parent(client: AsyncClient, auth_headers):
    parent = await _post(client, auth_headers, _entity("C1169_DRAFT_PARENT", status="draft"))
    assert parent["status"] == "created", parent
    item = await _post(client, auth_headers, _entity(
        "C1169_CHILD_OF_DRAFT", extends="C1169_DRAFT_PARENT", extends_version=1,
    ))
    assert item["status"] == "error", item
    assert item["error_code"] == "reference_violation", item
    assert item["details"]["rule"] == "parent_not_active", item


@pytest.mark.asyncio
async def test_an_active_create_refuses_a_retired_parent(client: AsyncClient, auth_headers):
    await _retired(client, auth_headers, "C1169_OLD_PARENT")
    item = await _post(client, auth_headers, _entity(
        "C1169_CHILD_OF_OLD", extends="C1169_OLD_PARENT", extends_version=1,
    ))
    assert item["status"] == "error", item
    assert item["details"]["rule"] == "parent_not_active", item


@pytest.mark.asyncio
async def test_a_draft_may_still_extend_a_draft(client: AsyncClient, auth_headers):
    await _post(client, auth_headers, _entity("C1169_DP2", status="draft"))
    child = _entity("C1169_DC2", status="draft", extends="C1169_DP2", extends_version=1)
    child["fields"] = [{"name": "note", "type": "string", "label": "Note"}]
    item = await _post(client, auth_headers, child)
    assert item["status"] == "created", item


@pytest.mark.asyncio
async def test_an_update_refuses_a_new_retired_parent(client: AsyncClient, auth_headers):
    await _retired(client, auth_headers, "C1169_UPD_PARENT")
    child = await _post(client, auth_headers, _entity("C1169_UPD_CHILD"))
    item = await _put(client, auth_headers, child["id"],
                      extends="C1169_UPD_PARENT", extends_version=1)
    assert item["status"] == "error", item
    assert item["details"]["rule"] == "parent_not_active", item


def _ref_field(**shape) -> dict:
    return {"name": "link", "label": "Link", "type": "reference", **shape}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, "draft"])
@pytest.mark.parametrize("shape, rule", [
    ({}, "reference_type_required"),
    ({"reference_type": "term"}, "target_terminologies_required"),
])
async def test_a_reference_field_declares_what_it_references(
    client: AsyncClient, auth_headers, status, shape, rule,
):
    value = f"C1169_REF_{rule}_{status}".upper()
    payload = _entity(value, status=status)
    payload["fields"].append(_ref_field(**shape))
    item = await _post(client, auth_headers, payload)
    assert item["status"] == "error", item
    assert item["error_code"] == "validation_failed", item
    assert item["details"]["rule"] == rule, item


@pytest.mark.asyncio
async def test_an_update_cannot_add_an_undeclared_reference(client: AsyncClient, auth_headers):
    item = await _post(client, auth_headers, _entity("C1169_REF_UPD"))
    fields = [{"name": "id", "type": "string", "label": "Id", "mandatory": True},
              _ref_field(reference_type="term")]
    result = await _put(client, auth_headers, item["id"], fields=fields)
    assert result["status"] == "error", result
    assert result["details"]["rule"] == "target_terminologies_required", result


@pytest.mark.asyncio
async def test_an_update_keeps_the_edge_type_contract(client: AsyncClient, auth_headers):
    for value in ("C1169_ES", "C1169_ET"):
        await _post(client, auth_headers, _entity(value))
    edge = await _post(client, auth_headers, {
        "namespace": "wip", "value": "C1169_EDGE", "label": "Edge", "usage": "relationship",
        "identity_fields": ["source_ref", "target_ref"],
        "source_templates": ["C1169_ES"], "target_templates": ["C1169_ET"],
        "fields": [
            {"name": "source_ref", "label": "S", "type": "reference",
             "reference_type": "document", "mandatory": True},
            {"name": "target_ref", "label": "T", "type": "reference",
             "reference_type": "document", "mandatory": True},
        ],
    })
    assert edge["status"] == "created", edge
    result = await _put(client, auth_headers, edge["id"], fields=[
            {"name": "source_ref", "label": "S", "type": "reference",
             "reference_type": "document", "mandatory": True},
            {"name": "note", "label": "Note", "type": "string"},
        ])
    assert result["status"] == "error", result
    assert result["details"]["rule"] == "relationship_shape", result
    assert await Template.find({"template_id": edge["id"]}).count() == 1
