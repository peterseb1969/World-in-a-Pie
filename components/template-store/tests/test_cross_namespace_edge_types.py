"""An edge type's declared endpoints pass the cross-namespace reference gates.

Endpoint declarations (source_templates / target_templates) are template
references like any other: an edge type in 'wip' whose target templates live
in 'xns-closed' (closed to inbound references, the default) needs a writer
who can read 'xns-closed', on create and when widening.
"""

import pytest
from httpx import AsyncClient

from .test_relationship_templates import (
    API,
    _ensure_endpoint_templates,
    _entity_template,
    _post_template,
    _relationship_template,
)

READER = {"X-API-Key": "test_xns_reader_key"}
WRITER = {"X-API-Key": "test_xns_writer_key"}


async def _closed_target(client: AsyncClient, auth_headers: dict) -> None:
    await _ensure_endpoint_templates(client, auth_headers, "EXPERIMENT", "MOLECULE")
    target = _entity_template("XNS_TARGET")
    target["namespace"] = "xns-closed"
    item = await _post_template(client, auth_headers, target)
    assert item["status"] in ("created", "unchanged"), item


@pytest.mark.asyncio
async def test_an_edge_type_targeting_a_closed_namespace_needs_read(client, auth_headers):
    await _closed_target(client, auth_headers)
    # Only the declaration names the target: the target_ref field carries
    # none, so the endpoint gate (not the field-level one) decides.
    edge = _relationship_template(value="EDGE_XNS", source_templates=["EXPERIMENT"],
                                  target_templates=["xns-closed:XNS_TARGET"],
                                  target_ref_targets=[])

    refused = await _post_template(client, WRITER, edge)
    assert refused["status"] == "error", refused
    assert refused["error_code"] == "reference_violation"
    # Exactly what a qualified value that names nothing gets: the error
    # echoes only the identifier the writer submitted.
    missing = await _post_template(client, WRITER, {
        **edge, "target_templates": ["xns-closed:XNS_ABSENT"]})
    assert refused["error"] == missing["error"].replace("XNS_ABSENT", "XNS_TARGET")

    created = await _post_template(client, READER, edge)
    assert created["status"] == "created", created


@pytest.mark.asyncio
async def test_widening_into_a_closed_namespace_needs_read(client, auth_headers):
    await _closed_target(client, auth_headers)
    edge = await _post_template(client, READER, _relationship_template(
        value="EDGE_XNS_WIDEN", source_templates=["EXPERIMENT"], target_templates=["MOLECULE"]))
    assert edge["status"] == "created", edge

    async def widen(headers):
        return await client.post(
            f"{API}/templates/{edge['id']}/endpoints", headers=headers, params={"namespace": "wip"},
            json={"add_source_templates": [], "add_target_templates": ["xns-closed:XNS_TARGET"]},
        )

    refused = await widen(WRITER)
    assert refused.status_code in (400, 404, 422), refused.text
    missing = await client.post(
        f"{API}/templates/{edge['id']}/endpoints", headers=WRITER, params={"namespace": "wip"},
        json={"add_source_templates": [], "add_target_templates": ["xns-closed:XNS_ABSENT"]},
    )
    assert refused.text == missing.text.replace("XNS_ABSENT", "XNS_TARGET")

    widened = await widen(READER)
    assert widened.status_code == 200, widened.text
