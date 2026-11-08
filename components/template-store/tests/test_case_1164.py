"""Reactivate and endpoint widening need write on the template's own namespace.

Both routes checked write on the ``namespace`` query parameter, then acted on
whichever template the id named. A canonical id names one template wherever it
lives, so a key with write on ``wip`` alone could reactivate a frozen version
of a template in another namespace, or widen another namespace's edge type, by
naming it with ``?namespace=wip``. The gate is now the template row's own
namespace: a caller that cannot see it gets the 404 of an unknown id, a caller
that can read but not write it gets 403, and nothing changes.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from template_store.models.template import Template

API = "/api/template-store/templates"
WRITER = {"X-API-Key": "test_xns_writer_key"}  # write on wip; xns-closed invisible
READER = {"X-API-Key": "test_xns_reader_key"}  # write on wip; read on xns-closed
NS = "xns-closed"


async def _post(client, headers, payload) -> dict:
    resp = await client.post(API, headers=headers, json=[payload])
    assert resp.status_code == 200, resp.text
    item = resp.json()["results"][0]
    assert item["status"] in ("created", "updated", "unchanged"), item
    return item


def _entity(value: str, ns: str = NS, label: str | None = None) -> dict:
    return {"namespace": ns, "value": value, "label": label or value.title(),
            "fields": [{"name": "id", "type": "string", "label": "Id", "mandatory": True}]}


async def _frozen_v1(client, headers, value: str, ns: str = NS) -> str:
    """A template with two versions whose v1 is inactive; returns its id."""
    first = await _post(client, headers, _entity(value, ns))
    await _post(client, headers, _entity(value, ns, label="Second"))
    row = await Template.find_one({"template_id": first["id"], "version": 1})
    await row.set({"status": "inactive"})
    return first["id"]


async def _edge_type(client, headers) -> str:
    for value in ("C1164_SRC", "C1164_TGT", "C1164_NEW"):
        await _post(client, headers, _entity(value))
    item = await _post(client, headers, {
        "namespace": NS, "value": "C1164_EDGE", "label": "Edge", "usage": "relationship",
        "identity_fields": ["source_ref", "target_ref"],
        "source_templates": ["C1164_SRC"], "target_templates": ["C1164_TGT"],
        "fields": [
            {"name": "source_ref", "label": "S", "type": "reference",
             "reference_type": "document", "target_templates": ["C1164_SRC"], "mandatory": True},
            {"name": "target_ref", "label": "T", "type": "reference",
             "reference_type": "document", "target_templates": ["C1164_TGT"], "mandatory": True},
        ],
    })
    return item["id"]


async def _status(template_id: str, version: int) -> str:
    return (await Template.find_one({"template_id": template_id, "version": version})).status


@pytest.mark.asyncio
@pytest.mark.parametrize("headers, expected", [(WRITER, 404), (READER, 403)])
async def test_reactivate_elsewhere_is_refused(client: AsyncClient, auth_headers, headers, expected):
    tid = await _frozen_v1(client, auth_headers, f"C1164_REACT_{expected}")
    resp = await client.post(
        f"{API}/{tid}/reactivate", params={"namespace": "wip", "version": 1}, headers=headers,
    )
    assert resp.status_code == expected, resp.text
    assert await _status(tid, 1) == "inactive"


@pytest.mark.asyncio
@pytest.mark.parametrize("headers, expected", [(WRITER, 404), (READER, 403)])
async def test_widening_elsewhere_is_refused(client: AsyncClient, auth_headers, headers, expected):
    tid = await _edge_type(client, auth_headers)
    resp = await client.post(
        f"{API}/{tid}/endpoints", params={"namespace": "wip"}, headers=headers,
        json={"add_source_templates": [], "add_target_templates": ["C1164_NEW"]},
    )
    assert resp.status_code == expected, resp.text
    row = await Template.find_one({"template_id": tid})
    assert len(row.target_templates) == 1


@pytest.mark.asyncio
async def test_invisible_template_answers_like_an_unknown_id(client: AsyncClient, auth_headers):
    tid = await _frozen_v1(client, auth_headers, "C1164_HIDDEN")
    hidden = await client.post(
        f"{API}/{tid}/reactivate", params={"namespace": "wip", "version": 1}, headers=WRITER,
    )
    unknown_id = "019a0000-0000-7000-8000-000000000000"
    unknown = await client.post(
        f"{API}/{unknown_id}/reactivate", params={"namespace": "wip", "version": 1},
        headers=WRITER,
    )
    assert (hidden.status_code, unknown.status_code) == (404, 404)
    assert hidden.json()["detail"] == unknown.json()["detail"].replace(unknown_id, tid)


@pytest.mark.asyncio
async def test_writer_still_reactivates_its_own_template(client: AsyncClient, auth_headers):
    tid = await _frozen_v1(client, auth_headers, "C1164_OWN", ns="wip")
    resp = await client.post(
        f"{API}/{tid}/reactivate", params={"namespace": "wip", "version": 1}, headers=WRITER,
    )
    assert resp.status_code == 200, resp.text
    assert await _status(tid, 1) == "active"
