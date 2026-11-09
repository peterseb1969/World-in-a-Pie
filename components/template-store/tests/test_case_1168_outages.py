"""A Def-Store that does not answer is not an answer.

- The integrity scan skipped a terminology check Def-Store could not answer
  and reported "healthy". It reports the check as skipped, status "unknown",
  and /health/integrity answers 503 (reporting-sync counts the service as
  unavailable). A scan cut at its limit says so (truncated).
- The scan found a template reference by any one version row: a parent whose
  v1 is retired read as inactive; a draft's value-form reference read as
  orphaned; a reference field's targets were never scanned. It now asks for
  an active version, reads value and ns:VALUE forms, and scans targets.
- /validate and activation counted an unanswered terminology lookup as a
  warning, so the template was "valid" and activated. It is an error
  (code unverifiable).
- ?refs=portable served instance ids where Def-Store did not answer, mixed
  into a 200; the read now fails with 503.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from template_store.models.field import FieldDefinition
from template_store.models.template import Template
from template_store.services.def_store_client import DefStoreError

API = "/api/template-store/templates"
INTEGRITY = "/health/integrity"


def _down() -> AsyncMock:
    client = AsyncMock()
    client.get_terminology = AsyncMock(side_effect=DefStoreError("connection refused"))
    client.terminology_exists = AsyncMock(side_effect=DefStoreError("connection refused"))
    return client


async def _post(client, headers, payload) -> dict:
    resp = await client.post(API, headers=headers, json=[payload])
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


def _tpl(value: str, *fields: dict, **extra) -> dict:
    return {"namespace": "wip", "value": value, "label": value.title(),
            "fields": [{"name": "id", "type": "string", "label": "Id"}, *fields], **extra}


TERM_FIELD = {"name": "g", "type": "term", "label": "G", "terminology_ref": "GENDER"}


@pytest.mark.asyncio
async def test_an_unanswered_terminology_check_is_not_healthy(client: AsyncClient, auth_headers):
    await _post(client, auth_headers, _tpl("C1168O_TERM", TERM_FIELD))
    with patch("template_store.services.integrity_service.get_def_store_client",
               return_value=_down()):
        resp = await client.get(INTEGRITY, headers=auth_headers, params={"limit": 0})
    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["status"] == "unknown", body
    assert body["summary"]["checks_skipped"] >= 1
    assert any(s["reference"] for s in body["checks_skipped"])


@pytest.mark.asyncio
async def test_a_scan_cut_at_its_limit_says_so(client: AsyncClient, auth_headers):
    for v in ("C1168O_L1", "C1168O_L2"):
        await _post(client, auth_headers, _tpl(v))
    resp = await client.get(INTEGRITY, headers=auth_headers, params={"limit": 1})
    assert resp.json()["summary"]["truncated"] is True, resp.text


async def _issues_for(client, headers, template_id: str) -> list[dict]:
    resp = await client.get(INTEGRITY, headers=headers, params={"limit": 0})
    return [i for i in resp.json()["issues"] if i["template_id"] == template_id]


@pytest.mark.asyncio
async def test_a_parent_with_an_active_version_is_not_inactive(client: AsyncClient, auth_headers):
    parent = await _post(client, auth_headers, _tpl("C1168O_PARENT"))
    await _post(client, auth_headers, _tpl("C1168O_PARENT", label="Second"))
    child = await _post(client, auth_headers, {
        "namespace": "wip", "value": "C1168O_CHILD", "label": "Child",
        "extends": "C1168O_PARENT", "extends_version": 2,
        "fields": [{"name": "note", "type": "string", "label": "Note"}]})
    await (await Template.find_one({"template_id": parent["id"], "version": 1})).set(
        {"status": "inactive"})
    assert await _issues_for(client, auth_headers, child["id"]) == []


@pytest.mark.asyncio
async def test_a_drafts_value_form_reference_is_not_orphaned(client: AsyncClient, auth_headers):
    await _post(client, auth_headers, _tpl("C1168O_TARGET"))
    draft = await _post(client, auth_headers, _tpl(
        "C1168O_DRAFT", {"name": "link", "type": "reference", "label": "L",
                          "reference_type": "document", "target_templates": ["wip:C1168O_TARGET"]},
        status="draft"))
    assert draft["status"] == "created", draft
    assert await _issues_for(client, auth_headers, draft["id"]) == []


@pytest.mark.asyncio
async def test_a_reference_fields_targets_are_scanned(client: AsyncClient, auth_headers):
    item = await _post(client, auth_headers, _tpl("C1168O_REFS"))
    row = await Template.find_one({"template_id": item["id"]})
    row.fields.append(FieldDefinition(
        name="link", label="L", type="reference", reference_type="document",
        target_templates=["0190f000-0000-7000-8000-00000000dead"]))
    await row.save()
    issues = await _issues_for(client, auth_headers, item["id"])
    assert [i["type"] for i in issues] == ["orphaned_template_ref"], issues


@pytest.mark.asyncio
async def test_validate_is_not_valid_when_def_store_does_not_answer(
    client: AsyncClient, auth_headers,
):
    item = await _post(client, auth_headers, _tpl("C1168O_VALID", TERM_FIELD))
    with patch("template_store.services.template_service.get_def_store_client",
               return_value=_down()):
        resp = await client.post(f"{API}/{item['id']}/validate", headers=auth_headers,
                                 params={"namespace": "wip"}, json={})
    body = resp.json()
    assert body["valid"] is False, body
    assert body["errors"][0]["code"] == "unverifiable", body


@pytest.mark.asyncio
async def test_a_portable_read_fails_when_def_store_does_not_answer(
    client: AsyncClient, auth_headers,
):
    item = await _post(client, auth_headers, _tpl("C1168O_PORT", TERM_FIELD))
    with patch("template_store.services.portable.get_def_store_client", return_value=_down()):
        resp = await client.get(f"{API}/{item['id']}", headers=auth_headers,
                                params={"namespace": "wip", "refs": "portable"})
    assert resp.status_code == 503, resp.text
