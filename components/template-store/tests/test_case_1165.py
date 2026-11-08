"""A cascade reaches exactly the parent's own children, as the caller may change
them, and writes each one whole.

``cascade_to_children`` found the parent's versions and the children by the
parent's bare value, in every namespace, kept one child per bare value, numbered
the new version across namespaces, never asked for write on the children's
namespaces, and rebuilt each child from a partial copy (usage, endpoints,
versioned, header_fields and renames reset to their defaults). Now the children
are the templates whose ``extends`` names the parent's id, one result per child
template, numbered within it; a child in a namespace the caller can read but not
write is refused per child, one it cannot see is left alone and unreported, and
the new version is the child with only its version, extends pin and stamps
changed.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from template_store.models.template import Template

API = "/api/template-store/templates"
READER = {"X-API-Key": "test_xns_reader_key"}  # write on wip; read on xns-closed
WRITER = {"X-API-Key": "test_xns_writer_key"}  # write on wip; xns-closed invisible
FIELD = {"name": "id", "type": "string", "label": "Id", "mandatory": True}


async def _post(client, headers, payload) -> dict:
    resp = await client.post(API, headers=headers, json=[payload])
    assert resp.status_code == 200, resp.text
    item = resp.json()["results"][0]
    assert item["status"] in ("created", "updated"), item
    return item


async def _parent(client, headers, ns: str, value: str) -> str:
    return (await _post(client, headers, {
        "namespace": ns, "value": value, "label": "v1", "fields": [FIELD],
    }))["id"]


async def _child(client, headers, ns: str, value: str, extends: str, **extra) -> str:
    return (await _post(client, headers, {
        "namespace": ns, "value": value, "label": value, "extends": extends,
        "extends_version": 1,
        "fields": [{"name": "note", "type": "string", "label": "Note"}], **extra,
    }))["id"]


async def _bump(client, headers, ns: str, value: str) -> None:
    await _post(client, headers, {"namespace": ns, "value": value, "label": "v2", "fields": [FIELD]})


async def _cascade(client, headers, parent_id: str) -> dict:
    resp = await client.post(f"{API}/{parent_id}/cascade", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _latest(template_id: str) -> Template:
    rows = await Template.find({"template_id": template_id}).to_list()
    return max(rows, key=lambda t: t.version)


@pytest.mark.asyncio
async def test_a_same_named_parent_elsewhere_is_not_a_parent(client: AsyncClient, auth_headers):
    wip_base = await _parent(client, auth_headers, "wip", "C1165_BASE")
    other_base = await _parent(client, auth_headers, "test-ns", "C1165_BASE")
    wip_child = await _child(client, auth_headers, "wip", "C1165_WCHILD", "C1165_BASE")
    other_child = await _child(client, auth_headers, "test-ns", "C1165_OCHILD", "C1165_BASE")
    await _bump(client, auth_headers, "test-ns", "C1165_BASE")

    body = await _cascade(client, auth_headers, other_base)

    assert [r["old_template_id"] for r in body["results"]] == [other_child]
    untouched = await _latest(wip_child)
    assert (untouched.version, untouched.extends, untouched.extends_version) == (1, wip_base, 1)
    moved = await _latest(other_child)
    assert (moved.version, moved.extends, moved.extends_version) == (2, other_base, 2)


@pytest.mark.asyncio
async def test_same_named_children_are_each_cascaded(client: AsyncClient, auth_headers):
    base = await _parent(client, auth_headers, "wip", "C1165_SHARED_BASE")
    in_wip = await _child(client, auth_headers, "wip", "C1165_SAME", "C1165_SHARED_BASE")
    in_test = await _child(client, auth_headers, "test-ns", "C1165_SAME", "wip:C1165_SHARED_BASE")
    await _bump(client, auth_headers, "wip", "C1165_SHARED_BASE")

    body = await _cascade(client, auth_headers, base)

    assert sorted(r["old_template_id"] for r in body["results"]) == sorted([in_wip, in_test])
    assert body["updated"] == 2
    for child in (in_wip, in_test):
        latest = await _latest(child)
        assert (latest.version, latest.extends_version) == (2, 2)


@pytest.mark.asyncio
async def test_children_the_caller_may_not_write_are_left_alone(client: AsyncClient, auth_headers):
    base = await _parent(client, auth_headers, "wip", "C1165_GATE_BASE")
    closed_child = await _child(client, auth_headers, "xns-closed", "C1165_CLOSED",
                                "wip:C1165_GATE_BASE")
    own_child = await _child(client, auth_headers, "wip", "C1165_OWN", "C1165_GATE_BASE")
    await _bump(client, auth_headers, "wip", "C1165_GATE_BASE")

    readable = await _cascade(client, READER, base)
    rows = {r["old_template_id"]: r for r in readable["results"]}
    assert rows[closed_child]["status"] == "error"
    assert rows[closed_child]["error_code"] == "forbidden"
    assert rows[own_child]["status"] == "updated"
    assert (await _latest(closed_child)).version == 1

    hidden = await _cascade(client, WRITER, base)
    assert closed_child not in {r["old_template_id"] for r in hidden["results"]}
    assert (await _latest(closed_child)).version == 1


@pytest.mark.asyncio
async def test_the_new_version_is_the_whole_child(client: AsyncClient, auth_headers):
    base = await _parent(client, auth_headers, "wip", "C1165_WHOLE_BASE")
    child = await _child(
        client, auth_headers, "wip", "C1165_WHOLE", "C1165_WHOLE_BASE",
        identity_fields=["note"], versioned=False, header_fields=["note"],
    )
    await _bump(client, auth_headers, "wip", "C1165_WHOLE_BASE")

    await _cascade(client, auth_headers, base)

    before = await Template.find_one({"template_id": child, "version": 1})
    after = await _latest(child)
    assert after.version == 2
    assert (after.versioned, after.header_fields, after.identity_fields) == (
        before.versioned, before.header_fields, before.identity_fields,
    ) == (False, ["note"], ["note"])
    assert after.usage == before.usage
    assert after.extends_version == 2


@pytest.mark.asyncio
async def test_a_child_already_on_the_parent_version_is_unchanged(client: AsyncClient, auth_headers):
    base = await _parent(client, auth_headers, "wip", "C1165_PIN_BASE")
    child = await _child(client, auth_headers, "wip", "C1165_PIN", "C1165_PIN_BASE")

    body = await _cascade(client, auth_headers, base)  # parent still at v1

    assert [r["status"] for r in body["results"]] == ["unchanged"]
    assert (await _latest(child)).version == 1
