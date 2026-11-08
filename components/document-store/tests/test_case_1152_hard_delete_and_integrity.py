"""File hard delete and the integrity check do not fail open (CASE-1152).

1. A delete the object store refused was swallowed as "already gone": the
   record went, the bytes stayed, and the API answered permanently_deleted.
2. Hard delete checked only that the file was inactive, so a referenced file
   (force-soft-deleted) could be removed under its documents.
3. The integrity check said healthy when the store refused every object check.
"""

from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError

from document_store.models.document import DocumentStatus
from document_store.models.file import File, FileStatus
from document_store.services.document_service import DocumentService
from document_store.services.file_service import FileService, FileServiceError
from document_store.services.file_storage_client import FileStorageClient, FileStorageError
from tests.test_case_1137_hard_delete_holds import _id, _row
from tests.test_files import _insert_file, auth_headers, file_client  # noqa: F401 — fixtures


def _storage():
    import document_store.services.file_service as fs_mod
    return fs_mod.get_file_storage_client()


async def _inactive(**kwargs) -> File:
    return await _insert_file(status=FileStatus.INACTIVE, **kwargs)


# ── 1. A refused delete is an error, and the record stays ────────────────


@pytest.mark.asyncio
async def test_a_refused_storage_delete_keeps_the_record_and_is_a_502(file_client, auth_headers):  # noqa: F811
    doc = await _inactive()
    _storage().delete.side_effect = FileStorageError("Failed to delete file: AccessDenied")
    try:
        resp = await file_client.delete(f"/api/document-store/files/{doc.file_id}/hard", headers=auth_headers)
    finally:
        _storage().delete.side_effect = None

    assert resp.status_code == 502, resp.text
    assert "refused" in resp.json()["detail"]
    assert await File.find_one({"file_id": doc.file_id}) is not None


@pytest.mark.asyncio
async def test_an_object_already_gone_still_deletes_the_record(file_client, auth_headers):  # noqa: F811
    from document_store.services.file_storage_client import FileStorageNotFoundError

    doc = await _inactive()
    _storage().delete.side_effect = FileStorageNotFoundError("gone")
    try:
        resp = await file_client.delete(f"/api/document-store/files/{doc.file_id}/hard", headers=auth_headers)
    finally:
        _storage().delete.side_effect = None

    assert resp.status_code == 200, resp.text
    assert await File.find_one({"file_id": doc.file_id}) is None


@pytest.mark.asyncio
async def test_the_client_tells_a_missing_object_from_a_refusal():
    from document_store.services.file_storage_client import FileStorageNotFoundError

    class _S3:
        def __init__(self, code):
            self.code = code

        async def delete_object(self, **_):
            raise ClientError({"Error": {"Code": self.code}}, "DeleteObject")

    class _Session:
        def __init__(self, s3):
            self.s3 = s3

        def create_client(self, *a, **k):
            s3 = self.s3

            class _CM:
                async def __aenter__(self):
                    return s3

                async def __aexit__(self, *exc):
                    return False

            return _CM()

    def client(code):
        c = FileStorageClient(endpoint_url="http://g:3900", access_key="k", secret_key="s", bucket="b")
        c._session = _Session(_S3(code))  # type: ignore[assignment]
        return c

    with pytest.raises(FileStorageNotFoundError):
        await client("NoSuchKey").delete("k")
    with pytest.raises(FileStorageError) as exc:
        await client("AccessDenied").delete("k")
    assert not isinstance(exc.value, FileStorageNotFoundError)


# ── 2. A referenced file is not hard-deleted ─────────────────────────────


@pytest.mark.asyncio
async def test_a_file_an_active_document_references_is_refused(file_client, auth_headers):  # noqa: F811
    doc = await _inactive(reference_count=0)
    holder = _id()
    await _row(holder, 1, DocumentStatus.ACTIVE, files=[doc.file_id])

    resp = await file_client.delete(f"/api/document-store/files/{doc.file_id}/hard", headers=auth_headers)

    assert resp.status_code == 409, resp.text
    assert holder in resp.json()["detail"]
    assert await File.find_one({"file_id": doc.file_id}) is not None
    _storage().delete.assert_not_called()


@pytest.mark.asyncio
async def test_a_force_deleted_file_with_a_count_is_refused(file_client):  # noqa: F811
    doc = await _inactive(reference_count=2)
    with pytest.raises(FileServiceError) as exc:
        await FileService().hard_delete_file(doc.file_id)
    assert exc.value.code == "invalid_state"
    assert exc.value.details == {"rule": "referenced"}


@pytest.mark.asyncio
async def test_a_documents_hard_delete_reports_bytes_it_could_not_remove(file_client):  # noqa: F811
    doc = await _insert_file(status=FileStatus.ACTIVE, reference_count=0)
    dying = _id()
    await _row(dying, 1, DocumentStatus.ACTIVE, files=[doc.file_id])
    _storage().delete.side_effect = FileStorageError("AccessDenied")
    warnings: list[str] = []
    try:
        with patch("document_store.services.file_storage_client.is_file_storage_enabled", return_value=True):
            await DocumentService()._hard_delete_orphaned_files(
                [{"file_id": doc.file_id}], FileService(), deleting=dying, warnings=warnings,
            )
    finally:
        _storage().delete.side_effect = None

    assert len(warnings) == 1 and doc.file_id in warnings[0], warnings
    stored = await File.find_one({"file_id": doc.file_id})
    assert stored is not None and stored.status == FileStatus.INACTIVE


# ── 3. Integrity says what it could not check ────────────────────────────


@pytest.mark.asyncio
async def test_integrity_is_incomplete_when_the_store_refuses_every_check(file_client, auth_headers):  # noqa: F811
    for _ in range(3):
        await _insert_file(status=FileStatus.ACTIVE, reference_count=1)
    _storage().exists.side_effect = FileStorageError("AccessDenied")
    try:
        resp = await file_client.get("/api/document-store/files/health/integrity", headers=auth_headers)
    finally:
        _storage().exists.side_effect = None

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "incomplete", body
    assert body["summary"]["storage_check_failed"] == 3
    assert body["summary"]["storage_checked"] == 0


@pytest.mark.asyncio
async def test_integrity_counts_files_beyond_the_sweep(file_client, auth_headers, monkeypatch):  # noqa: F811
    monkeypatch.setattr(FileService, "STORAGE_SWEEP_LIMIT", 2)
    for _ in range(3):
        await _insert_file(status=FileStatus.ACTIVE, reference_count=1)

    body = (await file_client.get("/api/document-store/files/health/integrity", headers=auth_headers)).json()

    assert body["summary"]["storage_checked"] == 2
    assert body["summary"]["storage_not_checked"] == 1
    assert body["status"] == "incomplete", body
