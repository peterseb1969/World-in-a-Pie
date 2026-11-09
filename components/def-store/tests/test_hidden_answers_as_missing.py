"""An entity in a namespace the caller cannot read answers exactly as an id
that names nothing — same status, same body.

The id-keyed doors read the entity, then checked read on its namespace,
answering "Namespace not found" for an unreadable one while a missing id
answered the entity's own text ("Terminology not found"): the difference
confirmed that the id exists, and where. Doors whose miss is not a 404 (an
empty audit page, a not_found validation, a per-item bulk error) leaked
through the status alone; a bulk write aborted the whole request on one
hidden item. Now each answers what an unknown id gets on the same request.

Two callers, because the miss differs between them:
- an app's key, scoped to one namespace: resolve_or_404 resolves ids in
  that namespace, so an unknown id is its 404 ("Could not resolve ...");
  a hidden id resolves (a canonical id names one entity wherever it lives),
  so the door must answer that same 404 itself;
- the admin key with no namespace: ids pass through unresolved, and the
  door's own miss ("Terminology not found", an empty page) is the answer.
The app key runs unpatched; the admin key has its permission patched.
"""

import contextlib
import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient

API = "/api/def-store"
APP_KEY = {"X-API-Key": "test_xns_writer_key"}  # 'wip' only; 'xns-closed' is invisible
MODES = {
    # mode: (hidden namespace, whether the admin's permission is patched)
    "app-key": ("xns-closed", False),
    "admin-unresolved": ("test-ns", True),
}


def _missing_id() -> str:
    return str(uuid.uuid4())


async def _entities(client: AsyncClient, auth_headers: dict, namespace: str) -> tuple[str, str, str]:
    """A terminology with two terms in the namespace that will be hidden."""
    value = f"HID_{uuid.uuid4().hex[:8].upper()}"
    r = await client.post(f"{API}/terminologies", headers=auth_headers,
                          json=[{"value": value, "label": value, "namespace": namespace}])
    tid = r.json()["results"][0]["id"]
    r = await client.post(f"{API}/terminologies/{tid}/terms", headers=auth_headers,
                          json=[{"value": "a"}, {"value": "b"}])
    a, b = (row["id"] for row in r.json()["results"])
    return tid, a, b


def _hidden(namespace: str, patched: bool):
    if not patched:
        return contextlib.nullcontext()

    async def permission(identity, ns):
        return "none" if ns == namespace else "admin"
    return patch("wip_auth.permissions.resolve_permission", side_effect=permission)


DOORS = {
    "list terms": ("GET", "/terminologies/{tid}/terms", None),
    "get terminology": ("GET", "/terminologies/{tid}", None),
    "terminology dependencies": ("GET", "/terminologies/{tid}/dependencies", None),
    "export": ("GET", "/import-export/export/{tid}", None),
    "get term": ("GET", "/terms/{term}", None),
    "term audit": ("GET", "/audit/terms/{term}", None),
    "terminology audit": ("GET", "/audit/terminologies/{tid}", None),
    "restore": ("POST", "/terminologies/{tid}/restore", None),
    "create terms": ("POST", "/terminologies/{tid}/terms", [{"value": "c"}]),
    "validate": ("POST", "/validate", {"terminology_id": "{tid}", "value": "a"}),
    "bulk validate": ("POST", "/validate/bulk", {"items": [{"terminology_id": "{tid}", "value": "a"}]}),
    "update terminologies": ("PUT", "/terminologies", [{"terminology_id": "{tid}", "label": "x"}]),
    "delete terminologies": ("DELETE", "/terminologies", [{"id": "{tid}"}]),
    "update terms": ("PUT", "/terms", [{"term_id": "{term}", "label": "x"}]),
    "deprecate terms": ("POST", "/terms/deprecate", [{"term_id": "{term}", "reason": "x"}]),
    "delete terms": ("DELETE", "/terms", [{"id": "{term}"}]),
    "merge terms": ("POST", "/terms/merge", [{"survivor_term_id": "{term}", "absorbed_term_id": "{other}"}]),
}


def _fill(value, ids: dict[str, str]):
    if isinstance(value, str):
        return value.format(**ids)
    if isinstance(value, list):
        return [_fill(v, ids) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, ids) for k, v in value.items()}
    return value


async def _call(client, headers, door: str, ids: dict[str, str]):
    method, path, body = DOORS[door]
    kwargs = {} if body is None else {"json": _fill(body, ids)}
    return await client.request(method, API + path.format(**ids), headers=headers, **kwargs)


def _blank(text: str, ids: dict[str, str]) -> str:
    for name, value in ids.items():
        text = text.replace(value, f"<{name}>")
    return text


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize("door", list(DOORS))
async def test_a_hidden_id_answers_as_a_missing_one(
    client: AsyncClient, auth_headers: dict, door: str, mode: str,
):
    namespace, patched = MODES[mode]
    headers = auth_headers if patched else APP_KEY
    tid, a, b = await _entities(client, auth_headers, namespace)
    hidden_ids = {"tid": tid, "term": a, "other": b}
    missing_ids = {"tid": _missing_id(), "term": _missing_id(), "other": _missing_id()}

    with _hidden(namespace, patched):
        hidden = await _call(client, headers, door, hidden_ids)
        missing = await _call(client, headers, door, missing_ids)

    assert missing.status_code not in (401, 403), missing.text  # the key is in effect
    if mode == "app-key" and door == "get terminology":
        # the bed reaches the resolving path, not the pass-through one
        assert "Could not resolve" in missing.text, missing.text
    assert hidden.status_code == missing.status_code, (hidden.text, missing.text)
    assert _blank(hidden.text, hidden_ids) == _blank(missing.text, missing_ids)


@pytest.mark.asyncio
async def test_a_hidden_item_does_not_abort_the_rest_of_the_batch(client: AsyncClient, auth_headers: dict):
    tid, _a, _b = await _entities(client, auth_headers, "xns-closed")
    value = f"VIS_{uuid.uuid4().hex[:8].upper()}"
    r = await client.post(f"{API}/terminologies", headers=auth_headers,
                          json=[{"value": value, "label": value, "namespace": "wip"}])
    visible = r.json()["results"][0]["id"]

    with _hidden("xns-closed", False):
        r = await client.put(f"{API}/terminologies", headers=APP_KEY, json=[
            {"terminology_id": tid, "label": "x"}, {"terminology_id": visible, "label": "renamed"},
        ])
    assert r.status_code == 200, r.text
    hidden_row, visible_row = r.json()["results"]
    assert hidden_row["error_code"] == "not_found"
    assert hidden_row["error"] == "Terminology not found"
    assert visible_row["status"] == "updated"
