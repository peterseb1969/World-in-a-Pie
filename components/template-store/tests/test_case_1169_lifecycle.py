"""A template becomes active only through a door that checks it.

- The Registry resolves a draft's id, and a retired template's, like any
  other, so a directly-active write accepted either as a reference target or
  an edge type's endpoint. A template reference a non-draft write adds (create,
  update, endpoint widening) must now name a template with an active version,
  as activation demands; a reference the previous version held is carried.
- Updating a draft minted the next version active, bypassing activation. The
  next version of a draft is a draft.
- Soft delete accepts a draft, and reactivate turned the inactive row active
  without any check. Reactivate now holds the version to what activation
  demands and brings it to its active form the same way.
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


def _entity(value: str, status: str | None = None, link: str | None = None) -> dict:
    payload = {"namespace": "wip", "value": value, "label": value.title(),
               "fields": [{"name": "id", "type": "string", "label": "Id", "mandatory": True}]}
    if link:
        payload["fields"].append({"name": "link", "label": "Link", "type": "reference",
                                  "reference_type": "document", "target_templates": [link]})
    if status:
        payload["status"] = status
    return payload


def _edge(value: str, source: str, target: str) -> dict:
    return {
        "namespace": "wip", "value": value, "label": "Edge", "usage": "relationship",
        "identity_fields": ["source_ref", "target_ref"],
        "source_templates": [source], "target_templates": [target],
        "fields": [
            {"name": "source_ref", "label": "S", "type": "reference",
             "reference_type": "document", "mandatory": True},
            {"name": "target_ref", "label": "T", "type": "reference",
             "reference_type": "document", "mandatory": True},
        ],
    }


async def _retire(template_id: str) -> None:
    await (await Template.find_one({"template_id": template_id})).set({"status": "inactive"})


async def _soft_delete(client, headers, template_id: str) -> None:
    resp = await client.request(
        "DELETE", API, headers=headers, params={"namespace": "wip"},
        json=[{"id": template_id, "version": 1}],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][0]["status"] == "deleted", resp.text


async def _reactivate(client, headers, template_id: str):
    return await client.post(
        f"{API}/{template_id}/reactivate", params={"namespace": "wip", "version": 1},
        headers=headers,
    )


@pytest.mark.asyncio
async def test_an_edge_type_refuses_a_draft_endpoint(client: AsyncClient, auth_headers):
    await _post(client, auth_headers, _entity("C1169L_SRC"))
    draft = await _post(client, auth_headers, _entity("C1169L_DRAFT", status="draft"))
    assert draft["status"] == "created", draft
    edge = await _post(client, auth_headers, _edge("C1169L_EDGE", "C1169L_SRC", "C1169L_DRAFT"))
    assert edge["status"] == "error", edge
    assert edge["details"]["rule"] == "template_not_active", edge


@pytest.mark.asyncio
@pytest.mark.parametrize("retired", [False, True])
async def test_a_reference_target_must_be_active(client: AsyncClient, auth_headers, retired):
    value = f"C1169L_TARGET_{retired}".upper()
    target = await _post(client, auth_headers, _entity(value, status=None if retired else "draft"))
    if retired:
        await _retire(target["id"])
    item = await _post(client, auth_headers, _entity(f"C1169L_POINTS_{retired}".upper(), link=value))
    assert item["status"] == "error", item
    assert item["details"]["rule"] == "template_not_active", item


@pytest.mark.asyncio
async def test_a_carried_reference_to_a_retired_target_still_updates(
    client: AsyncClient, auth_headers,
):
    target = await _post(client, auth_headers, _entity("C1169L_LATER_GONE"))
    item = await _post(client, auth_headers, _entity("C1169L_KEEPS", link="C1169L_LATER_GONE"))
    await _retire(target["id"])
    fields = _entity("x", link=target["id"])["fields"]
    fields.append({"name": "extra", "label": "Extra", "type": "string"})
    resp = await client.put(API, headers=auth_headers, params={"namespace": "wip"},
                            json=[{"template_id": item["id"], "fields": fields}])
    result = resp.json()["results"][0]
    assert result["status"] == "updated", result


@pytest.mark.asyncio
async def test_widening_refuses_a_retired_endpoint(client: AsyncClient, auth_headers):
    for value in ("C1169L_WS", "C1169L_WT"):
        await _post(client, auth_headers, _entity(value))
    gone = await _post(client, auth_headers, _entity("C1169L_WGONE"))
    await _retire(gone["id"])
    edge = await _post(client, auth_headers, _edge("C1169L_WEDGE", "C1169L_WS", "C1169L_WT"))
    resp = await client.post(
        f"{API}/{edge['id']}/endpoints", params={"namespace": "wip"}, headers=auth_headers,
        json={"add_target_templates": ["C1169L_WGONE"]},
    )
    assert resp.status_code == 400, resp.text
    assert "no active version" in resp.text, resp.text


@pytest.mark.asyncio
async def test_updating_a_draft_writes_a_draft(client: AsyncClient, auth_headers):
    draft = await _post(client, auth_headers, _entity("C1169L_UPD", status="draft"))
    resp = await client.put(API, headers=auth_headers, params={"namespace": "wip"},
                            json=[{"template_id": draft["id"], "label": "Renamed"}])
    item = resp.json()["results"][0]
    assert item["status"] == "updated", item
    v2 = await Template.find_one({"template_id": draft["id"], "version": 2})
    assert v2.status == "draft"


@pytest.mark.asyncio
async def test_a_soft_deleted_draft_is_not_reactivated_unchecked(client: AsyncClient, auth_headers):
    draft = await _post(client, auth_headers, _entity(
        "C1169L_BROKEN", status="draft", link="C1169L_NOT_THERE",
    ))
    await _soft_delete(client, auth_headers, draft["id"])
    resp = await _reactivate(client, auth_headers, draft["id"])
    assert resp.status_code == 400, resp.text
    assert "C1169L_NOT_THERE" in resp.text, resp.text
    assert (await Template.find_one({"template_id": draft["id"]})).status == "inactive"


@pytest.mark.asyncio
async def test_a_reactivated_draft_is_in_its_active_form(client: AsyncClient, auth_headers):
    target = await _post(client, auth_headers, _entity("C1169L_THERE"))
    draft = await _post(client, auth_headers, _entity(
        "C1169L_FINE", status="draft", link="C1169L_THERE",
    ))
    await _soft_delete(client, auth_headers, draft["id"])
    resp = await _reactivate(client, auth_headers, draft["id"])
    assert resp.status_code == 200, resp.text
    row = await Template.find_one({"template_id": draft["id"]})
    assert row.status == "active"
    link = next(f for f in row.fields if f.name == "link")
    assert link.target_templates == [target["id"]]
    assert row.referent_identity_paths is not None


@pytest.mark.asyncio
async def test_a_version_whose_parent_retired_is_not_reactivated(client: AsyncClient, auth_headers):
    parent = await _post(client, auth_headers, _entity("C1169L_PARENT"))
    child_payload = _entity("C1169L_CHILD")
    child_payload.update(extends="C1169L_PARENT", extends_version=1,
                         fields=[{"name": "note", "type": "string", "label": "Note"}])
    child = await _post(client, auth_headers, child_payload)
    assert child["status"] == "created", child
    await _soft_delete(client, auth_headers, child["id"])
    await (await Template.find_one({"template_id": parent["id"]})).set({"status": "inactive"})
    resp = await _reactivate(client, auth_headers, child["id"])
    assert resp.status_code == 400, resp.text
    assert "not active" in resp.text, resp.text
