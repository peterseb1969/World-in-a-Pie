"""A term at a dotted identity path (address.country) feeds its resolved term_id.

Provenance: CASE-1090. Before, two aliases of one term at a nested identity
path made two documents: the hasher classified identity paths by their
top-level field, and ``address`` is an object, so the spelling fed the hash.
The template's ``referent_identity_paths`` (recorded by the template store)
now names the nested path, and the write hashes the resolved term_id.
"""

import pytest

from document_store.models.document import Document, DocumentStatus

from .conftest import SAMPLE_TEMPLATES

DOCS_URL = "/api/document-store/documents"


async def _write(client, auth_headers, country: str, note: str | None = None) -> dict:
    data = {"name": "Ada", "address": {"country": country}}
    if note:
        data["address"]["street"] = note
    resp = await client.post(
        DOCS_URL,
        json=[{"template_id": "NESTED_TERM_IDENTITY", "namespace": "wip", "data": data}],
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


@pytest.mark.asyncio
async def test_aliases_of_a_nested_term_are_one_document(client, auth_headers):
    first = await _write(client, auth_headers, "UK")
    assert first["status"] == "created", first
    second = await _write(client, auth_headers, "uk", note="1 Main St")
    assert second["status"] == "updated", second
    assert second["document_id"] == first["document_id"]
    assert second["identity_hash"] == first["identity_hash"]

    active = await Document.find(
        {"document_id": first["document_id"], "status": DocumentStatus.ACTIVE.value}
    ).to_list()
    assert len(active) == 1 and active[0].version == 2


@pytest.mark.asyncio
async def test_without_recorded_paths_a_nested_identity_path_is_refused(client, auth_headers):
    """A template stored before the field existed cannot classify the path:
    the write is refused rather than hashed from the spelling."""
    template = SAMPLE_TEMPLATES["NESTED_TERM_IDENTITY"]
    saved = template["referent_identity_paths"]
    template["referent_identity_paths"] = None
    try:
        result = await _write(client, auth_headers, "CA")
    finally:
        template["referent_identity_paths"] = saved
    assert result["status"] == "error", result
    assert "address.country" in (result.get("error") or "") + str(result.get("details"))
