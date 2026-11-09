"""A template in a namespace the caller cannot read answers exactly as an id
that names nothing — same status, same body.

The id-keyed doors read the template, then checked read on its namespace,
answering "Namespace not found" while a missing id answered "Template not
found"; doors whose miss is a 200 (children, descendants, /validate)
answered a hidden id with a 404 instead; a bulk write aborted the whole
request on one hidden item. Now each answers its own miss.
"""

import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient

API = "/api/template-store/templates"
HIDDEN_NS = "test-ns"


async def _template(client: AsyncClient, auth_headers: dict, namespace: str) -> str:
    value = f"HID_{uuid.uuid4().hex[:8].upper()}"
    r = await client.post(API, headers=auth_headers, json=[{
        "namespace": namespace, "value": value, "label": value, "identity_fields": ["code"],
        "fields": [{"name": "code", "label": "Code", "type": "string", "mandatory": True}],
    }])
    result = r.json()["results"][0]
    assert result["status"] == "created", result
    return result["id"]


def _hidden():
    async def permission(identity, namespace):
        return "none" if namespace == HIDDEN_NS else "admin"
    return patch("wip_auth.permissions.resolve_permission", side_effect=permission)


DOORS = {
    "get": ("GET", "/{tid}", None),
    "raw": ("GET", "/{tid}/raw", None),
    "versions": ("GET", "/{tid}/versions", None),
    "dependencies": ("GET", "/{tid}/dependencies", None),
    "validate": ("POST", "/{tid}/validate", {}),
    "children": ("GET", "/{tid}/children", None),
    "descendants": ("GET", "/{tid}/descendants", None),
    "cascade": ("POST", "/{tid}/cascade", None),
    "update": ("PUT", "", [{"template_id": "{tid}", "label": "x"}]),
    "delete": ("DELETE", "", [{"id": "{tid}", "version": 1}]),
}


def _fill(value, tid: str):
    if isinstance(value, str):
        return value.format(tid=tid)
    if isinstance(value, list):
        return [_fill(v, tid) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, tid) for k, v in value.items()}
    return value


async def _call(client, auth_headers, door: str, tid: str):
    method, path, body = DOORS[door]
    kwargs = {} if body is None else {"json": _fill(body, tid)}
    return await client.request(method, API + path.format(tid=tid), headers=auth_headers, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("door", list(DOORS))
async def test_a_hidden_id_answers_as_a_missing_one(client: AsyncClient, auth_headers: dict, door: str):
    hidden_id = await _template(client, auth_headers, HIDDEN_NS)
    missing_id = str(uuid.uuid4())
    with _hidden():
        hidden = await _call(client, auth_headers, door, hidden_id)
        missing = await _call(client, auth_headers, door, missing_id)
    assert hidden.status_code == missing.status_code, (hidden.text, missing.text)
    assert hidden.text.replace(hidden_id, "<id>") == missing.text.replace(missing_id, "<id>")


@pytest.mark.asyncio
async def test_a_hidden_item_does_not_abort_the_rest_of_the_batch(client: AsyncClient, auth_headers: dict):
    hidden_id = await _template(client, auth_headers, HIDDEN_NS)
    visible_id = await _template(client, auth_headers, "wip")
    with _hidden():
        r = await client.put(API, headers=auth_headers, json=[
            {"template_id": hidden_id, "label": "x"}, {"template_id": visible_id, "label": "renamed"},
        ])
    assert r.status_code == 200, r.text
    hidden_row, visible_row = r.json()["results"]
    assert (hidden_row["error_code"], hidden_row["error"]) == ("not_found", "Template not found")
    assert visible_row["status"] == "updated", visible_row
