"""Activation stays in its namespace, and the reference gates refuse what they
cannot place.

- Cascading activation collected every draft a template referenced, in any
  namespace, and activated the whole set: a key with write on one namespace
  could activate another namespace's draft by referencing its id from a draft
  of its own. The set now holds only drafts of the namespace being activated;
  a draft elsewhere is an ordinary reference, refused as not active.
- The inbound gate passed a target whose namespace the Registry did not
  report, and outbound isolation dropped such references from its check.
  Both now refuse them (an unanswered lookup is not a pass); an activation
  set's own drafts, reserved in the Registry, are placed from the set.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from template_store.models.template import Template
from template_store.services.errors import TemplateError
from template_store.services.inbound_gate import InboundGate
from template_store.services.template_service import TemplateService

API = "/api/template-store/templates"
READER = {"X-API-Key": "test_xns_reader_key"}  # write on wip; read on xns-closed


async def _draft(client, headers, payload) -> str:
    resp = await client.post(API, headers=headers, json=[{"status": "draft", **payload}])
    assert resp.status_code == 200, resp.text
    item = resp.json()["results"][0]
    assert item["status"] == "created", item
    return item["id"]


@pytest.mark.asyncio
async def test_activation_does_not_reach_another_namespaces_draft(client: AsyncClient, auth_headers):
    foreign = await _draft(client, auth_headers, {
        "namespace": "xns-closed", "value": "C1166_FOREIGN", "label": "Foreign",
        "fields": [{"name": "x", "label": "X", "type": "string"}],
    })
    mine = await _draft(client, READER, {
        "namespace": "wip", "value": "C1166_MINE", "label": "Mine",
        "fields": [{"name": "sub", "label": "Sub", "type": "object",
                    "template_ref": foreign, "template_ref_version": 1}],
    })

    resp = await client.post(f"{API}/{mine}/activate", params={"namespace": "wip"}, headers=READER)

    assert foreign not in (resp.json().get("activated") or []), resp.text
    assert (await Template.find_one({"template_id": foreign})).status == "draft"
    assert (await Template.find_one({"template_id": mine})).status == "draft"


@pytest.mark.asyncio
async def test_activation_within_a_namespace_still_cascades(client: AsyncClient, auth_headers):
    inner = await _draft(client, auth_headers, {
        "namespace": "wip", "value": "C1166_INNER", "label": "Inner",
        "fields": [{"name": "x", "label": "X", "type": "string"}],
    })
    outer = await _draft(client, auth_headers, {
        "namespace": "wip", "value": "C1166_OUTER", "label": "Outer",
        "fields": [{"name": "sub", "label": "Sub", "type": "object",
                    "template_ref": "C1166_INNER", "template_ref_version": 1}],
    })
    resp = await client.post(f"{API}/{outer}/activate", params={"namespace": "wip"},
                             headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert sorted(resp.json()["activated"]) == sorted([inner, outer])


def _registry_reporting(placed: dict[str, str]):
    client = MagicMock()
    client.lookup_entry_namespaces = AsyncMock(return_value=placed)
    return patch("template_store.services.inbound_gate.get_registry_client", return_value=client)


@pytest.mark.asyncio
async def test_inbound_gate_refuses_a_target_it_cannot_place():
    with _registry_reporting({}):
        gate = InboundGate("wip")
        assert await gate.visible({"REF": "id-unplaced"}) == {}
        assert await gate.refuses_entry("id-unplaced") is True
        carried = InboundGate("wip", carried=frozenset({"id-unplaced"}))
        assert await carried.refuses_entry("id-unplaced") is False
        known = InboundGate("wip", placed={"id-unplaced": "wip"})
        assert await known.visible({"REF": "id-unplaced"}) == {"REF": "id-unplaced"}


@pytest.mark.asyncio
async def test_outbound_isolation_refuses_a_reference_it_cannot_place(client: AsyncClient):
    client = MagicMock()
    client.lookup_entry_namespaces = AsyncMock(return_value={})
    with patch("template_store.services.template_service.get_registry_client", return_value=client):
        with pytest.raises(TemplateError) as refused:
            await TemplateService._check_reference_isolation(
                "wip", template_ids=["id-unplaced"], terminology_ids=[],
            )
        assert refused.value.code == "reference_violation"
        # Placed by the caller (an activation set's own draft): checked, passes.
        await TemplateService._check_reference_isolation(
            "wip", template_ids=["id-unplaced"], terminology_ids=[],
            known_namespaces={"id-unplaced": "wip"},
        )
