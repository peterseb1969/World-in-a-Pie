"""Upload filenames (CASE-1155).

1. The extension blocklist judged the raw name, but the sanitiser strips
   trailing dots, spaces and control characters, so "setup.exe." passed and was
   stored as "setup.exe". It now judges the stored name (last extension only —
   "invoice.exe.pdf" is accepted deliberately).
2. A filename outside latin-1 went raw into the S3 metadata header and failed
   the upload; metadata values are now percent-encoded.
3. Content-Disposition carried the raw name; every download now sends an ASCII
   fallback plus an RFC 5987 ``filename*``.
"""

from unittest.mock import AsyncMock
from urllib.parse import unquote

import pytest
from fastapi import HTTPException

from document_store.models.file import File, FileStatus
from document_store.services.file_storage_client import FileStorageClient
from document_store.services.file_validation import (
    blocked_extension,
    content_disposition,
    validate_upload_content_type,
)
from tests.test_files import _insert_file, auth_headers, file_client  # noqa: F401 — fixtures

STORED_AS_EXE = ["setup.exe.", "setup.exe ", "setup.exe\t", "x.EXE..", "dir/setup.exe. ."]


@pytest.mark.parametrize("name", STORED_AS_EXE)
def test_a_name_stored_with_a_blocked_extension_is_refused(name):
    assert blocked_extension(name) == ".exe"
    with pytest.raises(HTTPException) as exc:
        validate_upload_content_type("application/octet-stream", name)
    assert exc.value.status_code == 415


@pytest.mark.parametrize("name", ["invoice.exe.pdf", "deploy.sh.zip", "report.pdf", "README"])
def test_only_the_last_extension_counts(name):
    assert blocked_extension(name) is None
    validate_upload_content_type("application/octet-stream", name)  # must not raise


@pytest.mark.parametrize("name", ["report.pdf", "café.pdf", "отчёт 2026.xlsx", "報告.docx", 'a;b "c".txt'])
def test_content_disposition_carries_any_name(name):
    value = content_disposition(name)
    value.encode("ascii")  # an HTTP header value any server can write
    fallback = value.split('filename="', 1)[1].split('"', 1)[0]
    assert '"' not in fallback and "\\" not in fallback
    encoded = value.split("filename*=UTF-8''", 1)[1]
    assert unquote(encoded) == name
    assert value.startswith("attachment; ")


# ── The storage client ───────────────────────────────────────────────────


class _S3:
    def __init__(self):
        self.put = None
        self.presign = None

    async def put_object(self, **kwargs):
        self.put = kwargs

    async def generate_presigned_url(self, op, Params, ExpiresIn):
        self.presign = Params
        return "https://store/presigned"


class _Session:
    def __init__(self, s3):
        self.s3 = s3

    def create_client(self, *args, **kwargs):
        s3 = self.s3

        class _CM:
            async def __aenter__(self):
                return s3

            async def __aexit__(self, *exc):
                return False

        return _CM()


def _client() -> tuple[FileStorageClient, _S3]:
    s3 = _S3()
    c = FileStorageClient(endpoint_url="http://wip-garage:3900", access_key="k", secret_key="s", bucket="b")
    c._session = _Session(s3)  # type: ignore[assignment]
    c._ensure_bucket_once = AsyncMock()  # type: ignore[method-assign]
    return c, s3


@pytest.mark.asyncio
async def test_upload_metadata_is_header_safe_for_any_name():
    c, s3 = _client()
    await c.upload("k", b"x", "text/plain", metadata={"filename": "報告 2026.pdf", "uploaded_by": "José"})
    meta = s3.put["Metadata"]
    for value in meta.values():
        value.encode("ascii")
    assert unquote(meta["filename"]) == "報告 2026.pdf"
    assert unquote(meta["uploaded_by"]) == "José"


@pytest.mark.asyncio
async def test_a_presigned_download_carries_the_encoded_name():
    c, s3 = _client()
    await c.generate_download_url("k", filename="отчёт.pdf")
    disposition = s3.presign["ResponseContentDisposition"]
    disposition.encode("ascii")
    assert unquote(disposition.split("filename*=UTF-8''", 1)[1]) == "отчёт.pdf"


# ── Through the API ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_uploading_setup_exe_dot_is_refused_and_stores_nothing(file_client, auth_headers):  # noqa: F811
    before = await File.find_all().count()
    resp = await file_client.post(
        "/api/document-store/files", headers=auth_headers,
        files={"file": ("setup.exe.", b"MZ...", "application/octet-stream")},
        data={"namespace": "wip"},
    )
    assert resp.status_code == 415, resp.text
    assert await File.find_all().count() == before


@pytest.mark.asyncio
async def test_downloading_a_non_latin1_name_sends_rfc5987(file_client, auth_headers):  # noqa: F811
    doc = await _insert_file(filename="報告.pdf", status=FileStatus.ACTIVE, size_bytes=3)

    async def chunks(_key, chunk_size=64 * 1024):
        yield b"abc"

    import document_store.services.file_service as fs_mod
    fs_mod.get_file_storage_client().download_stream = chunks
    resp = await file_client.get(f"/api/document-store/files/{doc.file_id}/content", headers=auth_headers)

    assert resp.status_code == 200, resp.text
    disposition = resp.headers["content-disposition"]
    assert unquote(disposition.split("filename*=UTF-8''", 1)[1]) == "報告.pdf"
