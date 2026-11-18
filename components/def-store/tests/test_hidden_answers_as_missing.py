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

The same comparison runs on a third kind of id: a Registry entry whose row is
gone (a ghost, what /maintenance/registry-ghosts reports). It resolves and has
nothing behind it, so the door used to answer its own miss while an id nobody
ever issued answered the resolving one. Only the audit doors keep a difference:
an entity whose row is gone keeps serving its history, so the ghost there is
compared with no history at all, and the history of one that has it is asserted
separately.
"""

import contextlib
import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from def_store.models.audit_log import TermAuditLog
from def_store.models.term import Term
from def_store.models.terminology import Terminology
from wip_auth.resolve import EntityNotFoundError, resolve_entity_id

API = "/api/def-store"
APP_KEY = {"X-API-Key": "test_xns_writer_key"}  # 'wip' only; 'xns-closed' is invisible
MODES = {
    # mode: (hidden namespace, whether the admin's permission is patched)
    "app-key": ("xns-closed", False),
    "admin-unresolved": ("test-ns", True),
}
GHOST_NS = "wip"  # readable and writable by APP_KEY: only the row is missing


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
    "term search": ("GET", "/terms/search?q=a&namespace=wip&terminology={tid}", None),
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


async def _ghost_ids(client: AsyncClient, auth_headers: dict, *, with_history: bool) -> dict[str, str]:
    """A terminology and two terms whose Registry entries outlive their rows.

    `RegistryEntryNotRemovedError` leaves exactly this behind, and
    /maintenance/registry-ghosts exists to find it. With with_history=false the
    audit trail goes too, so nothing of the entity remains but its identity.
    """
    tid, a, b = await _entities(client, auth_headers, GHOST_NS)
    await Terminology.find({"terminology_id": tid}).delete()
    await Term.find({"term_id": {"$in": [a, b]}}).delete()
    if not with_history:
        await TermAuditLog.find({"terminology_id": tid}).delete()
    for entity_id, entity_type in ((tid, "terminology"), (a, "term"), (b, "term")):
        assert await _resolves(entity_id, entity_type), f"{entity_id} must still resolve"
    return {"tid": tid, "term": a, "other": b}


async def _resolves(entity_id: str, entity_type: str) -> bool:
    """Whether the Registry answers for this id in 'wip', as the doors ask it."""
    try:
        await resolve_entity_id(entity_id, entity_type, "wip")
    except EntityNotFoundError:
        return False
    return True


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize("door", list(DOORS))
async def test_an_id_whose_row_is_gone_answers_as_a_missing_one(
    client: AsyncClient, auth_headers: dict, door: str, mode: str,
):
    headers = auth_headers if mode == "admin-unresolved" else APP_KEY
    ghost_ids = await _ghost_ids(client, auth_headers, with_history=False)
    missing_ids = {name: _missing_id() for name in ghost_ids}
    assert not await _resolves(missing_ids["tid"], "terminology")

    ghost = await _call(client, headers, door, ghost_ids)
    missing = await _call(client, headers, door, missing_ids)

    assert missing.status_code not in (401, 403), missing.text  # the key is in effect
    if mode == "app-key" and door in ("get terminology", "list terms", "term search"):
        assert "Could not resolve" in missing.text, missing.text  # the resolving path
    assert ghost.status_code == missing.status_code, (ghost.text, missing.text)
    assert _blank(ghost.text, ghost_ids) == _blank(missing.text, missing_ids)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["audit/terminologies/{tid}", "audit/terms/{term}"])
async def test_an_entry_whose_row_is_gone_still_answers_with_its_history(
    client: AsyncClient, auth_headers: dict, path: str,
):
    """The audit doors keep serving the trail of an entity whose row is gone —
    the namespace comes from its most recent audit row — so only an id with no
    row and no history answers as one that names nothing (tested above)."""
    ids = await _ghost_ids(client, auth_headers, with_history=True)
    r = await client.get(f"{API}/{path.format(**ids)}", headers=APP_KEY)
    assert r.status_code == 200, r.text
    assert r.json()["total"] > 0, r.text


# A qualified id (ns:VALUE for a terminology, ns:TERMINOLOGY:value for a term)
# is resolved by its own prefix, whatever namespace the key gives: a hidden one
# resolves to its canonical id, an unknown one passes through as submitted.
# Each door must still answer both alike and never name the canonical id, for
# a key with several namespaces (which derives none, so the bulk doors skip
# resolution) and for a single-namespace app key (which derives one, so they
# resolve).
QUALIFIED_KEYS = {
    "multi-namespace": {"X-API-Key": "test_xns_multi_key"},  # 'wip' and 'xns-closed'
    "single-namespace": APP_KEY,  # 'wip' only
}
QUALIFIED_NS = "test-ns"  # readable by neither key


@pytest.mark.asyncio
@pytest.mark.parametrize("key", list(QUALIFIED_KEYS))
@pytest.mark.parametrize("door", list(DOORS))
async def test_a_hidden_qualified_id_answers_as_an_unknown_one(
    client: AsyncClient, auth_headers: dict, door: str, key: str,
):
    tid, a, b = await _entities(client, auth_headers, QUALIFIED_NS)
    value = (await client.get(f"{API}/terminologies/{tid}", headers=auth_headers)).json()["value"]
    nope = f"NOPE_{uuid.uuid4().hex[:8].upper()}"
    hidden_ids = {"tid": f"{QUALIFIED_NS}:{value}",
                  "term": f"{QUALIFIED_NS}:{value}:a", "other": f"{QUALIFIED_NS}:{value}:b"}
    missing_ids = {"tid": f"{QUALIFIED_NS}:{nope}",
                   "term": f"{QUALIFIED_NS}:{nope}:a", "other": f"{QUALIFIED_NS}:{nope}:b"}

    hidden = await _call(client, QUALIFIED_KEYS[key], door, hidden_ids)
    missing = await _call(client, QUALIFIED_KEYS[key], door, missing_ids)

    assert missing.status_code not in (401, 403), missing.text  # the key is in effect
    for canonical in (tid, a, b):  # no canonical id is given away
        assert canonical not in hidden.text, hidden.text
    assert hidden.status_code == missing.status_code, (hidden.text, missing.text)
    assert _blank(hidden.text, hidden_ids) == _blank(missing.text, missing_ids)



# A pointer inside a bulk item (a parent, a replacement, a relation endpoint)
# names a hidden term by its qualified id. The pointer is refused either way;
# the refusal must read the same as for a pointer naming nothing, and never
# carry the canonical id. The item's own term is visible to the key.
POINTER_DOORS = {
    "update terms: parent": ("PUT", "/terms", [{"term_id": "{own}", "parent_term_id": "{term}"}]),
    "deprecate terms: replacement": (
        "POST", "/terms/deprecate", [{"term_id": "{own}", "replaced_by_term_id": "{term}", "reason": "x"}],
    ),
    "create terms: parent": ("POST", "/terminologies/{own_tid}/terms", [{"value": "c", "parent_term_id": "{term}"}]),
    "create relations: target": (
        "POST", "/ontology/term-relations?namespace=wip",
        [{"source_term_id": "{own}", "target_term_id": "{term}", "relation_type": "is_a"}],
    ),
    "create relations: source": (
        "POST", "/ontology/term-relations?namespace=wip",
        [{"source_term_id": "{term}", "target_term_id": "{own}", "relation_type": "is_a"}],
    ),
    "delete relations: target": (
        "DELETE", "/ontology/term-relations?namespace=wip",
        [{"source_term_id": "{own}", "target_term_id": "{term}", "relation_type": "is_a"}],
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("door", list(POINTER_DOORS))
async def test_a_hidden_term_named_by_a_pointer_answers_as_an_unknown_one(
    client: AsyncClient, auth_headers: dict, door: str,
):
    tid, a, _ = await _entities(client, auth_headers, QUALIFIED_NS)
    value = (await client.get(f"{API}/terminologies/{tid}", headers=auth_headers)).json()["value"]
    own_tid, own, _ = await _entities(client, auth_headers, "wip")
    nope = f"NOPE_{uuid.uuid4().hex[:8].upper()}"
    hidden_ids = {"own": own, "own_tid": own_tid, "term": f"{QUALIFIED_NS}:{value}:a"}
    missing_ids = {"own": own, "own_tid": own_tid, "term": f"{QUALIFIED_NS}:{nope}:a"}
    method, path, body = POINTER_DOORS[door]

    async def call(ids):
        return await client.request(method, API + path.format(**ids), headers=APP_KEY, json=_fill(body, ids))

    hidden, missing = await call(hidden_ids), await call(missing_ids)

    assert missing.status_code not in (401, 403), missing.text  # the key is in effect
    assert tid not in hidden.text and a not in hidden.text, hidden.text  # nothing given away
    assert hidden.status_code == missing.status_code, (hidden.text, missing.text)
    assert _blank(hidden.text, hidden_ids) == _blank(missing.text, missing_ids)
