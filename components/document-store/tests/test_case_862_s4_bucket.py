"""The attachment bucket is ensured on first write, not only at startup.

Provenance: CASE-862 S4 — document-store created its bucket only at startup,
and only if the object store answered then. Started before the store, it never
checked again, so uploads failed until a restart. Writes now ensure the bucket
once per process.
"""

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from document_store.services.file_storage_client import FileStorageClient, FileStorageError


class _FakeS3:
    """Records calls; the bucket exists once created (or from the start)."""

    def __init__(self, bucket_exists: bool = False, reachable: bool = True) -> None:
        self.bucket_exists = bucket_exists
        self.reachable = reachable
        self.calls: list[str] = []

    async def head_bucket(self, Bucket):
        self.calls.append("head_bucket")
        if not self.reachable:
            raise EndpointConnectionError(endpoint_url="http://store.invalid")
        if not self.bucket_exists:
            raise ClientError({"Error": {"Code": "404"}}, "HeadBucket")

    async def create_bucket(self, Bucket):
        self.calls.append("create_bucket")
        self.bucket_exists = True

    async def put_object(self, **kwargs):
        self.calls.append("put_object")


class _Session:
    def __init__(self, s3: _FakeS3) -> None:
        self.s3 = s3

    def create_client(self, *_args, **_kwargs):
        s3 = self.s3

        class _CM:
            async def __aenter__(self):
                return s3

            async def __aexit__(self, *exc):
                return False

        return _CM()


def _client(s3: _FakeS3) -> FileStorageClient:
    c = FileStorageClient(endpoint_url="http://store.invalid", access_key="k", secret_key="s", bucket="b")
    c._session = _Session(s3)  # type: ignore[assignment]
    return c


@pytest.mark.asyncio
async def test_first_upload_creates_a_missing_bucket():
    s3 = _FakeS3(bucket_exists=False)
    await _client(s3).upload("k1", b"x", "text/plain")
    assert s3.calls == ["head_bucket", "create_bucket", "put_object"]


@pytest.mark.asyncio
async def test_the_bucket_is_checked_once_per_process():
    s3 = _FakeS3(bucket_exists=True)
    c = _client(s3)
    await c.upload("k1", b"x", "text/plain")
    await c.upload("k2", b"y", "text/plain")
    assert s3.calls == ["head_bucket", "put_object", "put_object"]


@pytest.mark.asyncio
async def test_a_store_that_comes_up_late_is_ensured_on_the_next_write():
    """The startup path: store unreachable at first, reachable later — no restart."""
    s3 = _FakeS3(bucket_exists=False, reachable=False)
    c = _client(s3)
    with pytest.raises(EndpointConnectionError):
        await c.upload("k1", b"x", "text/plain")
    assert "put_object" not in s3.calls

    s3.reachable = True
    await c.upload("k2", b"y", "text/plain")
    assert s3.calls[-3:] == ["head_bucket", "create_bucket", "put_object"]


@pytest.mark.asyncio
async def test_a_refused_bucket_check_fails_the_write_with_a_clear_error():
    s3 = _FakeS3()

    async def forbidden(Bucket):
        raise ClientError({"Error": {"Code": "403"}}, "HeadBucket")

    s3.head_bucket = forbidden  # type: ignore[method-assign]
    with pytest.raises(FileStorageError, match="Failed to ensure bucket exists"):
        await _client(s3).upload("k1", b"x", "text/plain")
