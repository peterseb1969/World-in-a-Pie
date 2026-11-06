"""Every non-draft template version records which identity paths are referents.

Provenance: CASE-1090. A term- or reference-typed identity field feeds the
identity hash the canonical ID it resolves to, but a dotted path into an object
field (``address.country``) kept feeding the submitted spelling, because the
hasher looked at the top-level field only. The template store now works the
answer out when a version is written, walking nested templates at their pinned
versions, and stores it as ``referent_identity_paths``.
"""

import pytest
from httpx import AsyncClient

from template_store.models.template import Template
from template_store.services.referent_paths import backfill

URL = "/api/template-store/templates"


async def _create(client: AsyncClient, auth_headers: dict, payload: dict) -> dict:
    response = await client.post(URL, headers=auth_headers, json=[payload])
    assert response.status_code == 200
    body = response.json()
    assert body["succeeded"] == 1, body
    return body["results"][0]


async def _get(client: AsyncClient, auth_headers: dict, template_id: str, version: int = 1) -> dict:
    response = await client.get(f"{URL}/{template_id}", headers=auth_headers, params={"version": version})
    assert response.status_code == 200, response.text
    return response.json()


async def _address(client: AsyncClient, auth_headers: dict, value: str) -> str:
    created = await _create(client, auth_headers, {
        "namespace": "wip", "value": value, "label": value,
        "fields": [
            {"name": "street", "label": "Street", "type": "string"},
            {"name": "country", "label": "Country", "type": "term", "terminology_ref": "COUNTRY"},
        ],
    })
    return created["id"]


def _person(value: str, address_id: str, identity: list[str], status: str = "active") -> dict:
    return {
        "namespace": "wip", "value": value, "label": value, "status": status,
        "identity_fields": identity,
        "fields": [
            {"name": "name", "label": "Name", "type": "string"},
            {"name": "gender", "label": "Gender", "type": "term", "terminology_ref": "GENDER"},
            {"name": "address", "label": "Address", "type": "object",
             "template_ref": address_id, "template_ref_version": 1},
        ],
    }


@pytest.mark.asyncio
async def test_nested_term_identity_path_is_recorded(client: AsyncClient, auth_headers: dict):
    address = await _address(client, auth_headers, "ADDR_1090_A")
    person = await _create(client, auth_headers, _person(
        "PERSON_1090_A", address, ["name", "gender", "address.country", "address.street"],
    ))
    served = await _get(client, auth_headers, person["id"])
    assert served["referent_identity_paths"] == ["gender", "address.country"]


@pytest.mark.asyncio
async def test_a_template_without_referents_records_an_empty_list(client: AsyncClient, auth_headers: dict):
    address = await _address(client, auth_headers, "ADDR_1090_B")
    person = await _create(client, auth_headers, _person("PERSON_1090_B", address, ["name", "address.street"]))
    served = await _get(client, auth_headers, person["id"])
    assert served["referent_identity_paths"] == []


@pytest.mark.asyncio
async def test_a_new_version_records_its_own_paths(client: AsyncClient, auth_headers: dict):
    address = await _address(client, auth_headers, "ADDR_1090_C")
    payload = _person("PERSON_1090_C", address, ["name", "address.country"])
    first = await _create(client, auth_headers, payload)
    payload["fields"].append({"name": "note", "label": "Note", "type": "string"})
    second = await _create(client, auth_headers, payload)
    assert second["status"] == "updated"
    served = await _get(client, auth_headers, first["id"], version=2)
    assert served["referent_identity_paths"] == ["address.country"]


@pytest.mark.asyncio
async def test_inherited_identity_and_fields_are_classified(client: AsyncClient, auth_headers: dict):
    address = await _address(client, auth_headers, "ADDR_1090_D")
    parent = await _create(client, auth_headers, _person("PERSON_1090_D", address, ["name", "address.country"]))
    child = await _create(client, auth_headers, {
        "namespace": "wip", "value": "EMPLOYEE_1090_D", "label": "Employee",
        "extends": parent["id"], "extends_version": 1,
        "fields": [{"name": "employee_no", "label": "No", "type": "string"}],
    })
    served = await _get(client, auth_headers, child["id"])
    assert served["identity_fields"] == ["name", "address.country"]
    assert served["referent_identity_paths"] == ["address.country"]


@pytest.mark.asyncio
async def test_a_draft_records_nothing_until_activated(client: AsyncClient, auth_headers: dict):
    address = await _address(client, auth_headers, "ADDR_1090_E")
    draft = await _create(client, auth_headers, _person(
        "PERSON_1090_E", address, ["name", "address.country"], status="draft",
    ))
    assert (await _get(client, auth_headers, draft["id"]))["referent_identity_paths"] is None

    response = await client.post(
        f"{URL}/{draft['id']}/activate", headers=auth_headers, params={"namespace": "wip"},
    )
    assert response.status_code == 200, response.text
    assert (await _get(client, auth_headers, draft["id"]))["referent_identity_paths"] == ["address.country"]


@pytest.mark.asyncio
async def test_backfill_fills_versions_stored_without_the_field(client: AsyncClient, auth_headers: dict):
    address = await _address(client, auth_headers, "ADDR_1090_F")
    person = await _create(client, auth_headers, _person("PERSON_1090_F", address, ["name", "address.country"]))
    stored = await Template.find_one({"template_id": person["id"], "version": 1})
    stored.referent_identity_paths = None
    await stored.save()

    assert await backfill() >= 1
    refreshed = await Template.find_one({"template_id": person["id"], "version": 1})
    assert refreshed.referent_identity_paths == ["address.country"]


@pytest.mark.asyncio
async def test_the_field_is_not_accepted_on_a_write(client: AsyncClient, auth_headers: dict):
    """Server-owned: a caller cannot declare its own answer (the request model
    forbids unknown fields, so the write is refused)."""
    address = await _address(client, auth_headers, "ADDR_1090_G")
    payload = _person("PERSON_1090_G", address, ["name", "address.country"])
    payload["referent_identity_paths"] = []
    response = await client.post(URL, headers=auth_headers, json=[payload])
    assert response.status_code == 422, response.text
