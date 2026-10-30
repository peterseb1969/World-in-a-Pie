"""A refused template reference is indistinguishable from a missing one.

'xns-closed' is closed to inbound references (the default), and only the
reader key can read it. For the writer key, a template reference into it must
produce exactly what a reference to a template that does not exist produces:
the same error code, message, details, and the same choice of which reference
is reported when several fail. Otherwise a writer could test whether an id
exists in a namespace it cannot read. Each test compares the full result of
the two cases, with the differing ids masked.
"""

import json

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
ABSENT = "0190c999-0000-7000-0000-00000000d999"
ABSENT_2 = "0190c999-0000-7000-0000-00000000d998"


def _masked(item: object, *ids: str) -> str:
    """The result as raw JSON (key order included), with ids masked."""
    text = json.dumps(item)
    for value in ids:
        text = text.replace(value, "<ID>")
    return text


@pytest.fixture
async def closed_id(client: AsyncClient, auth_headers: dict) -> str:
    """A template in the closed namespace."""
    await _ensure_endpoint_templates(client, auth_headers, "EXPERIMENT", "MOLECULE")
    target = _entity_template("XNS_PARITY_TARGET")
    target["namespace"] = "xns-closed"
    item = await _post_template(client, auth_headers, target)
    assert item["status"] in ("created", "unchanged"), item
    return item["id"]


def _with_refs(value: str, *refs: str, **extra: object) -> dict:
    """A template in 'wip' with one nested-object field per reference."""
    template = _entity_template(value)
    template["fields"] += [
        {"name": f"f{i}", "type": "object", "label": f"F{i}",
         "template_ref": ref, "template_ref_version": 1}
        for i, ref in enumerate(refs)
    ]
    template.update(extra)
    return template


@pytest.mark.asyncio
async def test_a_refused_template_ref_reads_as_missing(client: AsyncClient, closed_id: str) -> None:
    refused = await _post_template(client, WRITER, _with_refs("XNS_P_ONE", closed_id))
    missing = await _post_template(client, WRITER, _with_refs("XNS_P_ONE", ABSENT))
    assert refused["status"] == "error", refused
    assert _masked(refused, closed_id) == _masked(missing, ABSENT)
    assert "xns-closed" not in json.dumps(refused)
    # The reader may reference it.
    assert (await _post_template(client, READER, _with_refs("XNS_P_ONE", closed_id)))["status"] == "created"


@pytest.mark.asyncio
async def test_the_first_unresolved_reference_in_field_order_is_reported(
    client: AsyncClient, closed_id: str,
) -> None:
    """Refusing after resolution leaked here: beside a missing reference, a
    refused one resolved, so only the missing one could be reported."""
    refused = await _post_template(client, WRITER, _with_refs("XNS_P_TWO", closed_id, ABSENT))
    missing = await _post_template(client, WRITER, _with_refs("XNS_P_TWO", ABSENT_2, ABSENT))
    assert "<ID>" in _masked(refused, closed_id), refused
    assert _masked(refused, closed_id) == _masked(missing, ABSENT_2)

    refused = await _post_template(client, WRITER, _with_refs("XNS_P_TWO", ABSENT, closed_id))
    missing = await _post_template(client, WRITER, _with_refs("XNS_P_TWO", ABSENT, ABSENT_2))
    assert ABSENT in refused["error"], refused
    assert _masked(refused, closed_id) == _masked(missing, ABSENT_2)


@pytest.mark.asyncio
async def test_a_refused_parent_reads_as_missing(client: AsyncClient, closed_id: str) -> None:
    def child(parent: str) -> dict:
        return _with_refs("XNS_P_CHILD", extends=parent, extends_version=1)

    refused = await _post_template(client, WRITER, child(closed_id))
    missing = await _post_template(client, WRITER, child(ABSENT))
    assert refused["status"] == "error", refused
    assert _masked(refused, closed_id) == _masked(missing, ABSENT)


@pytest.mark.asyncio
async def test_a_refused_edge_endpoint_reads_as_missing(client: AsyncClient, closed_id: str) -> None:
    def edge(target: str) -> dict:
        return _relationship_template(
            value="EDGE_XNS_PARITY", source_templates=["EXPERIMENT"],
            target_templates=[target], target_ref_targets=[],
        )

    refused = await _post_template(client, WRITER, edge(closed_id))
    missing = await _post_template(client, WRITER, edge(ABSENT))
    assert refused["status"] == "error", refused
    assert _masked(refused, closed_id) == _masked(missing, ABSENT)


@pytest.mark.asyncio
async def test_widening_to_a_refused_endpoint_reads_as_missing(
    client: AsyncClient, closed_id: str,
) -> None:
    edge = await _post_template(client, READER, _relationship_template(
        value="EDGE_XNS_PARITY_WIDEN", source_templates=["EXPERIMENT"], target_templates=["MOLECULE"]))
    assert edge["status"] == "created", edge

    async def widen(target: str) -> tuple[int, str]:
        resp = await client.post(
            f"{API}/templates/{edge['id']}/endpoints", headers=WRITER, params={"namespace": "wip"},
            json={"add_source_templates": [], "add_target_templates": [target]},
        )
        return resp.status_code, resp.text

    refused, missing = await widen(closed_id), await widen(ABSENT)
    assert refused[0] != 200, refused
    assert (refused[0], _masked(refused[1], closed_id)) == (missing[0], _masked(missing[1], ABSENT))


@pytest.mark.asyncio
async def test_activation_reports_a_refused_reference_as_missing(
    client: AsyncClient, closed_id: str,
) -> None:
    """Drafts skip resolution at create; activation validates them, and
    must not tell a refused reference from a missing one either."""
    async def activate(value: str, ref: str) -> str:
        draft = await _post_template(client, WRITER, _with_refs(value, ref, status="draft"))
        assert draft["status"] == "created", draft
        resp = await client.post(
            f"{API}/templates/{draft['id']}/activate", headers=WRITER,
            params={"namespace": "wip", "dry_run": "true"},
        )
        assert resp.status_code == 200, resp.text
        return _masked(resp.json(), value)

    refused = await activate("XNS_P_DRAFT_A", closed_id)
    missing = await activate("XNS_P_DRAFT_B", ABSENT)
    assert '"errors": []' not in refused, refused
    assert refused.replace(closed_id, "<ID>") == missing.replace(ABSENT, "<ID>")


@pytest.mark.asyncio
async def test_an_update_carries_held_references_and_refuses_new_ones(
    client: AsyncClient, auth_headers: dict, closed_id: str,
) -> None:
    created = await _post_template(client, READER, _with_refs("XNS_P_UPDATE", closed_id))
    assert created["status"] == "created", created

    # A reference the template already holds stays usable to a writer who
    # cannot read its namespace.
    unrelated = _with_refs("XNS_P_UPDATE", closed_id)
    unrelated["fields"].append({"name": "note", "type": "string", "label": "Note"})
    assert (await _post_template(client, WRITER, unrelated))["status"] == "updated"

    # A new one reads exactly as a missing one.
    other = _entity_template("XNS_PARITY_OTHER")
    other["namespace"] = "xns-closed"
    other_id = (await _post_template(client, auth_headers, other))["id"]
    refused = await _post_template(client, WRITER, _with_refs("XNS_P_UPDATE", closed_id, other_id))
    missing = await _post_template(client, WRITER, _with_refs("XNS_P_UPDATE", closed_id, ABSENT))
    assert refused["status"] == "error", refused
    assert _masked(refused, other_id) == _masked(missing, ABSENT)
