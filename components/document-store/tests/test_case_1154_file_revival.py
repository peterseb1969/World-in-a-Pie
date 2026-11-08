"""A soft-deleted file can come back (CASE-1154).

Inactive is retired, not gone. Re-uploading a soft-deleted file's bytes used to
return the inactive record with HTTP 200, and nothing could ever move a file
out of inactive. Now:

- an upload of those bytes revives the file under the same file_id, writes the
  bytes again, takes the metadata fields it sends, and publishes file.updated;
- POST /files/reactivate brings files back without their bytes, per item,
  refusing one whose bytes are gone from storage;
- a revived file is active while documents still reference it (a force-delete
  keeps the count), orphan otherwise.
"""

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from document_store.models.file import File, FileStatus
from document_store.services.nats_client import EventType
from tests.test_files import _insert_file, auth_headers, file_client  # noqa: F401 — fixtures

FILES = "/api/document-store/files"
REACTIVATE = f"{FILES}/reactivate"
CONTENT = b"case 1154 bytes"
CHECKSUM = hashlib.sha256(CONTENT).hexdigest()


def _storage():
    # Through the module, at call time: the fixture patches the attribute.
    import document_store.services.file_service as fs_mod
    return fs_mod.get_file_storage_client()


def _registry_has(file_id: str):
    """The Registry already holds these bytes' checksum key under file_id —
    which it does for a soft-deleted file: soft-delete never releases it."""
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"results": [{"status": "already_exists", "registry_id": file_id}]}
    http = AsyncMock()
    http.post = AsyncMock(return_value=response)
    http.__aenter__ = AsyncMock(return_value=http)
    http.__aexit__ = AsyncMock(return_value=False)
    return patch("document_store.services.file_service.httpx.AsyncClient", return_value=http)


def _events():
    import document_store.services.file_service as fs_mod
    return [call.args[0] for call in fs_mod.publish_file_event.call_args_list]


async def _upload(client, headers, file_id: str, **form):
    with _registry_has(file_id):
        return await client.post(
            FILES, headers=headers,
            files={"file": ("again.txt", CONTENT, "text/plain")},
            data={"namespace": "wip", **form},
        )


async def _deleted(**kwargs) -> File:
    doc = await _insert_file(checksum=CHECKSUM, filename="orig.txt", content_type="text/plain",
                             status=FileStatus.INACTIVE, **kwargs)
    return doc


# ── Re-upload revives ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reupload_of_a_soft_deleted_files_bytes_revives_it(file_client, auth_headers):  # noqa: F811
    deleted = await _deleted()
    uploads_before = _storage().upload.await_count

    resp = await _upload(file_client, auth_headers, deleted.file_id)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["file_id"] == deleted.file_id
    assert body["status"] == "orphan"
    stored = await File.find_one({"file_id": deleted.file_id})
    assert stored.status == FileStatus.ORPHAN
    # The bytes are written again (the object may be gone), under the same key.
    assert _storage().upload.await_count == uploads_before + 1
    assert _storage().upload.await_args.kwargs["storage_key"] == deleted.storage_key
    assert EventType.FILE_UPDATED in _events()
    # And it is usable again.
    download = await file_client.get(f"{FILES}/{deleted.file_id}/download", headers=auth_headers)
    assert download.status_code == 200, download.text


@pytest.mark.asyncio
async def test_a_force_deleted_referenced_file_revives_as_active(file_client, auth_headers):  # noqa: F811
    deleted = await _deleted(reference_count=2)

    resp = await _upload(file_client, auth_headers, deleted.file_id)

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"
    assert (await File.find_one({"file_id": deleted.file_id})).reference_count == 2


@pytest.mark.asyncio
async def test_revival_takes_the_metadata_the_upload_sends(file_client, auth_headers):  # noqa: F811
    deleted = await _deleted(description="old", tags=["keep"], category="old-cat")

    resp = await _upload(file_client, auth_headers, deleted.file_id, description="new")

    assert resp.status_code == 200, resp.text
    stored = await File.find_one({"file_id": deleted.file_id})
    assert stored.metadata.description == "new"
    assert stored.metadata.tags == ["keep"]       # omitted: kept
    assert stored.metadata.category == "old-cat"  # omitted: kept
    assert stored.filename == "orig.txt"          # identity is the bytes, not the name


