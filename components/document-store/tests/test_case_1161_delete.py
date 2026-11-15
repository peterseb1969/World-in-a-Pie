"""delete_document's quiet exits, pinned before its refactor.

Provenance: CASE-1161. These branches of DocumentService.delete_document had
no test: hard-deleting an unknown document or a version that does not exist,
the Registry entry's removal failing (logged, not fatal) on the version and
the all-versions paths, a forced delete without warning or broken-reference
collectors, and a soft delete of a document with no active version (an
idempotent success) or without a warnings collector. Documents are written to the test MongoDB
directly; the Registry is a stand-in answering deletion_mode 'full'.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from document_store.models.document import Document, DocumentStatus
from document_store.services.document_service import DocumentService

NS = "del1161"


@pytest_asyncio.fixture
async def clean(session_mongo_client):
    from tests.conftest import _ensure_beanie

    await _ensure_beanie(session_mongo_client)
    await Document.find({"namespace": NS}).delete()
    yield
    await Document.find({"namespace": NS}).delete()


async def _doc(doc_id: str, versions: int = 1, *, active: bool = True) -> None:
    for v in range(1, versions + 1):
        latest = v == versions
        await Document(
            namespace=NS, document_id=doc_id, template_id="T", template_version=1,
            identity_hash=f"h-{doc_id}", version=v, data={},
            status=DocumentStatus.ACTIVE if (latest and active) else DocumentStatus.INACTIVE,
        ).insert()


def _registry(*, entry_removal_fails: bool = False):
    registry = MagicMock()
    registry.get_namespace_deletion_mode = AsyncMock(return_value="full")
    registry.hard_delete_entry = AsyncMock(
        side_effect=RuntimeError("registry down") if entry_removal_fails else None,
    )
    return patch("document_store.services.document_service.get_registry_client",
                 return_value=registry), registry


async def _versions(doc_id: str) -> list[int]:
    return sorted(d.version for d in await Document.find({"document_id": doc_id}).to_list())


@pytest.mark.asyncio
async def test_hard_deleting_what_does_not_exist_answers_false(clean):
    await _doc("D1", 2)
    patched, _ = _registry()
    with patched:
        assert await DocumentService().delete_document("NOPE", hard_delete=True) is False
        assert await DocumentService().delete_document("D1", hard_delete=True, version=7) is False
    assert await _versions("D1") == [1, 2]


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, 1], ids=["all-versions", "last-version"])
async def test_a_failed_registry_removal_does_not_fail_the_delete(clean, version):
    await _doc("D1", 1)
    patched, registry = _registry(entry_removal_fails=True)
    with patched:
        assert await DocumentService().delete_document("D1", hard_delete=True, version=version) is True
    assert await _versions("D1") == []
    registry.hard_delete_entry.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_forced_delete_needs_no_collectors(clean):
    await _doc("D1", 1)
    patched, _ = _registry()
    with patched, patch.object(DocumentService, "_hard_delete_referrers",
                               AsyncMock(return_value=[{"documents": 2}])):
        assert await DocumentService().delete_document("D1", hard_delete=True, force=True) is True
    assert await _versions("D1") == []


@pytest.mark.asyncio
async def test_a_soft_delete_is_idempotent_and_needs_no_collector(clean):
    # A document with no active version is already deleted: deleting it again
    # answers True, like the delete that retired it, and records nothing a
    # second time. Only a document with no version at all is not there
    # (CASE-1271, finding e93d63c6 R1).
    await _doc("GONE", 1, active=False)
    await _doc("LIVE", 2)
    service = DocumentService()
    record = AsyncMock(return_value=["w"])
    with patch.object(DocumentService, "_record_lifecycle", record):
        assert await service.delete_document("GONE") is True
        record.assert_not_awaited()
        assert await service.delete_document("NOPE") is False
        assert await service.delete_document("LIVE") is True
        assert await service.delete_document("LIVE") is True
    assert record.await_count == 1
    rows = await Document.find({"document_id": "LIVE"}).to_list()
    assert all(r.status == DocumentStatus.INACTIVE for r in rows)


@pytest.mark.asyncio
async def test_a_soft_delete_that_loses_to_a_concurrent_one_answers_deleted(clean):
    # The other request retired the active version between this one's read
    # and its write: the document is deleted, which is what was asked.
    await _doc("RACED", 1)
    service = DocumentService()
    with patch.object(DocumentService, "_record_lifecycle", AsyncMock(return_value=[])), \
            patch.object(DocumentService, "_retire_latest", AsyncMock(return_value=None)):
        assert await service.delete_document("RACED") is True
