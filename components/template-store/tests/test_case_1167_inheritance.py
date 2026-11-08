"""A template that extends another is an ordinary template over the composed
fields, and declares only what is its own.

- A field or rule the parent chain already declares is a conflict, on create,
  update, cascade and activation (sending the served, composed template back
  used to store the parent's fields as the child's own copies, which then
  stopped following the parent).
- Identity belongs to the composed template: a child may name any of its
  fields, own or inherited. With none of its own, it uses the parent's, which
  GET serves; sending that back is no change (it was refused as an identity
  change from [] to the parent's).
- A new parent, or parent version, that changes the effective identity is a
  fork, refused like any identity change.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

API = "/api/template-store/templates"


async def _post(client, headers, payload) -> dict:
    resp = await client.post(API, headers=headers, json=[payload])
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


async def _put(client, headers, template_id: str, **changes) -> dict:
    resp = await client.put(API, headers=headers, params={"namespace": "wip"},
                            json=[{"template_id": template_id, **changes}])
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


def _tpl(value: str, fields: list[dict], **extra) -> dict:
    return {"namespace": "wip", "value": value, "label": value.title(), "fields": fields, **extra}


ADDRESS = [{"name": "street", "type": "string", "label": "Street"},
           {"name": "postcode", "type": "string", "label": "Postcode"}]


async def _address(client, headers, value: str, **extra) -> dict:
    item = await _post(client, headers, _tpl(value, ADDRESS, **extra))
    assert item["status"] in ("created", "updated"), item
    return item


@pytest.mark.asyncio
async def test_a_child_names_inherited_fields_in_its_identity(client: AsyncClient, auth_headers):
    await _address(client, auth_headers, "C1167I_ADDRESS")
    order = await _post(client, auth_headers, _tpl(
        "C1167I_ORDER", [{"name": "order_no", "type": "string", "label": "Order"}],
        extends="C1167I_ADDRESS", extends_version=1,
        identity_fields=["order_no", "postcode"],
    ))
    assert order["status"] == "created", order


@pytest.mark.asyncio
async def test_sending_back_the_served_identity_is_no_change(client: AsyncClient, auth_headers):
    await _address(client, auth_headers, "C1167I_KEYED", identity_fields=["postcode"])
    child = await _post(client, auth_headers, _tpl(
        "C1167I_KEYED_CHILD", [{"name": "note", "type": "string", "label": "Note"}],
        extends="C1167I_KEYED", extends_version=1,
    ))
    served = (await client.get(f"{API}/{child['id']}", headers=auth_headers)).json()
    assert served["identity_fields"] == ["postcode"]
    own = [{"name": "note", "type": "string", "label": "Note (edited)"}]
    result = await _put(client, auth_headers, child["id"], label="Renamed",
                        identity_fields=served["identity_fields"], fields=own)
    assert result["status"] == "updated", result
    again = await _post(client, auth_headers, _tpl(
        "C1167I_KEYED_CHILD", own, label="Renamed", extends="C1167I_KEYED", extends_version=1,
        identity_fields=["postcode"],
    ))
    assert again["status"] == "unchanged", again


@pytest.mark.asyncio
async def test_a_rule_the_parent_declares_is_a_conflict(client: AsyncClient, auth_headers):
    rule = {"type": "dependency", "target_field": "street",
            "conditions": [{"field": "postcode", "operator": "exists"}]}
    await _address(client, auth_headers, "C1167I_RULED", rules=[rule])
    item = await _post(client, auth_headers, _tpl(
        "C1167I_RULED_CHILD", [{"name": "note", "type": "string", "label": "Note"}],
        extends="C1167I_RULED", extends_version=1, rules=[rule],
    ))
    assert item["status"] == "error", item
    assert item["details"]["rule"] == "rule_defined_by_parent", item


@pytest.mark.asyncio
async def test_a_reparent_that_changes_identity_is_a_fork(client: AsyncClient, auth_headers):
    await _address(client, auth_headers, "C1167I_BY_POSTCODE", identity_fields=["postcode"])
    await _address(client, auth_headers, "C1167I_BY_STREET", identity_fields=["street"])
    child = await _post(client, auth_headers, _tpl(
        "C1167I_MOVER", [{"name": "note", "type": "string", "label": "Note"}],
        extends="C1167I_BY_POSTCODE", extends_version=1,
    ))
    result = await _put(client, auth_headers, child["id"],
                        extends="C1167I_BY_STREET", extends_version=1)
    assert result["status"] == "error", result
    assert result["error_code"] == "identity_fields_immutable", result


@pytest.mark.asyncio
async def test_a_cascade_refuses_a_child_the_new_parent_version_clashes_with(
    client: AsyncClient, auth_headers,
):
    parent = await _address(client, auth_headers, "C1167I_GROWS")
    child = await _post(client, auth_headers, _tpl(
        "C1167I_GROWS_CHILD", [{"name": "note", "type": "string", "label": "Note"}],
        extends="C1167I_GROWS", extends_version=1,
    ))
    assert child["status"] == "created", child
    bumped = await _post(client, auth_headers, _tpl(
        "C1167I_GROWS", [*ADDRESS, {"name": "note", "type": "string", "label": "Note"}]))
    assert bumped["status"] == "updated", bumped
    resp = await client.post(f"{API}/{parent['id']}/cascade", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    [result] = resp.json()["results"]
    assert result["status"] == "error", result
    assert "already defined by the parent" in result["error"], result
