"""Portable reference projection on template reads (?refs=portable).

Stored field references and extends are canonical UUIDs — instance-specific
state that breaks seed files on any other install. The portable projection
maps each UUID back to its referent's value name (bare in the template's own
namespace, ns:VALUE across), which the write door accepts anywhere.
Provenance: CASE-843.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from template_store.models.template import Template
from template_store.services.portable import template_with_portable_refs

API = "/api/template-store"

_UUIDISH = "-"  # stored canonical refs are UUID7 strings containing dashes


def _entity(value: str, fields: list[dict] | None = None) -> dict:
    return {
        "namespace": "wip",
        "value": value,
        "label": value,
        "fields": fields or [{"name": "name", "label": "Name", "type": "string", "mandatory": True}],
    }


async def _create(client: AsyncClient, headers: dict, body: dict) -> dict:
    resp = await client.post(f"{API}/templates", headers=headers, json=[body])
    assert resp.status_code == 200, resp.text
    item = resp.json()["results"][0]
    assert item["status"] in ("created", "updated", "unchanged"), item
    return item


@pytest.mark.asyncio
async def test_portable_rewrites_target_templates_and_extends(
    client: AsyncClient, auth_headers: dict
):
    await _create(client, auth_headers, _entity("PARENT_843"))
    await _create(client, auth_headers, {
        **_entity("CHILD_843"),
        "extends": "PARENT_843",
        "extends_version": 1,
    })
    ref_item = await _create(client, auth_headers, _entity("REF_843", fields=[
        {"name": "name", "label": "Name", "type": "string", "mandatory": True},
        {
            "name": "parent",
            "label": "Parent",
            "type": "reference",
            "reference_type": "document",
            "target_templates": ["PARENT_843"],
        },
    ]))

    # Default (canonical): the stored refs are resolved UUIDs, not values.
    resp = await client.get(
        f"{API}/templates/{ref_item['id']}", headers=auth_headers,
        params={"namespace": "wip"},
    )
    assert resp.status_code == 200, resp.text
    stored = next(f for f in resp.json()["fields"] if f["name"] == "parent")
    assert stored["target_templates"] != ["PARENT_843"]
    assert _UUIDISH in stored["target_templates"][0]

    # Portable: the same read serves the value form.
    resp = await client.get(
        f"{API}/templates/{ref_item['id']}", headers=auth_headers,
        params={"namespace": "wip", "refs": "portable"},
    )
    assert resp.status_code == 200, resp.text
    portable = next(f for f in resp.json()["fields"] if f["name"] == "parent")
    assert portable["target_templates"] == ["PARENT_843"]

    # extends is stored resolved too — the projection covers it.
    resp = await client.get(
        f"{API}/templates/by-value/CHILD_843", headers=auth_headers,
        params={"namespace": "wip", "refs": "portable"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["extends"] == "PARENT_843"


@pytest.mark.asyncio
async def test_portable_rewrites_terminology_ref(
    client: AsyncClient, auth_headers: dict
):
    item = await _create(client, auth_headers, _entity("TERM_REF_843", fields=[
        {"name": "name", "label": "Name", "type": "string", "mandatory": True},
        {"name": "gender", "label": "Gender", "type": "term", "terminology_ref": "GENDER"},
    ]))

    # Stored form is the Registry-resolved UUID.
    resp = await client.get(
        f"{API}/templates/{item['id']}", headers=auth_headers,
        params={"namespace": "wip"},
    )
    stored_ref = next(
        f for f in resp.json()["fields"] if f["name"] == "gender"
    )["terminology_ref"]
    assert stored_ref != "GENDER" and _UUIDISH in stored_ref

    # The terminology's home store answers the reverse lookup.
    def_store = AsyncMock()
    def_store.get_terminology = AsyncMock(return_value={
        "terminology_id": stored_ref, "namespace": "wip",
        "value": "GENDER", "status": "active",
    })
    with patch(
        "template_store.services.portable.get_def_store_client",
        return_value=def_store,
    ):
        resp = await client.get(
            f"{API}/templates/{item['id']}", headers=auth_headers,
            params={"namespace": "wip", "refs": "portable"},
        )
    assert resp.status_code == 200, resp.text
    portable_ref = next(
        f for f in resp.json()["fields"] if f["name"] == "gender"
    )["terminology_ref"]
    assert portable_ref == "GENDER"
    def_store.get_terminology.assert_awaited_once_with(terminology_id=stored_ref)


@pytest.mark.asyncio
async def test_portable_qualifies_cross_namespace_referents(client: AsyncClient):
    """A referent living outside the template's namespace comes back as
    ns:VALUE — the only value form the write door accepts across a
    namespace. Service-level: the foreign template row is planted directly."""
    foreign_id = "01990000-0000-7000-8000-000000000843"
    await Template(
        template_id=foreign_id, namespace="otherns843", value="FOREIGN_843",
        label="Foreign", version=1, status="active", fields=[],
    ).insert()
    try:
        local = Template(
            template_id="01990000-0000-7000-8000-000000000844",
            namespace="wip", value="LOCAL_843", label="Local", version=1,
            status="active",
            fields=[{
                "name": "ref", "label": "Ref", "type": "reference",
                "reference_type": "document",
                "target_templates": [foreign_id],
            }],
        )
        portable = await template_with_portable_refs(local)
        assert portable.fields[0].target_templates == ["otherns843:FOREIGN_843"]
        # The input object is not mutated — the projection is a copy.
        assert local.fields[0].target_templates == [foreign_id]
    finally:
        await Template.find({"template_id": foreign_id}).delete()


@pytest.mark.asyncio
async def test_portable_passes_through_unknown_and_non_uuid_refs(client: AsyncClient):
    """A dangling UUID stays visible (fail-open), and a ref that is not
    UUID-shaped is already portable and is left untouched — without even a
    lookup (the def-store client must not be called)."""
    dangling = "01990000-dead-7000-8000-000000000999"
    tpl = Template(
        template_id="01990000-0000-7000-8000-000000000845",
        namespace="wip", value="PASSTHRU_843", label="x", version=1,
        status="active",
        fields=[
            {
                "name": "ref", "label": "Ref", "type": "reference",
                "reference_type": "document",
                "target_templates": [dangling],
            },
            {"name": "status", "label": "Status", "type": "term", "terminology_ref": "DOC_STATUS"},
        ],
    )
    def_store = AsyncMock()
    with patch(
        "template_store.services.portable.get_def_store_client",
        return_value=def_store,
    ):
        portable = await template_with_portable_refs(tpl)
    assert portable.fields[0].target_templates == [dangling]
    assert portable.fields[1].terminology_ref == "DOC_STATUS"
    def_store.get_terminology.assert_not_awaited()
