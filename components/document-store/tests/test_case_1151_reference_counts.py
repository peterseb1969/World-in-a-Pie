"""File reference counts and the bytes they guard (CASE-1151).

update_reference_count read the count, added in Python and saved the whole
record, so two documents attaching one file at once counted 1. Reclaiming a
file's bytes trusted that count alone — and the count passes through 0 during
an update that keeps a file — and the reclaim path's own save could rewind a
concurrent increment. Now the count is one atomic database update, writes to a
file record set only the fields they change, and bytes are reclaimed only when
no active document other than the one being deleted references the file.
"""

import asyncio

import pytest

from document_store.models.document import DocumentStatus
from document_store.models.file import File, FileStatus
from document_store.services.document_service import DocumentService
from document_store.services.file_service import FileService
from tests.test_case_1137_hard_delete_holds import _count, _file, _id, _row, full_mode  # noqa: F401


@pytest.mark.asyncio
async def test_parallel_attaches_all_count(client):
    file_id = await _file(0)
    svc = FileService()

    await asyncio.gather(*(svc.update_reference_count(file_id, +1) for _ in range(20)))

    stored = await File.find_one({"file_id": file_id})
    assert stored.reference_count == 20
    assert stored.status == FileStatus.ACTIVE


@pytest.mark.asyncio
async def test_parallel_attach_and_release_net_out_and_never_go_negative(client):
    file_id = await _file(5)
    svc = FileService()

    await asyncio.gather(*(svc.update_reference_count(file_id, d) for d in [+1] * 10 + [-1] * 10))
    assert await _count(file_id) == 5

    await asyncio.gather(*(svc.update_reference_count(file_id, -1) for _ in range(9)))
    stored = await File.find_one({"file_id": file_id})
    assert stored.reference_count == 0
    assert stored.status == FileStatus.ORPHAN


@pytest.mark.asyncio
async def test_a_reference_never_reactivates_an_inactive_file(client):
    file_id = await _file(0)
    await File.get_motor_collection().update_one(
        {"file_id": file_id}, {"$set": {"status": FileStatus.INACTIVE.value}},
    )
    await FileService().update_reference_count(file_id, +1)
    assert (await File.find_one({"file_id": file_id})).status == FileStatus.INACTIVE


@pytest.mark.asyncio
async def test_a_metadata_write_keeps_a_count_changed_meanwhile(client, monkeypatch):
    from document_store.models.api_models import UpdateFileMetadataRequest

    file_id = await _file(1)
    svc = FileService()
    real = svc._set_fields

    async def attach_then_write(file_doc, fields):
        await svc.update_reference_count(file_id, +1)  # lands between read and write
        await real(file_doc, fields)

    monkeypatch.setattr(svc, "_set_fields", attach_then_write)
    await svc.update_metadata(file_id, UpdateFileMetadataRequest(description="d"))

    stored = await File.find_one({"file_id": file_id})
    assert stored.reference_count == 2
    assert stored.metadata.description == "d"


@pytest.mark.asyncio
async def test_a_file_another_active_document_holds_is_not_reclaimed(client, full_mode):  # noqa: F811 — fixture
    """The count says 0 (drifted, or mid-update) but document B still holds the
    file: deleting A must not take B's bytes, and the count is repaired."""
    removed = full_mode
    file_id = await _file(0)
    a, b = _id(), _id()
    await _row(a, 1, DocumentStatus.ACTIVE, files=[file_id])
    await _row(b, 1, DocumentStatus.ACTIVE, files=[file_id])

    await DocumentService()._hard_delete_orphaned_files(
        [{"file_id": file_id}], FileService(), deleting=a,
    )

    assert removed == []
    stored = await File.find_one({"file_id": file_id})
    assert stored.status == FileStatus.ACTIVE
    assert stored.reference_count >= 1


@pytest.mark.asyncio
async def test_a_file_only_the_deleted_document_holds_is_reclaimed(client, full_mode):  # noqa: F811 — fixture
    removed = full_mode
    file_id = await _file(0)
    a = _id()
    await _row(a, 1, DocumentStatus.ACTIVE, files=[file_id])
    await _row(a, 2, DocumentStatus.ACTIVE, files=[file_id])

    await DocumentService()._hard_delete_orphaned_files(
        [{"file_id": file_id}], FileService(), deleting=a,
    )

    assert removed == [file_id]


@pytest.mark.asyncio
async def test_deleting_one_version_keeps_a_file_the_latest_version_holds(client, full_mode):  # noqa: F811 — fixture
    removed = full_mode
    file_id = await _file(0)
    a = _id()
    await _row(a, 1, DocumentStatus.INACTIVE, files=[file_id])
    await _row(a, 2, DocumentStatus.ACTIVE, files=[file_id])

    await DocumentService()._hard_delete_orphaned_files(
        [{"file_id": file_id}], FileService(), deleting=a, deleting_version=1,
    )

    assert removed == []


@pytest.mark.asyncio
async def test_a_counted_file_is_not_reclaimed(client, full_mode):  # noqa: F811 — fixture
    removed = full_mode
    file_id = await _file(1)

    await DocumentService()._hard_delete_orphaned_files(
        [{"file_id": file_id}], FileService(), deleting=_id(),
    )

    assert removed == []
    assert (await File.find_one({"file_id": file_id})).status == FileStatus.ACTIVE
