"""A field's default_value is informational: it never makes a field optional.

The platform never fills an omitted field with its default_value (document
validation refuses a mandatory field left out, default or not). The schema
diff still counted a new mandatory field that declared a default as an
added *optional* field, so a version event that broke every write leaving
the field out read as compatible and migration-eligible, and document-store
then refused each of those migrations. Provenance: CASE-1200.
"""

import pytest
from httpx import AsyncClient

from template_store.services.document_store_client import set_document_store_client
from tests.test_case_711_guardrails import _payload, _post, _StubDocStore

NOTE = {"name": "note", "label": "Note", "type": "string", "mandatory": False}
CODE = {"name": "code", "label": "Code", "type": "string", "mandatory": True}


@pytest.fixture(autouse=True)
def _reset_doc_store_client():
    yield
    set_document_store_client(None)


@pytest.mark.asyncio
async def test_a_new_mandatory_field_with_a_default_is_an_added_required_field(
    client: AsyncClient, auth_headers: dict
):
    set_document_store_client(_StubDocStore(stats={
        "total_live_docs": 3, "docs_per_version": {"1": 3}, "field_nonempty_counts": {},
    }))
    await _post(client, auth_headers, [_payload("DEFAULT_1200", fields=[CODE, NOTE])])

    status = {"name": "status", "label": "Status", "type": "string",
              "mandatory": True, "default_value": "draft"}
    result = await _post(client, auth_headers, [_payload("DEFAULT_1200", fields=[CODE, NOTE, status])])

    item = result["results"][0]
    assert item["status"] == "updated"
    details = item["details"]
    assert details["added_required"] == ["status"]
    assert details["added_optional"] == []
    assert details["migration"]["eligible"] is False


@pytest.mark.asyncio
async def test_a_new_optional_field_with_a_default_stays_optional(
    client: AsyncClient, auth_headers: dict
):
    await _post(client, auth_headers, [_payload("DEFAULT_1200_OPT", fields=[CODE, NOTE])])
    flag = {"name": "active", "label": "Active", "type": "boolean", "default_value": True}
    result = await _post(client, auth_headers, [_payload("DEFAULT_1200_OPT", fields=[CODE, NOTE, flag])])

    details = result["results"][0]["details"]
    assert details["added_optional"] == ["active"]
    assert details["added_required"] == []
