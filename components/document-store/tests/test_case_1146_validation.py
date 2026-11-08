"""Validation rules that held only part of the time.

Provenance: CASE-1146 (validation slice).

- validation.enum holds for numbers, integers and booleans, not only strings.
- file_config.max_files caps a multi-file field.
- An email with a trailing newline is not an email.
- A reference resolved through the Registry answers the latest version.
- A template-store miss is not cached: a template created a moment later is found.
"""

import uuid
from unittest.mock import AsyncMock

import pytest

from document_store.models.document import Document, DocumentStatus
from document_store.services.template_store_client import TemplateStoreClient

CANDIDATE = "/api/document-store/validation/validate-candidate"


def _candidate(fields: list[dict]) -> dict:
    return {
        "template_id": "candidate", "value": "CANDIDATE_1146", "label": "C",
        "version": 1, "status": "active", "namespace": "wip",
        "identity_fields": [], "fields": fields, "rules": [],
    }


async def _codes(client, auth_headers, fields: list[dict], documents: list[dict]) -> list[list[str]]:
    resp = await client.post(CANDIDATE, headers=auth_headers, json={
        "namespace": "wip", "template_definition": _candidate(fields), "documents": documents,
    })
    assert resp.status_code == 200, resp.text
    return [[e["code"] for e in r["validation"]["errors"]] for r in resp.json()["results"]]


@pytest.mark.asyncio
async def test_enum_holds_for_numbers_integers_and_booleans(client, auth_headers):
    fields = [
        {"name": "n", "label": "n", "type": "number", "validation": {"enum": [1.5, 2.5]}},
        {"name": "i", "label": "i", "type": "integer", "validation": {"enum": [1, 2]}},
        {"name": "b", "label": "b", "type": "boolean", "validation": {"enum": [True]}},
    ]
    codes = await _codes(client, auth_headers, fields, [
        {"n": 1.5, "i": 2, "b": True},
        {"n": 3.0, "i": 7, "b": False},
    ])
    assert codes[0] == []
    assert codes[1] == ["invalid_enum"] * 3


@pytest.mark.asyncio
async def test_max_files_caps_a_multi_file_field(client, auth_headers):
    fields = [{"name": "f", "label": "f", "type": "file",
               "file_config": {"multiple": True, "max_files": 2}}]
    codes = await _codes(client, auth_headers, fields, [{"f": ["a", "b", "c"]}])
    assert "max_files" in codes[0]


@pytest.mark.asyncio
async def test_an_email_with_a_trailing_newline_is_refused(client, auth_headers):
    fields = [{"name": "e", "label": "e", "type": "string", "semantic_type": "email"}]
    codes = await _codes(client, auth_headers, fields, [{"e": "a@b.co"}, {"e": "a@b.co\n"}])
    assert codes[0] == []
    assert codes[1] == ["invalid_email"]


@pytest.mark.asyncio
async def test_a_registry_resolved_reference_answers_the_latest_version(client, monkeypatch):
    """Two active rows of one document (a restore that kept older versions):
    the lookup answers the highest, not whichever the store returns first."""
    from document_store.services import registry_client
    from document_store.services.validation_service import ValidationService

    document_id = str(uuid.uuid4())
    for version in (1, 3, 2):
        await Document(
            namespace="wip", document_id=document_id, template_id="T",
            template_version=1, identity_hash=f"h{version}", version=version,
            data={}, status=DocumentStatus.ACTIVE,
        ).insert()
    fake = AsyncMock()
    fake.resolve_candidates = AsyncMock(return_value=([{"entry_id": document_id}], 1))
    monkeypatch.setattr(registry_client, "get_registry_client", lambda: fake)

    doc, candidates = await ValidationService()._resolve_bare_document_reference(
        "some-synonym", "wip", [],
    )
    assert candidates == []
    assert doc.version == 3


@pytest.mark.asyncio
async def test_a_template_store_miss_is_not_cached():
    client = TemplateStoreClient(base_url="http://template-store", api_key="k")
    template = {"template_id": "T", "version": 1, "namespace": "wip"}
    client.get_template = AsyncMock(side_effect=[None, template])
    client._namespace_is_fresh = AsyncMock(return_value=True)

    assert await client.get_template_resolved("T") is None
    assert await client.get_template_resolved("T") == template