@pytest.mark.asyncio
async def test_a_duplicate_of_a_live_file_is_unchanged(file_client, auth_headers):  # noqa: F811
    live = await _insert_file(checksum=CHECKSUM, description="old")
    uploads_before = _storage().upload.await_count

    resp = await _upload(file_client, auth_headers, live.file_id, description="new")

    assert resp.status_code == 200, resp.text
    assert resp.json()["file_id"] == live.file_id
    assert (await File.find_one({"file_id": live.file_id})).metadata.description == "old"
    assert _storage().upload.await_count == uploads_before


# ── POST /files/reactivate ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reactivate_brings_a_soft_deleted_file_back(file_client, auth_headers):  # noqa: F811
    deleted = await _deleted()

    resp = await file_client.post(REACTIVATE, headers=auth_headers, json=[{"id": deleted.file_id}])

    assert resp.status_code == 200, resp.text
    result = resp.json()["results"][0]
    assert result["status"] == "reactivated", result
    assert (await File.find_one({"file_id": deleted.file_id})).status == FileStatus.ORPHAN
    assert EventType.FILE_UPDATED in _events()

    again = await file_client.post(REACTIVATE, headers=auth_headers, json=[{"id": deleted.file_id}])
    assert again.json()["results"][0]["status"] == "unchanged"


@pytest.mark.asyncio
async def test_reactivate_refuses_a_file_whose_bytes_are_gone(file_client, auth_headers):  # noqa: F811
    deleted = await _deleted()
    _storage().exists.return_value = False
    try:
        resp = await file_client.post(REACTIVATE, headers=auth_headers, json=[{"id": deleted.file_id}])
    finally:
        _storage().exists.return_value = True

    result = resp.json()["results"][0]
    assert result["status"] == "error", result
    assert result["error_code"] == "invalid_state"
    assert result["details"]["rule"] == "storage_missing"
    assert (await File.find_one({"file_id": deleted.file_id})).status == FileStatus.INACTIVE


@pytest.mark.asyncio
async def test_reactivate_an_unknown_file_is_not_found(file_client, auth_headers):  # noqa: F811
    resp = await file_client.post(
        REACTIVATE, headers=auth_headers, json=[{"id": "019d0000-0000-7000-8000-000000000000"}],
    )
    assert resp.json()["results"][0]["error_code"] == "not_found"


@pytest.mark.asyncio
async def test_reactivate_answers_permission_per_item(file_client):  # noqa: F811
    """A reader without write is forbidden, a caller who cannot read gets
    not_found, and neither stops the rest of the batch."""
    closed = await _insert_file(namespace="xns-closed", status=FileStatus.INACTIVE)
    mine = await _insert_file(namespace="wip", status=FileStatus.INACTIVE)

    reader_only = await file_client.post(
        REACTIVATE, headers={"X-API-Key": "test_xns_only_key"}, json=[{"id": closed.file_id}],
    )
    assert reader_only.json()["results"][0]["error_code"] == "forbidden", reader_only.text

    no_access = await file_client.post(
        REACTIVATE, headers={"X-API-Key": "test_scoped_key"}, json=[{"id": mine.file_id}],
    )
    assert no_access.json()["results"][0]["error_code"] == "not_found", no_access.text

    mixed = await file_client.post(
        REACTIVATE, headers={"X-API-Key": "test_xns_writer_key"},
        json=[{"id": closed.file_id}, {"id": mine.file_id}],
    )
    assert mixed.status_code == 200, mixed.text
    first, second = mixed.json()["results"]
    assert first["status"] == "error" and first["error_code"] == "not_found", first
    assert second["status"] == "reactivated", second
    assert (await File.find_one({"file_id": closed.file_id})).status == FileStatus.INACTIVE
