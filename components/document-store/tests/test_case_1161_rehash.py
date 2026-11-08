"""How the identity rehash sorts each document, pinned before its refactor.

Provenance: CASE-1161. rehash_identities' outcomes for a document whose
template version is unreadable, whose template has no identity fields, whose
identity paths cannot be classified, whose template has no referent identity
fields, and whose stored resolutions cannot recompute its hash had no test.
Documents are written to the test MongoDB directly; the template store is a
stand-in answering one template per outcome.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import pytest_asyncio

from document_store.models.document import Document, DocumentStatus
from document_store.services.identity_migration import rehash_identities

NS = "rehash1161"

TEMPLATES = {
    "T-NOID": {"identity_fields": [], "fields": []},
    "T-PLAIN": {"identity_fields": ["k"], "fields": [{"name": "k", "type": "string"}],
                "referent_identity_paths": []},
    # A dotted identity path into a nested template, and no stored
    # referent_identity_paths: it cannot be classified.
    "T-NESTED": {"identity_fields": ["addr.city"],
                 "fields": [{"name": "addr", "type": "object", "template_ref": "ADDR"}]},
    "T-TERM": {"identity_fields": ["c"], "fields": [{"name": "c", "type": "term"}],
               "referent_identity_paths": ["c"]},
}


@pytest_asyncio.fixture
async def docs(session_mongo_client):
    from tests.conftest import _ensure_beanie

    await _ensure_beanie(session_mongo_client)
    await Document.find({"namespace": NS}).delete()
    for doc_id, template, data in (
        ("D-GONE", "T-GONE", {"k": "x"}),
        ("D-NOID", "T-NOID", {"k": "x"}),
        ("D-PLAIN", "T-PLAIN", {"k": "x"}),
        ("D-NESTED", "T-NESTED", {"addr": {"city": "Bonn"}}),
        ("D-TERM", "T-TERM", {"c": "red"}),  # no term_references: nothing to recompute from
    ):
        await Document(
            namespace=NS, document_id=doc_id, template_id=template, template_version=1,
            identity_hash=f"h-{doc_id}", version=1, data=data, status=DocumentStatus.ACTIVE,
        ).insert()
    yield
    await Document.find({"namespace": NS}).delete()


def _template_store():
    async def get_template(template_id=None, version=None, **_kw):
        return TEMPLATES.get(template_id)
    return patch("document_store.services.identity_migration.get_template_store_client",
                 return_value=SimpleNamespace(get_template=get_template))


@pytest.mark.asyncio
async def test_each_document_is_sorted_into_its_outcome(docs):
    with _template_store():
        report = await rehash_identities(NS, dry_run=True)
    assert report.scanned == 5
    assert (report.unchanged, report.errors, report.unresolved, report.changed) == (2, 2, 1, 0)
    listed = {item.document_id: (item.status, item.detail) for item in report.items}
    assert listed["D-GONE"] == ("error", "template version unreadable")
    assert listed["D-NESTED"][0] == "error" and "addr.city" in listed["D-NESTED"][1]
    assert listed["D-TERM"][0] == "unresolved"
    # The unchanged ones are counted, not listed.
    assert "D-NOID" not in listed and "D-PLAIN" not in listed
