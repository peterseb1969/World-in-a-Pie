"""Tests for POST /templates?on_conflict=validate (CASE-25 Phase 1 Gap 2).

Behavior matrix:
  no existing            → status='created'
  identical existing     → status='unchanged'
  added optional field   → status='updated'  (compatible, version N+1)
  removed field          → status='error', error_code='incompatible_schema'
  added required field   → status='error', error_code='incompatible_schema'
  changed field type     → status='error', error_code='incompatible_schema'
  identity_fields change → status='error', error_code='incompatible_schema'
  modified existing fld  → status='error', error_code='incompatible_schema'
"""

import pytest
from httpx import AsyncClient


def _person_v1_payload() -> dict:
    return {
        "namespace": "wip",
        "value": "PERSON_VAL",
        "label": "Person",
        "identity_fields": ["national_id"],
        "fields": [
            {"name": "first_name", "label": "First Name", "type": "string", "mandatory": True},
            {"name": "last_name", "label": "Last Name", "type": "string", "mandatory": True},
            {"name": "national_id", "label": "National ID", "type": "string", "mandatory": True},
        ],
    }


async def _post(client: AsyncClient, auth_headers: dict, items: list[dict], on_conflict: str | None = None) -> dict:
    url = "/api/template-store/templates"
    if on_conflict:
        url += f"?on_conflict={on_conflict}"
    response = await client.post(url, headers=auth_headers, json=items)
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
async def test_validate_creates_when_missing(client: AsyncClient, auth_headers: dict):
    """on_conflict=validate creates the template when none exists (status='created')."""
    payload = _person_v1_payload()
    payload["value"] = "PERSON_CREATE"
    data = await _post(client, auth_headers, [payload], on_conflict="validate")
    assert data["succeeded"] == 1
    assert data["results"][0]["status"] == "created"
    assert data["results"][0]["version"] == 1


@pytest.mark.asyncio
async def test_validate_identical_returns_unchanged(client: AsyncClient, auth_headers: dict):
    """Re-posting an identical schema returns status='unchanged'."""
    payload = _person_v1_payload()
    payload["value"] = "PERSON_IDENT"
    first = await _post(client, auth_headers, [payload], on_conflict="validate")
    assert first["results"][0]["status"] == "created"
    template_id = first["results"][0]["id"]

    second = await _post(client, auth_headers, [payload], on_conflict="validate")
    assert second["succeeded"] == 1
    assert second["failed"] == 0
    item = second["results"][0]
    assert item["status"] == "unchanged"
    assert item["id"] == template_id
    assert item["version"] == 1


@pytest.mark.asyncio
async def test_validate_added_optional_field_bumps_version(client: AsyncClient, auth_headers: dict):
    """Adding only an optional field is compatible — bumps to version N+1."""
    payload = _person_v1_payload()
    payload["value"] = "PERSON_OPT"
    first = await _post(client, auth_headers, [payload], on_conflict="validate")
    template_id = first["results"][0]["id"]

    payload_v2 = _person_v1_payload()
    payload_v2["value"] = "PERSON_OPT"
    payload_v2["fields"].append({
        "name": "nickname",
        "label": "Nickname",
        "type": "string",
        "mandatory": False,
    })

    second = await _post(client, auth_headers, [payload_v2], on_conflict="validate")
    assert second["succeeded"] == 1
    item = second["results"][0]
    assert item["status"] == "updated"
    assert item["id"] == template_id
    assert item["version"] == 2
    assert item["is_new_version"] is True
    assert item["details"]["added_optional"] == ["nickname"]


@pytest.mark.asyncio
async def test_validate_added_required_is_incompatible(client: AsyncClient, auth_headers: dict):
    """Adding a required field with no default is incompatible."""
    payload = _person_v1_payload()
    payload["value"] = "PERSON_REQ"
    await _post(client, auth_headers, [payload], on_conflict="validate")

    payload_v2 = _person_v1_payload()
    payload_v2["value"] = "PERSON_REQ"
    payload_v2["fields"].append({
        "name": "ssn",
        "label": "SSN",
        "type": "string",
        "mandatory": True,
    })

    second = await _post(client, auth_headers, [payload_v2], on_conflict="validate")
    assert second["failed"] == 1
    item = second["results"][0]
    assert item["status"] == "error"
    assert item["error_code"] == "incompatible_schema"
    assert item["details"]["added_required"] == ["ssn"]


