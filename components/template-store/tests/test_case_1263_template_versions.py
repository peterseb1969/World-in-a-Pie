"""CASE-1263 (reviews of 039144e6 and 8dca5042) — what a version event says
and what a template keeps one value of.

- A version that inherits from another parent (extends / extends_version)
  is not offered as an additive migration: the fields the new parent gives
  are not in the diff.
- A changed default_value alone is reported apart (default_value_changed),
  not as a modified field: the platform never applies it.
- sync_strategy is one value per template: a write that sets it, in place or
  on a new version, sets it for every version.
"""

import pytest
from httpx import AsyncClient

from template_store.models.template import Template
from template_store.services.document_store_client import set_document_store_client
from tests.test_case_711_guardrails import _payload, _post, _StubDocStore

CODE = {"name": "code", "label": "Code", "type": "string", "mandatory": True}
NOTE = {"name": "note", "label": "Note", "type": "string", "mandatory": False}
EMAIL = {"name": "email", "label": "Email", "type": "string", "mandatory": True}


@pytest.fixture(autouse=True)
def _doc_store():
    set_document_store_client(_StubDocStore(stats={
        "total_live_docs": 2, "docs_per_version": {"1": 2}, "field_nonempty_counts": {},
    }))
    yield
    set_document_store_client(None)


@pytest.mark.asyncio
async def test_a_new_parent_version_is_not_offered_as_additive(client: AsyncClient, auth_headers: dict):
    parent = (await _post(client, auth_headers, [_payload("PERSON_1263", fields=[CODE])]))["results"][0]
    await _post(client, auth_headers, [_payload("PERSON_1263", fields=[CODE, EMAIL])])  # v2: requires email
    child = {"value": "STUDENT_1263", "extends": parent["id"], "fields": [NOTE], "identity_fields": []}
    await _post(client, auth_headers, [_payload(**child, extends_version=1)])

    result = await _post(client, auth_headers, [_payload(**child, extends_version=2)])

    item = result["results"][0]
    assert item["status"] == "updated"
    assert item["details"]["parent_changed"]["new"]["extends_version"] == 2
    assert item["details"]["migration"]["eligible"] is None
    assert "parent template changed" in item["details"]["migration"]["reason"]


@pytest.mark.asyncio
async def test_a_default_value_change_alone_is_reported_apart(client: AsyncClient, auth_headers: dict):
    status = {"name": "status", "label": "Status", "type": "string", "default_value": "draft"}
    await _post(client, auth_headers, [_payload("PREFILL_1263", fields=[CODE, status])])

    result = await _post(client, auth_headers, [
        _payload("PREFILL_1263", fields=[CODE, {**status, "default_value": "new"}]),
    ])

    item = result["results"][0]
    assert item["status"] == "updated"  # the edit is kept: a new version
    assert item["details"]["default_value_changed"] == ["status"]
    assert item["details"]["modified_existing"] == []
    assert item["details"]["migration"]["eligible"] is True


async def _strategies(template_id: str) -> dict[int, str]:
    rows = await Template.find({"template_id": template_id}).to_list()
    return {t.version: (t.reporting.sync_strategy if t.reporting else "latest_only") for t in rows}


@pytest.mark.asyncio
async def test_a_new_versions_strategy_applies_to_every_version(client: AsyncClient, auth_headers: dict):
    first = (await _post(client, auth_headers, [_payload("STRAT_1263", fields=[CODE])]))["results"][0]
    await _post(client, auth_headers, [_payload("STRAT_1263", fields=[CODE, NOTE])])

    await _post(client, auth_headers, [_payload(
        "STRAT_1263", fields=[CODE, NOTE, {**NOTE, "name": "extra"}],
        reporting={"sync_strategy": "all_versions"},
    )])

    assert await _strategies(first["id"]) == {1: "all_versions", 2: "all_versions", 3: "all_versions"}


@pytest.mark.asyncio
async def test_an_in_place_strategy_change_applies_to_every_version(client: AsyncClient, auth_headers: dict):
    first = (await _post(client, auth_headers, [_payload(
        "STRAT2_1263", fields=[CODE], reporting={"sync_strategy": "all_versions"},
    )]))["results"][0]
    await _post(client, auth_headers, [_payload(
        "STRAT2_1263", fields=[CODE, NOTE], reporting={"sync_strategy": "all_versions"},
    )])

    resp = await client.put("/api/template-store/templates", headers=auth_headers, json=[{
        "template_id": first["id"], "reporting": {"sync_strategy": "latest_only"},
    }])
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][0]["status"] in ("updated", "unchanged"), resp.text

    assert await _strategies(first["id"]) == {1: "latest_only", 2: "latest_only"}
