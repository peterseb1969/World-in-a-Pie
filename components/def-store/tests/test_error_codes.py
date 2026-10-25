"""def-store's bulk error items carry a machine-readable error_code.

Callers branch on the code, not the English message. These pin the codes
on the paths that used to report a message only: missing entities, the
terminology-delete dependency guard, term-relation writes and term merges —
plus error_fields' mapping of every exception type a handler catches.
"""

import pytest
from fastapi import HTTPException
from httpx import AsyncClient

from def_store.services.errors import DefStoreError, error_fields
from def_store.services.registry_client import RegistryError
from def_store.services.term_names import TermNameError
from def_store.services.terminology_service import (
    EntityExistsError,
    NameCollisionError,
)

API = "/api/def-store"
MISSING_ID = "0190c000-0000-7000-0000-0000000000ff"


async def _terminology(client: AsyncClient, headers: dict, value: str) -> str:
    r = await client.post(f"{API}/terminologies", headers=headers,
                          json=[{"value": value, "label": value.title(), "namespace": "wip"}])
    assert r.json()["succeeded"] == 1, r.json()
    return r.json()["results"][0]["id"]


async def _term(client: AsyncClient, headers: dict, terminology_id: str, value: str) -> str:
    r = await client.post(f"{API}/terminologies/{terminology_id}/terms", headers=headers,
                          json=[{"value": value}])
    assert r.json()["succeeded"] == 1, r.json()
    return r.json()["results"][0]["id"]


def _only_item(response) -> dict:
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["failed"] == 1, data
    return data["results"][0]


# -- error_fields -------------------------------------------------------------


def test_error_fields_codes_every_handled_exception_type():
    assert error_fields(DefStoreError("gone", "not_found"))["error_code"] == "not_found"
    rule = error_fields(DefStoreError("no", "invalid_state", rule="has_terms"))
    assert rule["details"] == {"rule": "has_terms"}
    assert error_fields(EntityExistsError("dup", "id-1", []))["error_code"] == "already_exists"
    collision = error_fields(NameCollisionError([{"name": "a", "conflicts_with_value": "b"}]))
    assert collision["error_code"] == "name_collision"
    assert collision["details"]["name_collisions"][0]["name"] == "a"
    assert error_fields(TermNameError("bad"))["error_code"] == "invalid_names"
    registry = error_fields(RegistryError("down"))
    assert registry == {"error": "Registry error: down", "error_code": "registry_error"}
    assert error_fields(HTTPException(404, "nope"))["error_code"] == "not_found"
    assert error_fields(ValueError("odd"))["error_code"] == "invalid_request"
    assert error_fields(RuntimeError("boom"))["error_code"] == "internal_error"


# -- terminologies ------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_missing_terminology_is_not_found(client, auth_headers):
    r = await client.put(f"{API}/terminologies", headers=auth_headers,
                         json=[{"terminology_id": MISSING_ID, "label": "X"}])
    assert _only_item(r)["error_code"] == "not_found"


@pytest.mark.asyncio
async def test_delete_guard_codes(client, auth_headers, template_store):
    tid = await _terminology(client, auth_headers, "codes_guard")
    template_store.templates = [{
        "template_id": "0190c000-0000-7000-0000-000000000001", "value": "probe",
        "label": "Probe", "status": "active",
        "fields": [{"name": "color", "type": "term", "terminology_ref": tid}],
    }]
    r = await client.request("DELETE", f"{API}/terminologies", headers=auth_headers,
                             json=[{"id": tid}])
    item = _only_item(r)
    assert item["error_code"] == "invalid_state"
    assert item["details"] == {"rule": "has_templates"}

    template_store.templates = []
    template_store.unreachable = True
    r = await client.request("DELETE", f"{API}/terminologies", headers=auth_headers,
                             json=[{"id": tid}])
    template_store.unreachable = False
    item = _only_item(r)
    assert item["error_code"] == "invalid_state"
    assert item["details"] == {"rule": "dependencies_unverifiable"}


# -- terms --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_term_in_missing_terminology_is_404(client, auth_headers):
    """The single-item create path maps the not_found code to HTTP 404 —
    by the code, no longer by searching the message for 'not found'."""
    r = await client.post(f"{API}/terminologies/{MISSING_ID}/terms", headers=auth_headers,
                          json=[{"value": "x"}])
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_update_missing_term_is_not_found(client, auth_headers):
    r = await client.put(f"{API}/terms", headers=auth_headers,
                         json=[{"term_id": MISSING_ID, "label": "X"}])
    assert _only_item(r)["error_code"] == "not_found"


@pytest.mark.asyncio
async def test_merge_codes(client, auth_headers):
    tid = await _terminology(client, auth_headers, "codes_merge")
    a = await _term(client, auth_headers, tid, "alpha")
    r = await client.post(f"{API}/terms/merge", headers=auth_headers,
                          json=[{"survivor_term_id": a, "absorbed_term_id": a}])
    assert _only_item(r)["error_code"] == "invalid_request"

    r = await client.post(f"{API}/terms/merge", headers=auth_headers,
                          json=[{"survivor_term_id": a, "absorbed_term_id": MISSING_ID}])
    assert _only_item(r)["error_code"] == "not_found"


# -- term relations -----------------------------------------------------------


@pytest.mark.asyncio
async def test_relation_codes(client, auth_headers):
    tid = await _terminology(client, auth_headers, "codes_relations")
    a = await _term(client, auth_headers, tid, "alpha")
    b = await _term(client, auth_headers, tid, "beta")
    url, params = f"{API}/ontology/term-relations", {"namespace": "wip"}

    async def create(source: str, target: str, relation_type: str = "is_a") -> dict:
        r = await client.post(url, headers=auth_headers, params=params, json=[{
            "source_term_id": source, "target_term_id": target, "relation_type": relation_type,
        }])
        return _only_item(r)

    item = await create(a, b, "no_such_relation")
    assert (item["error_code"], item["details"]["rule"]) == ("validation_failed", "unknown_relation_type")
    item = await create(a, MISSING_ID)
    assert (item["error_code"], item["details"]["rule"]) == ("reference_violation", "target_term_not_found")
    item = await create(a, a)
    assert (item["error_code"], item["details"]["rule"]) == ("validation_failed", "self_relation")

    r = await client.request("DELETE", url, headers=auth_headers, params=params, json=[{
        "source_term_id": a, "target_term_id": b, "relation_type": "is_a",
    }])
    assert _only_item(r)["error_code"] == "not_found"
