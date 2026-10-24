"""Every template-store error item carries a machine-readable error_code.

Callers branch on the code, not the message. Where one code covers several
rules, details.rule names the rule that failed. Messages are unchanged.
"""

import pytest
from httpx import AsyncClient

from template_store.services.errors import TemplateError, error_fields
from template_store.services.template_service import TemplateService

TEMPLATES = "/api/template-store/templates"


def _tpl(value: str, *fields: dict, **extra) -> dict:
    return {
        "namespace": "wip",
        "value": value,
        "label": value,
        "fields": [{"name": "id", "label": "id", "type": "string"}, *fields],
        "identity_fields": ["id"],
        **extra,
    }


async def _create(client, headers, *templates) -> list[dict]:
    resp = await client.post(TEMPLATES, headers=headers, json=list(templates))
    assert resp.status_code == 200, resp.text
    return resp.json()["results"]


def test_error_fields_for_a_template_error():
    exc = TemplateError("boom", "invalid_state", rule="not_draft")
    assert error_fields(exc) == {
        "error": "boom", "error_code": "invalid_state", "details": {"rule": "not_draft"},
    }


def test_error_fields_falls_back_for_a_plain_value_error():
    assert error_fields(ValueError("boom")) == {"error": "boom", "error_code": "invalid_request"}


def test_template_error_is_a_value_error():
    # Existing `except ValueError` handlers keep catching it.
    with pytest.raises(ValueError, match="boom"):
        raise TemplateError("boom", "not_found")


@pytest.mark.asyncio
async def test_authoring_rule_is_validation_failed_with_its_rule(client: AsyncClient, auth_headers: dict):
    [item] = await _create(client, auth_headers, _tpl(
        "EC_ARRAY_REFS", {"name": "refs", "label": "R", "type": "array", "array_item_type": "reference"},
    ))
    assert item["status"] == "error"
    assert item["error_code"] == "validation_failed"
    assert item["details"] == {"rule": "reference_type_required"}
    assert "reference_type is required" in item["error"]  # message unchanged


@pytest.mark.asyncio
async def test_unresolvable_parent_is_reference_violation(client: AsyncClient, auth_headers: dict):
    [item] = await _create(client, auth_headers, _tpl(
        "EC_ORPHAN", extends="EC_NO_SUCH_PARENT", extends_version=1,
    ))
    assert item["status"] == "error"
    assert item["error_code"] == "reference_violation", item


@pytest.mark.asyncio
async def test_changing_identity_on_update_is_identity_fields_immutable(client: AsyncClient, auth_headers: dict):
    [created] = await _create(client, auth_headers, _tpl(
        "EC_IDENTITY", {"name": "other", "label": "o", "type": "string"},
    ))
    assert created["status"] == "created", created
    resp = await client.put(TEMPLATES, headers=auth_headers, json=[{
        "template_id": created["id"], "identity_fields": ["other"],
    }])
    [item] = resp.json()["results"]
    assert item["status"] == "error"
    assert item["error_code"] == "identity_fields_immutable", item


@pytest.mark.asyncio
async def test_versionless_deactivate_is_invalid_request(client: AsyncClient, auth_headers: dict):
    [created] = await _create(client, auth_headers, _tpl("EC_DEACTIVATE"))
    resp = await client.request("DELETE", TEMPLATES, headers=auth_headers, json=[{"id": created["id"]}])
    [item] = resp.json()["results"]
    assert item["status"] == "error"
    assert item["error_code"] == "invalid_request"
    assert item["details"] == {"rule": "version_required"}


@pytest.mark.asyncio
async def test_deleting_a_parent_is_invalid_state(client: AsyncClient, auth_headers: dict):
    [parent] = await _create(client, auth_headers, _tpl("EC_PARENT"))
    [child] = await _create(client, auth_headers, _tpl("EC_CHILD", extends="EC_PARENT", extends_version=1))
    assert child["status"] == "created", child
    resp = await client.request("DELETE", TEMPLATES, headers=auth_headers, json=[
        {"id": parent["id"], "version": 1},
    ])
    [item] = resp.json()["results"]
    assert item["status"] == "error"
    assert item["error_code"] == "invalid_state"
    assert item["details"] == {"rule": "has_children"}


@pytest.mark.asyncio
async def test_a_plain_value_error_still_gets_a_code(client: AsyncClient, auth_headers: dict, monkeypatch):
    [created] = await _create(client, auth_headers, _tpl("EC_FALLBACK"))

    async def boom(*_args, **_kwargs):
        raise ValueError("something unexpected")

    monkeypatch.setattr(TemplateService, "update_template", boom)
    resp = await client.put(TEMPLATES, headers=auth_headers, json=[{
        "template_id": created["id"], "label": "x",
    }])
    [item] = resp.json()["results"]
    assert item == {**item, "status": "error", "error": "something unexpected", "error_code": "invalid_request"}
