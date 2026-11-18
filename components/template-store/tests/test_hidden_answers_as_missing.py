"""A template in a namespace the caller cannot read answers exactly as an id
that names nothing — same status, same body.

The id-keyed doors read the template, then checked read on its namespace,
answering "Namespace not found" while a missing id answered "Template not
found"; doors whose miss is a 200 (children, descendants, /validate)
answered a hidden id with a 404 instead; a bulk write aborted the whole
request on one hidden item. Now each answers what an unknown id gets on
the same request: with an app's single-namespace key, resolve_or_404's 404
("Could not resolve ..."), since ids resolve in that namespace; with the
admin key and no namespace, the door's own miss, since ids pass through.
The app key runs unpatched; the admin key has its permission patched.

The same comparison runs on a third kind of id: a Registry entry whose rows are
gone (a ghost). It resolves and has nothing behind it, so the door used to
answer its own miss ("Template not found", an empty list, a not_found
validation) while an id nobody ever issued answered the resolving one.
"""

import contextlib
import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from template_store.models.template import Template
from wip_auth.resolve import EntityNotFoundError, resolve_entity_id

API = "/api/template-store/templates"
APP_KEY = {"X-API-Key": "test_xns_writer_key"}  # 'wip' only; 'xns-closed' is invisible
MODES = {"app-key": ("xns-closed", False), "admin-unresolved": ("test-ns", True)}
GHOST_NS = "wip"  # writable by APP_KEY: only the rows are missing


async def _template(client: AsyncClient, auth_headers: dict, namespace: str) -> str:
    value = f"HID_{uuid.uuid4().hex[:8].upper()}"
    r = await client.post(API, headers=auth_headers, json=[{
        "namespace": namespace, "value": value, "label": value, "identity_fields": ["code"],
        "fields": [{"name": "code", "label": "Code", "type": "string", "mandatory": True}],
    }])
    result = r.json()["results"][0]
    assert result["status"] == "created", result
    return result["id"]


def _hidden(namespace: str, patched: bool):
    if not patched:
        return contextlib.nullcontext()

    async def permission(identity, ns):
        return "none" if ns == namespace else "admin"
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


