"""document-store files lows (CASE-1158)."""

import logging

import pytest
from botocore.exceptions import ClientError

from document_store.models.file import File
from document_store.services.file_storage_client import (
    FileStorageClient,
    FileStorageUnavailableError,
)
from tests.test_files import _insert_file, auth_headers, file_client  # noqa: F401 — fixtures

FILES = "/api/document-store/files"


@pytest.mark.asyncio
async def test_sort_by_size_sorts_by_size(file_client, auth_headers):  # noqa: F811
    for size in (300, 100, 200):
        await _insert_file(size_bytes=size)

    resp = await file_client.get(FILES, headers=auth_headers,
                                 params={"namespace": "wip", "sort_by": "size", "sort_order": "asc"})

    assert resp.status_code == 200, resp.text
    assert [f["size_bytes"] for f in resp.json()["items"]] == [100, 200, 300]


@pytest.mark.asyncio
@pytest.mark.parametrize("any_type", ["*/*", "*"])
async def test_the_any_type_filter_filters_nothing(file_client, auth_headers, any_type):  # noqa: F811
    await _insert_file(content_type="application/pdf")
    await _insert_file(content_type="image/png")

    resp = await file_client.get(FILES, headers=auth_headers,
                                 params={"namespace": "wip", "content_type": any_type})

    assert resp.json()["total"] == 2, resp.text


@pytest.mark.asyncio
async def test_get_file_checks_the_namespace_of_the_record_it_read(file_client):  # noqa: F811
    doc = await _insert_file(namespace="xns-closed")
    resp = await file_client.get(f"{FILES}/{doc.file_id}", headers={"X-API-Key": "test_xns_writer_key"})
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,item", [
    ("PATCH", "", {"file_id": "x"}),
    ("DELETE", "", {"id": "x"}),
    ("POST", "/reactivate", {"id": "x"}),
])
async def test_bulk_file_bodies_are_capped(file_client, auth_headers, method, path, item):  # noqa: F811
    resp = await file_client.request(method, f"{FILES}{path}", headers=auth_headers, json=[item] * 1001)
    assert resp.status_code == 422, resp.text


def test_files_have_indexes_for_id_and_storage_key_lookups():
    names = {i.document["name"] for i in File.Settings.indexes}
    assert {"file_id_idx", "file_storage_key_idx"} <= names


# ── The storage client ───────────────────────────────────────────────────


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


def _client(s3) -> FileStorageClient:
    c = FileStorageClient(endpoint_url="http://g:3900", access_key="k", secret_key="s", bucket="b")
    c._session = _Session(s3)  # type: ignore[assignment]
    return c


@pytest.mark.asyncio
async def test_an_outage_mid_download_leaves_no_partial_file(tmp_path):
    c = _client(object())

    async def stream(_key, _chunk):
        yield b"partial"
        raise FileStorageUnavailableError("Object store unreachable")

    c.download_stream = stream  # type: ignore[method-assign]
    dest = tmp_path / "archive.zip"
    with pytest.raises(FileStorageUnavailableError):
        await c.download_to_file("k", str(dest))
    assert not dest.exists()


class _Bucket:
    def __init__(self, code=None):
        self.code = code
        self.created = False

    async def head_bucket(self, Bucket):
        if self.code:
            raise ClientError({"Error": {"Code": self.code}}, "HeadBucket")

    async def create_bucket(self, Bucket):
        self.created = True


@pytest.mark.asyncio
async def test_health_refused_credentials_are_unhealthy_and_logged(caplog):
    with caplog.at_level(logging.WARNING):
        assert await _client(_Bucket("AccessDenied")).health_check() is False
    assert "AccessDenied" in caplog.text


@pytest.mark.asyncio
async def test_health_creates_a_missing_bucket_like_the_first_write_would():
    s3 = _Bucket("NoSuchBucket")
    assert await _client(s3).health_check() is True
    assert s3.created


@pytest.mark.asyncio
async def test_health_of_a_usable_bucket():
    assert await _client(_Bucket()).health_check() is True
