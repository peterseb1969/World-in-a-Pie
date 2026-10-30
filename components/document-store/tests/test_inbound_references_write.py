"""The inbound-reference gate on the document write paths, end to end.

A document in 'wip' uses a template owned by 'xns-closed', a namespace the
Registry has no settings for, so it reads as closed. Two non-admin keys may
write 'wip'; only the reader can also read 'xns-closed'.

- A new reference into the closed namespace needs the writer's read, and a
  refusal looks like a reference that does not resolve.
- A reference the document's previous version already held carries over: a
  writer without read may still upsert or PATCH the document.
"""

import pytest
from httpx import AsyncClient

DOCS_URL = "/api/document-store/documents"
READER = {"X-API-Key": "test_xns_reader_key"}
WRITER = {"X-API-Key": "test_xns_writer_key"}
TEMPLATE_ID = "0190c983-0000-7000-0000-00000000c983"


@pytest.fixture
def closed_namespace_template():
    from .conftest import SAMPLE_TEMPLATES

    SAMPLE_TEMPLATES[TEMPLATE_ID] = {
        "template_id": TEMPLATE_ID,
        "value": "XNS_CLOSED_PROBE",
        "label": "Inbound reference probe",
        "version": 1,
        "status": "active",
        "namespace": "xns-closed",
        "identity_fields": ["code"],
        "header_fields": [],
        "fields": [
            {"name": "code", "type": "string", "label": "Code", "mandatory": True},
            {"name": "note", "type": "string", "label": "Note", "mandatory": False},
        ],
        "rules": [],
        "usage": "entity",
        "source_templates": [],
        "target_templates": [],
        "versioned": True,
    }
    yield TEMPLATE_ID
    SAMPLE_TEMPLATES.pop(TEMPLATE_ID, None)


async def _write(client: AsyncClient, headers: dict, code: str, note: str) -> dict:
    response = await client.post(
        DOCS_URL, headers=headers,
        json=[{
            "namespace": "wip", "template_id": TEMPLATE_ID,
            "template_version": 1, "data": {"code": code, "note": note},
        }],
    )
    assert response.status_code == 200, response.text
    return response.json()["results"][0]


@pytest.mark.asyncio
async def test_a_writer_who_cannot_read_the_closed_namespace_is_refused(
    client: AsyncClient, closed_namespace_template: str,
):
    result = await _write(client, WRITER, "NEW", "x")
    assert result["status"] == "error"
    assert result["error_code"] == "validation_failed"
    assert result["details"]["errors"][0]["code"] == "reference_not_found"
    # Nothing tells the writer which namespace refused it.
    assert "xns-closed" not in str(result)


@pytest.mark.asyncio
async def test_a_writer_who_reads_the_closed_namespace_may_reference_it(
    client: AsyncClient, closed_namespace_template: str,
):
    result = await _write(client, READER, "READ", "x")
    assert result["status"] == "created", result


@pytest.mark.asyncio
async def test_an_upsert_keeps_the_reference_the_previous_version_held(
    client: AsyncClient, closed_namespace_template: str,
):
    first = await _write(client, READER, "KEEP", "v1")
    assert first["status"] == "created", first

    second = await _write(client, WRITER, "KEEP", "v2")
    assert second["status"] == "updated", second
    assert second["document_id"] == first["document_id"]


@pytest.mark.asyncio
async def test_a_patch_keeps_the_reference_the_current_version_holds(
    client: AsyncClient, closed_namespace_template: str,
):
    created = await _write(client, READER, "PATCHED", "v1")
    assert created["status"] == "created", created

    response = await client.patch(
        DOCS_URL, headers=WRITER,
        json=[{"document_id": created["document_id"], "patch": {"note": "v2"}}],
    )
    assert response.status_code == 200, response.text
    assert response.json()["results"][0]["status"] == "updated", response.text
