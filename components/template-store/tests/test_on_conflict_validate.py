"""Tests for template create as an upsert keyed on (namespace, value).

The on_conflict parameter is deprecated: it is still accepted ('error' and
'validate' both upsert; any other value is a 400) and no longer selects
behavior. Matrix:
  no existing                  → status='created' (version 1)
  identical existing           → status='unchanged'
  added optional field         → status='updated'  (version N+1, diff in details)
  added required field         → status='updated'  (version N+1, diff in details)
  removed field                → status='updated'  (version N+1, diff in details)
  changed field type           → status='updated'  (version N+1, diff in details)
  identity_fields change       → status='error', error_code='identity_fields_immutable'
  identity field changes type  → status='error', error_code='identity_fields_immutable'
  reporting table_name change  → status='error', error_code='immutable_property'
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
async def test_validate_added_required_versions_with_diff(client: AsyncClient, auth_headers: dict):
    """Adding a required field versions the template — create is an upsert;
    the diff (added_required) is the loud report, not a rejection."""
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
    assert second["failed"] == 0
    item = second["results"][0]
    assert item["status"] == "updated"
    assert item["version"] == 2
    assert item["is_new_version"] is True
    assert item["details"]["added_required"] == ["ssn"]


@pytest.mark.asyncio
async def test_validate_removed_field_versions_with_diff(client: AsyncClient, auth_headers: dict):
    """Removing a field versions the template — the diff lists it under
    'removed' as the loud report."""
    payload = _person_v1_payload()
    payload["value"] = "PERSON_REM"
    await _post(client, auth_headers, [payload], on_conflict="validate")

    payload_v2 = _person_v1_payload()
    payload_v2["value"] = "PERSON_REM"
    payload_v2["fields"] = [f for f in payload_v2["fields"] if f["name"] != "last_name"]
    payload_v2["identity_fields"] = ["national_id"]

    second = await _post(client, auth_headers, [payload_v2], on_conflict="validate")
    assert second["failed"] == 0
    item = second["results"][0]
    assert item["status"] == "updated"
    assert item["version"] == 2
    assert "last_name" in item["details"]["removed"]


@pytest.mark.asyncio
async def test_validate_changed_type_versions_with_diff(client: AsyncClient, auth_headers: dict):
    """Changing a field's type versions the template — the diff records
    old/new type as the loud report."""
    payload = _person_v1_payload()
    payload["value"] = "PERSON_TYPE"
    await _post(client, auth_headers, [payload], on_conflict="validate")

    payload_v2 = _person_v1_payload()
    payload_v2["value"] = "PERSON_TYPE"
    for f in payload_v2["fields"]:
        if f["name"] == "first_name":
            f["type"] = "integer"

    second = await _post(client, auth_headers, [payload_v2], on_conflict="validate")
    assert second["failed"] == 0
    item = second["results"][0]
    assert item["status"] == "updated"
    assert item["version"] == 2
    diffs = item["details"]["changed_type"]
    assert any(d["name"] == "first_name" and d["new_type"] == "integer" for d in diffs)


@pytest.mark.asyncio
async def test_an_identity_field_keeps_its_type(client: AsyncClient, auth_headers: dict):
    """An identity field that changed type would hash its values
    differently ("123" vs 123): re-posted documents would fork instead of
    versioning. Refused like a change of identity_fields."""
    payload = _person_v1_payload()
    payload["value"] = "PERSON_IDTYPE"
    await _post(client, auth_headers, [payload])

    payload_v2 = _person_v1_payload()
    payload_v2["value"] = "PERSON_IDTYPE"
    for f in payload_v2["fields"]:
        if f["name"] == "national_id":
            f["type"] = "integer"
    second = await _post(client, auth_headers, [payload_v2])
    item = second["results"][0]
    assert item["status"] == "error"
    assert item["error_code"] == "identity_fields_immutable"
    assert "national_id" in item["error"]

    # The update route refuses it too.
    put = await client.put(
        "/api/template-store/templates", headers=auth_headers,
        json=[{"template_id": item["id"], "fields": payload_v2["fields"]}],
    )
    assert put.status_code == 200
    result = put.json()["results"][0]
    assert (result["status"], result["error_code"]) == ("error", "identity_fields_immutable")


@pytest.mark.asyncio
async def test_validate_identity_change_is_a_fork(client: AsyncClient, auth_headers: dict):
    """Changing identity_fields is rejected as a fork — the identity
    declaration is immutable across versions (document identity must stay
    comparable across the whole version catalog)."""
    payload = _person_v1_payload()
    payload["value"] = "PERSON_IDENT2"
    await _post(client, auth_headers, [payload], on_conflict="validate")

    payload_v2 = _person_v1_payload()
    payload_v2["value"] = "PERSON_IDENT2"
    payload_v2["identity_fields"] = ["first_name", "last_name"]

    second = await _post(client, auth_headers, [payload_v2], on_conflict="validate")
    assert second["failed"] == 1
    item = second["results"][0]
    assert item["status"] == "error"
    assert item["error_code"] == "identity_fields_immutable"
    assert "fork" in item["error"]
    assert item["details"]["identity_changed"]["old"] == ["national_id"]
    assert item["details"]["identity_changed"]["new"] == ["first_name", "last_name"]


@pytest.mark.asyncio
async def test_default_mode_upserts_identical_as_unchanged(client: AsyncClient, auth_headers: dict):
    """Default on_conflict (omitted) upserts like every mode: re-posting an
    identical schema returns 'unchanged' with the existing id/version —
    the idempotent-bootstrap contract with no query parameter needed."""
    payload = _person_v1_payload()
    payload["value"] = "PERSON_DEFAULT"
    first = await _post(client, auth_headers, [payload])  # no on_conflict
    second = await _post(client, auth_headers, [payload])  # re-run → unchanged
    assert second["failed"] == 0
    item = second["results"][0]
    assert item["status"] == "unchanged"
    assert item["id"] == first["results"][0]["id"]
    assert item["version"] == 1


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
    #   index 1 (BULK_B): added optional → updated (v2)
    #   index 2 (BULK_C): new → created
    #   index 3 (BULK_A): added required → updated (v2, loud diff)
    #   index 4 (BULK_B): identity change → error (fork)
    item_a_identical = _person_v1_payload()
    item_a_identical["value"] = "BULK_A"
    item_b_compat = _person_v1_payload()
    item_b_compat["value"] = "BULK_B"
    item_b_compat["fields"].append({
        "name": "nickname", "label": "Nickname", "type": "string", "mandatory": False,
    })
    item_c_new = _person_v1_payload()
    item_c_new["value"] = "BULK_C"
    item_a_required = _person_v1_payload()
    item_a_required["value"] = "BULK_A"
    item_a_required["fields"].append({
        "name": "must_have", "label": "Must Have", "type": "string", "mandatory": True,
    })
    item_b_fork = _person_v1_payload()
    item_b_fork["value"] = "BULK_B"
    item_b_fork["identity_fields"] = ["first_name"]

    bulk = await _post(
        client, auth_headers,
        [item_a_identical, item_b_compat, item_c_new, item_a_required, item_b_fork],
        on_conflict="validate",
    )
    statuses = [r["status"] for r in bulk["results"]]
    assert statuses == ["unchanged", "updated", "created", "updated", "error"]
    assert bulk["results"][3]["details"]["added_required"] == ["must_have"]
    assert bulk["results"][4]["error_code"] == "identity_fields_immutable"


@pytest.mark.asyncio
async def test_the_reporting_table_name_is_fixed_after_create(client: AsyncClient, auth_headers: dict):
    """Every reporting relation of a template is named from table_name; a
    change would split its tables across two names. Refused on a re-post
    and on an update (in place or with a new version); an unchanged name,
    and other reporting changes, still go through."""
    payload = _person_v1_payload()
    payload["value"] = "PERSON_TBL"
    payload["reporting"] = {"sync_enabled": True, "table_name": "people"}
    created = (await _post(client, auth_headers, [payload]))["results"][0]
    assert created["status"] == "created"

    renamed = dict(payload, reporting={"sync_enabled": True, "table_name": "persons"})
    item = (await _post(client, auth_headers, [renamed]))["results"][0]
    assert (item["error_code"], item["details"]) == ("immutable_property", {"rule": "table_name"})

    put = await client.put("/api/template-store/templates", headers=auth_headers, json=[
        {"template_id": created["id"], "reporting": {"sync_enabled": True, "table_name": "persons"}},
    ])
    result = put.json()["results"][0]
    assert (result["error_code"], result["details"]) == ("immutable_property", {"rule": "table_name"})

    # The default name counts too: setting one later is a change.
    plain = _person_v1_payload()
    plain["value"] = "PERSON_TBL_DEFAULT"
    await _post(client, auth_headers, [plain])
    named = dict(plain, reporting={"sync_enabled": True, "table_name": "people2"})
    item = (await _post(client, auth_headers, [named]))["results"][0]
    assert item["error_code"] == "immutable_property"

    # Same name, other reporting settings: allowed (in place).
    same = await client.put("/api/template-store/templates", headers=auth_headers, json=[
        {"template_id": created["id"],
         "reporting": {"sync_enabled": True, "table_name": "people", "include_metadata": False}},
    ])
    assert same.json()["results"][0]["status"] in ("updated", "unchanged"), same.text
