"""A write counts its files before its document is written.

A document write validated its files, inserted the document, and only then
raised their reference counts. A hard delete reclaiming a file's bytes marks
the file inactive guarded on its count being 0, looks for referrers, and
deletes the bytes. In between the two, a write that had validated the file
while it was active inserted its document after the referrer check: the
document then referenced bytes that were gone. Now every write primitive
counts the files first and is refused when one went inactive meanwhile, so
the reclaim's guard either misses (the file is kept) or the write is refused.
"""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from beanie import Document as BeanieDocument

from document_store.models.document import Document, DocumentMetadata, DocumentStatus
from document_store.models.file import File, FileStatus
from document_store.services.document_service import DocumentService
from document_store.services.errors import DocumentStoreError
from tests.test_case_1137_hard_delete_holds import NS, _count, _file, _id


@pytest.fixture
def storage_on():
    with (
        patch("document_store.services.document_service.is_file_storage_enabled", return_value=True),
        patch("document_store.services.file_storage_client.is_file_storage_enabled", return_value=True),
    ):
        yield


def _insert_args(file_id: str, document_id: str | None = None) -> dict:
    return {
        "namespace": NS, "document_id": document_id or _id(), "template_id": _id(),
        "template_version": 1, "template_value": "T1273", "identity_hash": f"h-{_id()}",
        "version": 1, "data": {}, "term_references": [], "references": [],
        "file_references": [{"field_path": "f", "file_id": file_id}],
        "metadata": DocumentMetadata(), "actor": "test", "now": datetime.now(UTC),
    }


async def _retire(file_id: str) -> None:
    """What a reclaim does first: the file goes inactive at count 0."""
    marked = await File.get_motor_collection().update_one(
        {"file_id": file_id, "reference_count": {"$lte": 0}},
        {"$set": {"status": FileStatus.INACTIVE.value}},
    )
    assert marked.matched_count == 1


@pytest.mark.asyncio
async def test_an_insert_counts_its_file_before_the_document_lands(client, storage_on):
    file_id = await _file(0)
    seen: list[int] = []
    real_insert = BeanieDocument.insert

    async def observe(self, *a, **kw):
        seen.append(await _count(file_id))
        return await real_insert(self, *a, **kw)

    with patch.object(Document, "insert", observe):
        await DocumentService()._insert_with_retry(**_insert_args(file_id))

    assert seen == [1]
    assert await _count(file_id) == 1
    # The reclaim's first step now misses: the file is counted.
    retired = await File.get_motor_collection().update_one(
        {"file_id": file_id, "reference_count": {"$lte": 0}}, {"$set": {"status": "inactive"}},
    )
    assert retired.matched_count == 0


@pytest.mark.asyncio
async def test_an_insert_of_a_file_retired_since_validation_is_refused(client, storage_on):
    file_id = await _file(0)
    await _retire(file_id)
    args = _insert_args(file_id)

    with pytest.raises(DocumentStoreError) as refused:
        await DocumentService()._insert_with_retry(**args)

    assert refused.value.code == "validation_failed"
    assert refused.value.details == {"rule": "file_deleted"}
    assert await _count(file_id) == 0
    assert await Document.find_one({"document_id": args["document_id"]}) is None


@pytest.mark.asyncio
async def test_a_failed_insert_releases_the_count(client, storage_on):
    file_id = await _file(0)

    async def boom(self, *a, **kw):
        raise RuntimeError("insert failed")

    with patch.object(Document, "insert", boom), pytest.raises(RuntimeError):
        await DocumentService()._insert_with_retry(**_insert_args(file_id))

    assert await _count(file_id) == 0


@pytest.mark.asyncio
async def test_an_in_place_write_counts_first_and_releases_when_it_loses(client, storage_on):
    file_id = await _file(0)
    doc = Document(
        namespace=NS, document_id=_id(), template_id=_id(), template_version=1,
        template_value="T1273", identity_hash=f"h-{_id()}", version=1, data={},
        status=DocumentStatus.ACTIVE,
    )
    await doc.insert()
    doc.file_references = [{"field_path": "f", "file_id": file_id}]
    svc = DocumentService()

    # Another write moved the revision on: this one does not land.
    assert not await svc._write_in_place(doc, 41, DocumentStatus.ACTIVE)
    assert await _count(file_id) == 0

    assert await svc._write_in_place(doc, None, DocumentStatus.ACTIVE)
    assert await _count(file_id) == 1

    await _retire(other := await _file(0))
    doc.file_references = [{"field_path": "f", "file_id": other}]
    with pytest.raises(DocumentStoreError):
        await svc._write_in_place(doc, doc.revision, DocumentStatus.ACTIVE)
    assert await _count(other) == 0


# ── The reclaim's repair and its failures ───────────────────────────────


