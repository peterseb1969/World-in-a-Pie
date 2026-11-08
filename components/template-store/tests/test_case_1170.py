"""An edge type's derived endpoint constraint is handled the same on every door.

The constraint ``target_templates`` on ``source_ref`` / ``target_ref`` is a
projection of the template-level declaration: derived when the template is
served, discarded when it is written. These tests pin each door to that rule:

- create discards what a caller sends there before resolving anything, so a
  served edge type (carrying another install's ids) or a typo there posts;
- reactivating an already-active version serves the projection like every
  other response;
- the endpoint heal moves the namespace's change stamp, so a consumer caching
  templates by that stamp re-reads the healed row;
- the template event carries ``usage`` and ``versioned``, so reporting builds
  an edge type's endpoint columns from the event itself;
- /validate and activation read the projected fields, so a stored endpoint
  field is not reported as missing a recommended ``target_templates``;
- re-widening an endpoint the edge type already declares, in its legacy
  lookup-only form, is a no-op for a writer who cannot read its namespace.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from template_store.models.template import Template
from template_store.services.template_service import TemplateService

API = "/api/template-store/templates"
WRITER = {"X-API-Key": "test_xns_writer_key"}  # write on wip; xns-closed invisible


async def _post(client, headers, payload) -> dict:
    resp = await client.post(API, headers=headers, json=[payload])
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


def _entity(value: str, ns: str = "wip") -> dict:
    return {"namespace": ns, "value": value, "label": value.title(),
            "fields": [{"name": "id", "type": "string", "label": "Id", "mandatory": True}]}


def _edge(value: str, source: str, target: str, field_targets: list[str] | None = None,
          status: str | None = None) -> dict:
    payload = {
        "namespace": "wip", "value": value, "label": "Edge", "usage": "relationship",
        "identity_fields": ["source_ref", "target_ref"],
        "source_templates": [source], "target_templates": [target],
        "fields": [
            {"name": "source_ref", "label": "S", "type": "reference",
             "reference_type": "document", "mandatory": True,
             **({"target_templates": field_targets} if field_targets else {})},
            {"name": "target_ref", "label": "T", "type": "reference",
             "reference_type": "document", "mandatory": True,
             **({"target_templates": field_targets} if field_targets else {})},
        ],
    }
    if status:
        payload["status"] = status
    return payload


async def _endpoints(client, headers, *values: str) -> None:
    for value in values:
        item = await _post(client, headers, _entity(value))
        assert item["status"] in ("created", "unchanged"), item


@pytest.mark.asyncio
async def test_create_ignores_the_field_level_constraint(client: AsyncClient, auth_headers):
    await _endpoints(client, auth_headers, "C1170_S", "C1170_T")
    item = await _post(client, auth_headers, _edge(
        "C1170_EDGE", "C1170_S", "C1170_T", field_targets=["NO_SUCH_TEMPLATE"],
    ))
    assert item["status"] == "created", item
    stored = await Template.find_one({"template_id": item["id"]})
    assert all(f.target_templates is None for f in stored.fields)


@pytest.mark.asyncio
async def test_a_served_edge_type_posts_elsewhere(client: AsyncClient, auth_headers):
    """The served shape carries canonical ids in the projection; ids from
    another install resolve to nothing here and must not matter."""
    await _endpoints(client, auth_headers, "C1170_PS", "C1170_PT")
    foreign = "0190f000-0000-7000-8000-00000000dead"
    item = await _post(client, auth_headers, _edge(
        "C1170_PORT", "C1170_PS", "C1170_PT", field_targets=[foreign],
    ))
    assert item["status"] == "created", item


@pytest.mark.asyncio
async def test_reactivate_of_an_active_edge_type_serves_the_projection(
    client: AsyncClient, auth_headers,
):
    await _endpoints(client, auth_headers, "C1170_RS", "C1170_RT")
    item = await _post(client, auth_headers, _edge("C1170_REACT", "C1170_RS", "C1170_RT"))
    resp = await client.post(
        f"{API}/{item['id']}/reactivate", params={"namespace": "wip", "version": 1},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    fields = {f["name"]: f for f in resp.json()["fields"]}
    assert fields["source_ref"]["target_templates"], fields["source_ref"]
    assert fields["target_ref"]["target_templates"], fields["target_ref"]


@pytest.mark.asyncio
async def test_heal_moves_the_namespace_stamp(client: AsyncClient, auth_headers):
    await _endpoints(client, auth_headers, "C1170_HS", "C1170_HT")
    await _post(client, auth_headers, _edge("C1170_HEAL", "C1170_HS", "C1170_HT"))
    row = await Template.find_one({"value": "C1170_HEAL", "namespace": "wip"})
    for entry in row.source_templates + row.target_templates:
        entry.resolved = None
    await row.save()
    before = await TemplateService.get_namespace_template_stamp("wip")

    resp = await client.post(
        "/api/template-store/maintenance/heal-endpoints", headers=auth_headers,
        json={"namespace": "wip", "dry_run": False},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["templates_changed"] == 1

    assert await TemplateService.get_namespace_template_stamp("wip") != before


@pytest.mark.asyncio
async def test_the_template_event_says_what_kind_of_template_it_is(
    client: AsyncClient, auth_headers,
):
    await _endpoints(client, auth_headers, "C1170_ES", "C1170_ET")
    item = await _post(client, auth_headers, _edge("C1170_EVT", "C1170_ES", "C1170_ET"))
    row = await Template.find_one({"template_id": item["id"]})
    payload = TemplateService._template_to_event_payload(row)
    assert payload["usage"] == "relationship"
    assert payload["versioned"] is True


@pytest.mark.asyncio
async def test_validate_does_not_ask_for_the_derived_constraint(
    client: AsyncClient, auth_headers,
):
    await _endpoints(client, auth_headers, "C1170_VS", "C1170_VT")
    item = await _post(client, auth_headers, _edge("C1170_VAL", "C1170_VS", "C1170_VT"))
    resp = await client.post(
        f"{API}/{item['id']}/validate", params={"namespace": "wip"}, headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["valid"], body
    assert not [w for w in body["warnings"] if w["code"] == "recommended"], body


@pytest.mark.asyncio
async def test_activation_does_not_ask_for_the_derived_constraint(
    client: AsyncClient, auth_headers,
):
    await _endpoints(client, auth_headers, "C1170_AS", "C1170_AT")
    item = await _post(client, auth_headers, _edge(
        "C1170_ACT", "C1170_AS", "C1170_AT", status="draft",
    ))
    resp = await client.post(
        f"{API}/{item['id']}/activate", params={"namespace": "wip"}, headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_activated"] == 1, body
    assert not [w for w in body["warnings"] if w["code"] == "recommended"], body


@pytest.mark.asyncio
async def test_rewidening_a_declared_legacy_endpoint_is_a_no_op(
    client: AsyncClient, auth_headers,
):
    """The edge type already declares an endpoint in a closed namespace, in
    the lookup-only form that predates the resolved half. Naming it again is
    not a new reference, so the writer's lack of read there does not matter."""
    await _endpoints(client, auth_headers, "C1170_WS")
    target = await _post(client, auth_headers, _entity("C1170_WT", ns="xns-closed"))
    assert target["status"] == "created", target
    item = await _post(client, auth_headers, _edge(
        "C1170_WIDE", "C1170_WS", "xns-closed:C1170_WT",
    ))
    assert item["status"] == "created", item
    row = await Template.find_one({"template_id": item["id"]})
    for entry in row.target_templates:
        entry.resolved = None
    await row.save()

    resp = await client.post(
        f"{API}/{item['id']}/endpoints", params={"namespace": "wip"}, headers=WRITER,
        json={"add_target_templates": ["xns-closed:C1170_WT"]},
    )
    assert resp.status_code == 200, resp.text
    row = await Template.find_one({"template_id": item["id"]})
    assert [e.lookup_value for e in row.target_templates] == ["xns-closed:C1170_WT"]
