"""Re-sending a template in another addressing form is not a change.

Change detection compared fields byte for byte, and ``extends`` as a raw
string, before references were normalized. A PUT repeating the stored fields
with a terminology named by value (stored as its id), or a re-post naming the
parent by value (as the portable projection serves it), minted a version
that differed in nothing. References now compare by what they resolve to.

A reporting-only change is made in place, without a version; it was
reported "unchanged" although the template changed. It is "updated", with
is_new_version false.
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


async def _put(client, headers, template_id: str, **changes) -> dict:
    resp = await client.put(API, headers=headers, params={"namespace": "wip"},
                            json=[{"template_id": template_id, **changes}])
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


def _fields() -> list[dict]:
    return [{"name": "code", "type": "string", "label": "Code", "mandatory": True},
            {"name": "status", "type": "term", "label": "Status", "terminology_ref": "DOC_STATUS"}]


async def _versions(template_id: str) -> int:
    return await Template.find({"template_id": template_id}).count()


@pytest.mark.asyncio
async def test_a_put_repeating_value_form_references_is_unchanged(client: AsyncClient, auth_headers):
    item = await _post(client, auth_headers, {
        "namespace": "wip", "value": "C1167S_PUT", "label": "Put", "fields": _fields()})
    stored = await Template.find_one({"template_id": item["id"]})
    assert stored.fields[1].terminology_ref != "DOC_STATUS"  # stored canonical
    result = await _put(client, auth_headers, item["id"], fields=_fields())
    assert result["status"] == "unchanged", result
    assert await _versions(item["id"]) == 1


@pytest.mark.asyncio
async def test_a_repost_naming_the_parent_by_value_is_unchanged(client: AsyncClient, auth_headers):
    await _post(client, auth_headers, {
        "namespace": "wip", "value": "C1167S_PARENT", "label": "Parent",
        "fields": [{"name": "a", "type": "string", "label": "A"}]})
    child = {"namespace": "wip", "value": "C1167S_CHILD", "label": "Child",
             "extends": "C1167S_PARENT", "extends_version": 1,
             "fields": [{"name": "b", "type": "string", "label": "B"}]}
    created = await _post(client, auth_headers, child)
    assert created["status"] == "created", created
    again = await _post(client, auth_headers, child)
    assert again["status"] == "unchanged", again
    assert await _versions(created["id"]) == 1


@pytest.mark.asyncio
async def test_a_real_change_still_versions(client: AsyncClient, auth_headers):
    item = await _post(client, auth_headers, {
        "namespace": "wip", "value": "C1167S_REAL", "label": "Real", "fields": _fields()})
    fields = _fields()
    fields[0]["label"] = "Code (renamed)"
    result = await _put(client, auth_headers, item["id"], fields=fields)
    assert result["status"] == "updated" and result["is_new_version"], result


@pytest.mark.asyncio
async def test_a_reporting_change_is_reported_as_one(client: AsyncClient, auth_headers):
    item = await _post(client, auth_headers, {
        "namespace": "wip", "value": "C1167S_REP", "label": "Rep", "fields": _fields()})
    result = await _put(client, auth_headers, item["id"],
                        reporting={"sync_enabled": True, "sync_strategy": "all_versions"})
    assert result["status"] == "updated", result
    assert result["is_new_version"] is False, result
    assert await _versions(item["id"]) == 1
    stored = await Template.find_one({"template_id": item["id"]})
    assert stored.reporting.sync_strategy == "all_versions"