@pytest.mark.asyncio
async def test_validate_removed_field_is_incompatible(client: AsyncClient, auth_headers: dict):
    """Removing a field is incompatible — diff lists it under 'removed'."""
    payload = _person_v1_payload()
    payload["value"] = "PERSON_REM"
    await _post(client, auth_headers, [payload], on_conflict="validate")

    payload_v2 = _person_v1_payload()
    payload_v2["value"] = "PERSON_REM"
    payload_v2["fields"] = [f for f in payload_v2["fields"] if f["name"] != "last_name"]
    payload_v2["identity_fields"] = ["national_id"]

    second = await _post(client, auth_headers, [payload_v2], on_conflict="validate")
    assert second["failed"] == 1
    item = second["results"][0]
    assert item["error_code"] == "incompatible_schema"
    assert "last_name" in item["details"]["removed"]


@pytest.mark.asyncio
async def test_validate_changed_type_is_incompatible(client: AsyncClient, auth_headers: dict):
    """Changing a field's type is incompatible — diff records old/new type."""
    payload = _person_v1_payload()
    payload["value"] = "PERSON_TYPE"
    await _post(client, auth_headers, [payload], on_conflict="validate")

    payload_v2 = _person_v1_payload()
    payload_v2["value"] = "PERSON_TYPE"
    for f in payload_v2["fields"]:
        if f["name"] == "national_id":
            f["type"] = "integer"

    second = await _post(client, auth_headers, [payload_v2], on_conflict="validate")
    assert second["failed"] == 1
    item = second["results"][0]
    assert item["error_code"] == "incompatible_schema"
    diffs = item["details"]["changed_type"]
    assert any(d["name"] == "national_id" and d["new_type"] == "integer" for d in diffs)


@pytest.mark.asyncio
async def test_validate_identity_change_is_incompatible(client: AsyncClient, auth_headers: dict):
    """Changing identity_fields is incompatible — diff records old/new lists."""
    payload = _person_v1_payload()
    payload["value"] = "PERSON_IDENT2"
    await _post(client, auth_headers, [payload], on_conflict="validate")

    payload_v2 = _person_v1_payload()
    payload_v2["value"] = "PERSON_IDENT2"
    payload_v2["identity_fields"] = ["first_name", "last_name"]

    second = await _post(client, auth_headers, [payload_v2], on_conflict="validate")
    assert second["failed"] == 1
    item = second["results"][0]
    assert item["error_code"] == "incompatible_schema"
    assert item["details"]["identity_changed"]["old"] == ["national_id"]
    assert item["details"]["identity_changed"]["new"] == ["first_name", "last_name"]


@pytest.mark.asyncio
async def test_validate_default_mode_still_errors(client: AsyncClient, auth_headers: dict):
    """Default on_conflict (omitted) still errors on duplicate value."""
    payload = _person_v1_payload()
    payload["value"] = "PERSON_DEFAULT"
    await _post(client, auth_headers, [payload])  # no on_conflict
    second = await _post(client, auth_headers, [payload])  # default → error
    assert second["failed"] == 1
    assert second["results"][0]["status"] == "error"
    assert "already exists" in second["results"][0]["error"]


@pytest.mark.asyncio
async def test_validate_invalid_on_conflict_returns_400(client: AsyncClient, auth_headers: dict):
    """Unknown on_conflict value is rejected with 400."""
    response = await client.post(
        "/api/template-store/templates?on_conflict=bogus",
        headers=auth_headers,
        json=[_person_v1_payload()],
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_validate_bulk_mixed_outcomes(client: AsyncClient, auth_headers: dict):
    """A bulk validate request can return a mix of created/unchanged/updated/error."""
    # Pre-create two templates to set up identical and compatible cases.
    base_a = _person_v1_payload()
    base_a["value"] = "BULK_A"
    base_b = _person_v1_payload()
    base_b["value"] = "BULK_B"
    await _post(client, auth_headers, [base_a], on_conflict="validate")
    await _post(client, auth_headers, [base_b], on_conflict="validate")

    # Bulk request:
    #   index 0 (BULK_A): identical → unchanged
    #   index 1 (BULK_B): added optional → updated
    #   index 2 (BULK_C): new → created
    #   index 3 (BULK_A): added required → error
    item_a_identical = _person_v1_payload(); item_a_identical["value"] = "BULK_A"
    item_b_compat = _person_v1_payload(); item_b_compat["value"] = "BULK_B"
    item_b_compat["fields"].append({
        "name": "nickname", "label": "Nickname", "type": "string", "mandatory": False,
    })
    item_c_new = _person_v1_payload(); item_c_new["value"] = "BULK_C"
    item_a_incompat = _person_v1_payload(); item_a_incompat["value"] = "BULK_A"
    item_a_incompat["fields"].append({
        "name": "must_have", "label": "Must Have", "type": "string", "mandatory": True,
    })

    bulk = await _post(
        client, auth_headers,
        [item_a_identical, item_b_compat, item_c_new, item_a_incompat],
        on_conflict="validate",
    )
    statuses = [r["status"] for r in bulk["results"]]
    assert statuses == ["unchanged", "updated", "created", "error"]
    assert bulk["results"][3]["error_code"] == "incompatible_schema"