@pytest.mark.asyncio
async def test_a_kept_file_gets_the_count_of_the_references_held(client, storage_on):
    """The repair counted referring documents, at most 10: a document using
    the file in two fields counted once, and twelve documents counted ten."""
    from document_store.services.file_service import FileService

    file_id = await _file(0)
    deleting = _id()
    await Document(
        namespace=NS, document_id=deleting, template_id=_id(), template_version=1,
        template_value="T1273", identity_hash=f"h-{deleting}", version=1, data={},
        file_references=[{"field_path": "f", "file_id": file_id}], status=DocumentStatus.ACTIVE,
    ).insert()
    for _ in range(12):
        holder = _id()
        await Document(
            namespace=NS, document_id=holder, template_id=_id(), template_version=1,
            template_value="T1273", identity_hash=f"h-{holder}", version=1, data={},
            file_references=[{"field_path": "a", "file_id": file_id},
                             {"field_path": "b", "file_id": file_id}],
            status=DocumentStatus.ACTIVE,
        ).insert()

    await DocumentService()._hard_delete_orphaned_files(
        [{"file_id": file_id}], FileService(), deleting=deleting,
    )

    stored = await File.find_one({"file_id": file_id})
    assert (stored.status, stored.reference_count) == (FileStatus.ACTIVE, 24)


@pytest.mark.asyncio
async def test_a_file_whose_bytes_the_store_kept_is_reported(client, storage_on):
    """A file left inactive because the store refused to delete its bytes
    looked like any soft-deleted file: no scan reported it."""
    from document_store.services.file_service import FileService, FileServiceError

    file_id = await _file(0)
    deliberate = await _file(0)
    await _retire(deliberate)  # soft-deleted on purpose: not an issue
    warnings: list[str] = []

    async def refused(self, *_a, **_kw):
        raise FileServiceError("access denied", rule="storage_refused")

    with patch.object(FileService, "hard_delete_file", refused):
        await DocumentService()._hard_delete_orphaned_files(
            [{"file_id": file_id}], FileService(), deleting=_id(), warnings=warnings,
        )

    assert warnings and file_id in warnings[0]
    report = await FileService().check_integrity()
    unreclaimed = [i for i in report.issues if i.type == "unreclaimed_file"]
    assert [i.file_id for i in unreclaimed] == [file_id]
    assert "access denied" in unreclaimed[0].message
    assert report.summary["unreclaimed_file"] == 1


@pytest.mark.asyncio
async def test_a_reclaim_that_failed_before_the_delete_is_not_reported_as_unreclaimed(client, storage_on):
    """A database failure after the inactive-mark never asked the store to
    delete anything: the bytes are untouched, so the file is not stamped as
    one whose bytes the store kept (CASE-1355 R2)."""
    from document_store.services.file_service import FileService

    file_id = await _file(0)
    warnings: list[str] = []

    async def db_blip(self, *_a, **_kw):
        raise RuntimeError("ServerSelectionTimeoutError")

    with patch.object(FileService, "live_referrers", db_blip):
        await DocumentService()._hard_delete_orphaned_files(
            [{"file_id": file_id}], FileService(), deleting=_id(), warnings=warnings,
        )

    assert warnings and file_id in warnings[0]
    assert (await File.find_one({"file_id": file_id})).reclaim_failed is None
    report = await FileService().check_integrity()
    # The scan covers the whole database; other tests leave their own files.
    assert not [i for i in report.issues if i.type == "unreclaimed_file" and i.file_id == file_id]


@pytest.mark.asyncio
async def test_a_store_unreachable_for_the_delete_is_reported(client, storage_on):
    """An object store that could not be reached for the delete left the
    bytes, as a refusal does: the file is reported for another hard delete."""
    from document_store.services.file_service import FileService
    from document_store.services.file_storage_client import FileStorageUnavailableError

    file_id = await _file(0)

    async def unreachable(self, *_a, **_kw):
        raise FileStorageUnavailableError("Object store unreachable")

    with patch.object(FileService, "hard_delete_file", unreachable):
        await DocumentService()._hard_delete_orphaned_files(
            [{"file_id": file_id}], FileService(), deleting=_id(),
        )

    assert (await File.find_one({"file_id": file_id})).reclaim_failed == "Object store unreachable"


@pytest.mark.parametrize("raised", ["document", "file"])
@pytest.mark.asyncio
async def test_a_typed_refusal_during_patch_keeps_its_code(client, raised):
    """A refusal raised after a PATCH's validation (a file gone inactive
    before the write held it) answered internal_error; a create answers it
    with its own code (CASE-1355 R1)."""
    from document_store.models.api_models import PatchDocumentItem
    from document_store.services.file_service import FileServiceError

    error = (
        DocumentStoreError("File has been deleted: F-1", "validation_failed", rule="file_deleted")
        if raised == "document"
        else FileServiceError("File F-1 is inactive", "validation_failed", rule="file_deleted")
    )

    async def refuse(self, *_a, **_kw):
        raise error

    with patch.object(DocumentService, "_patch_one", refuse):
        response = await DocumentService().bulk_patch(
            [PatchDocumentItem(document_id=_id(), patch={"x": 1})],
        )

    [item] = response.results
    assert (item.error_code, item.details) == ("validation_failed", {"rule": "file_deleted"})