async def _call(client, headers, door: str, tid: str, doors: dict | None = None):
    method, path, body = (doors or DOORS)[door]
    kwargs = {} if body is None else {"json": _fill(body, tid)}
    return await client.request(method, API + path.format(tid=tid), headers=headers, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize("door", list(DOORS))
async def test_a_hidden_id_answers_as_a_missing_one(
    client: AsyncClient, auth_headers: dict, door: str, mode: str,
):
    namespace, patched = MODES[mode]
    headers = auth_headers if patched else APP_KEY
    hidden_id = await _template(client, auth_headers, namespace)
    missing_id = str(uuid.uuid4())
    with _hidden(namespace, patched):
        hidden = await _call(client, headers, door, hidden_id)
        missing = await _call(client, headers, door, missing_id)
    assert missing.status_code not in (401, 403), missing.text  # the key is in effect
    if mode == "app-key" and door == "get":
        assert "Could not resolve" in missing.text, missing.text  # the resolving path
    assert hidden.status_code == missing.status_code, (hidden.text, missing.text)
    assert hidden.text.replace(hidden_id, "<id>") == missing.text.replace(missing_id, "<id>")


@pytest.mark.asyncio
async def test_a_hidden_item_does_not_abort_the_rest_of_the_batch(client: AsyncClient, auth_headers: dict):
    hidden_id = await _template(client, auth_headers, "xns-closed")
    visible_id = await _template(client, auth_headers, "wip")
    with _hidden("xns-closed", False):
        r = await client.put(API, headers=APP_KEY, json=[
            {"template_id": hidden_id, "label": "x"}, {"template_id": visible_id, "label": "renamed"},
        ])
    assert r.status_code == 200, r.text
    hidden_row, visible_row = r.json()["results"]
    assert (hidden_row["error_code"], hidden_row["error"]) == ("not_found", "Template not found")
    assert visible_row["status"] == "updated", visible_row


# The ghost doors name their namespace; these three need it as a query parameter.
GHOST_DOORS = {
    **DOORS,
    "activate": ("POST", "/{tid}/activate?namespace=wip", None),
    "reactivate": ("POST", "/{tid}/reactivate?namespace=wip&version=1", None),
    "endpoints": ("POST", "/{tid}/endpoints?namespace=wip", {}),
}
# Doors whose id goes through resolve_or_404, so an unknown one gets the
# resolving 404. The bulk doors report a per-item not_found and /versions
# resolves nothing at all.
RESOLVING_GHOST_DOORS = [d for d in GHOST_DOORS if d not in ("versions", "update", "delete")]


async def _ghost_template(client: AsyncClient, auth_headers: dict) -> str:
    """A template whose Registry entry outlives every row of it."""
    template_id = await _template(client, auth_headers, GHOST_NS)
    await Template.find({"template_id": template_id}).delete()
    assert await _resolves(template_id), f"{template_id} must still resolve"
    return template_id


async def _resolves(template_id: str) -> bool:
    """Whether the Registry answers for this id in 'wip', as the doors ask it."""
    try:
        await resolve_entity_id(template_id, "template", "wip")
    except EntityNotFoundError:
        return False
    return True


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize("door", list(GHOST_DOORS))
async def test_an_id_whose_rows_are_gone_answers_as_a_missing_one(
    client: AsyncClient, auth_headers: dict, door: str, mode: str,
):
    headers = auth_headers if mode == "admin-unresolved" else APP_KEY
    ghost_id = await _ghost_template(client, auth_headers)
    missing_id = str(uuid.uuid4())
    assert not await _resolves(missing_id), "the comparison id must not resolve"

    ghost = await _call(client, headers, door, ghost_id, GHOST_DOORS)
    missing = await _call(client, headers, door, missing_id, GHOST_DOORS)

    assert missing.status_code not in (401, 403), missing.text  # the key is in effect
    if mode == "app-key" and door in RESOLVING_GHOST_DOORS:
        assert "Could not resolve" in missing.text, missing.text  # the resolving path
    assert ghost.status_code == missing.status_code, (ghost.text, missing.text)
    assert ghost.text.replace(ghost_id, "<id>") == missing.text.replace(missing_id, "<id>")


# A qualified id is resolved by its own prefix, whatever namespace the key
# gives: a hidden one resolves to its canonical id, an unknown one passes
# through as submitted. Each door must still answer both alike, for a key with
# several namespaces (which derives none, so the bulk doors skip resolution)
# and for a single-namespace app key (which derives one, so they resolve).
QUALIFIED_KEYS = {
    "multi-namespace": {"X-API-Key": "test_xns_reader_key"},  # 'wip' and 'xns-closed'
    "single-namespace": APP_KEY,  # 'wip' only
}
QUALIFIED_NS = "test-ns"  # readable by neither key


@pytest.mark.asyncio
@pytest.mark.parametrize("key", list(QUALIFIED_KEYS))
@pytest.mark.parametrize("door", list(DOORS))
async def test_a_hidden_qualified_id_answers_as_an_unknown_one(
    client: AsyncClient, auth_headers: dict, door: str, key: str,
):
    hidden_tid = await _template(client, auth_headers, QUALIFIED_NS)
    hidden_value = (await client.get(f"{API}/{hidden_tid}", headers=auth_headers)).json()["value"]
    hidden_id = f"{QUALIFIED_NS}:{hidden_value}"
    missing_id = f"{QUALIFIED_NS}:NOPE_{uuid.uuid4().hex[:8].upper()}"

    hidden = await _call(client, QUALIFIED_KEYS[key], door, hidden_id)
    missing = await _call(client, QUALIFIED_KEYS[key], door, missing_id)

    assert missing.status_code not in (401, 403), missing.text  # the key is in effect
    assert hidden_tid not in hidden.text, hidden.text  # the canonical id is not given away
    assert hidden.status_code == missing.status_code, (hidden.text, missing.text)
    assert hidden.text.replace(hidden_id, "<id>") == missing.text.replace(missing_id, "<id>")
