"""A document in a namespace the caller cannot read answers exactly as an id
that names nothing, for a key scoped to several namespaces too.

Provenance: CASE-1264 (commit-review backfill, ee9b8eac R2). A key scoped to
more than one namespace has no namespace to resolve an id in, so the id
passes through unresolved: an unknown id then got the door's own miss
("Document not found"), a hidden one "Could not resolve document_id … in
namespace 'None'". The doors now answer a hidden id with miss_detail, as
def-store and template-store do: the same text either way.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from document_store.models.document import Document, DocumentStatus
from tests.conftest import SAMPLE_TEMPLATES

DOCS = "/api/document-store/documents"
# Scoped to "wip" and "xns-closed": no namespace to resolve an id in.
MULTI_NS = {"X-API-Key": "test_xns_reader_key"}


async def _hidden_document() -> str:
    """A document in a namespace the multi-namespace key cannot read."""
    document_id = str(uuid.uuid4())
    await Document(
        namespace="hidden-1264", document_id=document_id,
        template_id=SAMPLE_TEMPLATES["PERSON"]["template_id"], template_value="PERSON",
        template_version=1, identity_hash=f"hidden-{document_id}", version=1,
        data={"first_name": "Hidden"}, status=DocumentStatus.ACTIVE,
    ).insert()
    return document_id


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["{id}", "{id}/versions/1", "{id}/latest", "{id}/relationships"])
async def test_a_hidden_document_answers_as_a_missing_one(client: AsyncClient, path: str):
    hidden_id, missing_id = await _hidden_document(), str(uuid.uuid4())
    hidden = await client.get(f"{DOCS}/{path.format(id=hidden_id)}", headers=MULTI_NS)
    missing = await client.get(f"{DOCS}/{path.format(id=missing_id)}", headers=MULTI_NS)
    assert missing.status_code == 404, missing.text
    assert (hidden.status_code, hidden.json()) == (missing.status_code, missing.json())
    assert "None" not in hidden.text
