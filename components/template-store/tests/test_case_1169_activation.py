"""Activation and /validate apply one set of checks, to every identifier form.

- Activation found references by a MongoDB id or bare-value lookup, so a draft
  naming its parent or a referenced template as ``ns:VALUE`` (the form that
  crosses namespaces, stored verbatim by drafts) was refused as not found.
  The lookup now falls back to the Registry, and a set member is found in
  that form too. (A draft's ``extends`` was already safe: create resolves it
  through the Registry and stores the canonical id. Its test is a guard.)
- An edge type's declared endpoints are checked by /validate and the
  activation dry run, which therefore no longer pass a declaration the real
  activation aborts on.
- /validate on an active template ran an existence-only check at any status
  and no reference gate. It now runs activation's checks: a retired
  referent fails, and a reference into a namespace the caller cannot read is
  not found (PoNIF 9: a closed namespace confirms nothing).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from template_store.models.template import Template

API = "/api/template-store/templates"
WRITER = {"X-API-Key": "test_xns_writer_key"}  # write on wip; xns-closed invisible


async def _post(client, headers, payload) -> dict:
    resp = await client.post(API, headers=headers, json=[payload])
    assert resp.status_code == 200, resp.text
    item = resp.json()["results"][0]
    assert item["status"] in ("created", "updated"), item
    return item


def _entity(value: str, ns: str = "wip", status: str | None = None, **extra) -> dict:
    payload = {"namespace": ns, "value": value, "label": value.title(),
               "fields": [{"name": "id", "type": "string", "label": "Id", "mandatory": True}],
               **extra}
    if status:
        payload["status"] = status
    return payload


def _linking(value: str, target: str, status: str | None = None) -> dict:
    payload = _entity(value, status=status)
    payload["fields"].append({"name": "link", "label": "Link", "type": "reference",
                              "reference_type": "document", "target_templates": [target]})
    return payload


async def _activate(client, headers, template_id: str, dry_run: bool = False) -> dict:
    resp = await client.post(
        f"{API}/{template_id}/activate",
        params={"namespace": "wip", "dry_run": str(dry_run).lower()}, headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _validate(client, headers, template_id: str) -> dict:
    resp = await client.post(
        f"{API}/{template_id}/validate", params={"namespace": "wip"}, headers=headers, json={},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_a_draft_extending_ns_value_activates(client: AsyncClient, auth_headers):
    await _post(client, auth_headers, _entity("C1169A_PARENT"))
    child = _entity("C1169A_CHILD", status="draft", extends="wip:C1169A_PARENT",
                    extends_version=1)
    child["fields"] = [{"name": "note", "type": "string", "label": "Note"}]
    draft = await _post(client, auth_headers, child)
    body = await _activate(client, auth_headers, draft["id"])
    assert body["total_activated"] == 1, body


@pytest.mark.asyncio
async def test_a_draft_naming_another_draft_as_ns_value_activates_both(
    client: AsyncClient, auth_headers,
):
    other = await _post(client, auth_headers, _entity("C1169A_B", status="draft"))
    root = await _post(client, auth_headers, _linking("C1169A_A", "wip:C1169A_B", status="draft"))
    body = await _activate(client, auth_headers, root["id"])
    assert sorted(body["activated"]) == sorted([root["id"], other["id"]]), body


@pytest.mark.asyncio
async def test_an_edge_type_draft_with_a_missing_endpoint_does_not_validate(
    client: AsyncClient, auth_headers,
):
    await _post(client, auth_headers, _entity("C1169A_ES"))
    edge = await _post(client, auth_headers, {
        "namespace": "wip", "value": "C1169A_EDGE", "label": "Edge", "usage": "relationship",
        "status": "draft", "identity_fields": ["source_ref", "target_ref"],
        "source_templates": ["C1169A_ES"], "target_templates": ["C1169A_NOWHERE"],
        "fields": [
            {"name": "source_ref", "label": "S", "type": "reference",
             "reference_type": "document", "mandatory": True},
            {"name": "target_ref", "label": "T", "type": "reference",
             "reference_type": "document", "mandatory": True},
        ],
    })
    assert not (await _validate(client, auth_headers, edge["id"]))["valid"]
    preview = await _activate(client, auth_headers, edge["id"], dry_run=True)
    assert preview["errors"], preview
    assert (await Template.find_one({"template_id": edge["id"]})).status == "draft"


@pytest.mark.asyncio
async def test_validate_fails_an_active_template_whose_referent_retired(
    client: AsyncClient, auth_headers,
):
    target = await _post(client, auth_headers, _entity("C1169A_GONE"))
    item = await _post(client, auth_headers, _linking("C1169A_POINTS", "C1169A_GONE"))
    await (await Template.find_one({"template_id": target["id"]})).set({"status": "inactive"})
    body = await _validate(client, auth_headers, item["id"])
    assert not body["valid"], body
    assert "not active" in body["errors"][0]["message"], body


@pytest.mark.asyncio
async def test_validate_does_not_confirm_a_closed_namespace_to_a_non_reader(
    client: AsyncClient, auth_headers,
):
    await _post(client, auth_headers, _entity("C1169A_HIDDEN", ns="xns-closed"))
    item = await _post(client, auth_headers, _linking("C1169A_PEEK", "xns-closed:C1169A_HIDDEN"))
    assert (await _validate(client, auth_headers, item["id"]))["valid"]
    body = await _validate(client, WRITER, item["id"])
    assert not body["valid"], body
    assert "not found" in body["errors"][0]["message"], body
