"""An unreachable object store answers 503, never a bare 500.

Provenance: CASE-948 — with the object store stopped, a file upload escaped
aiohttp's DNS error to the ASGI layer (500, body "Internal Server Error"). The
storage client caught only botocore's ClientError, which a connection failure
is not. Transport failures now become one FileStorageUnavailableError, which the
app answers with 503, and each file route maps a FileServiceError's code to a
status instead of answering every one with the same status. The routes'
behaviour with the store or the Registry down is tested in test_files.py.
"""

import socket
from types import SimpleNamespace

import aiohttp
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, ReadTimeoutError

from document_store.api.files import _file_error_status
from document_store.services.file_service import FileServiceError
from document_store.services.file_storage_client import (
    FileStorageClient,
    FileStorageError,
    FileStorageUnavailableError,
)

# The exact exception observed live with the store's container stopped.
_DNS_ERROR = aiohttp.ClientConnectorDNSError(
    SimpleNamespace(host="wip-garage", port=3900, ssl="default"),
    socket.gaierror(8, "Name or service not known"),
)


class _S3:
    """A store whose every call raises the configured exception."""

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    async def head_bucket(self, Bucket):
        raise self.exc

    async def head_object(self, Bucket, Key):
        raise self.exc

    async def get_object(self, Bucket, Key):
        raise self.exc

    async def delete_object(self, Bucket, Key):
        raise self.exc


class _Session:
    def __init__(self, s3: _S3) -> None:
        self.s3 = s3

    def create_client(self, *_args, **_kwargs):
        s3 = self.s3

        class _CM:
            async def __aenter__(self):
                return s3

            async def __aexit__(self, *exc):
                return False

        return _CM()


def _client(exc: BaseException) -> FileStorageClient:
    c = FileStorageClient(endpoint_url="http://wip-garage:3900", access_key="k", secret_key="s", bucket="b")
    c._session = _Session(_S3(exc))  # type: ignore[assignment]
    return c


# ---------------------------------------------------------------------------
# The client: every transport failure becomes one exception type
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [
    _DNS_ERROR,
    EndpointConnectionError(endpoint_url="http://wip-garage:3900"),
    ReadTimeoutError(endpoint_url="http://wip-garage:3900"),
    TimeoutError(),
], ids=["aiohttp-dns", "endpoint-connection", "read-timeout", "timeout"])
async def test_transport_failures_become_unavailable(exc):
    c = _client(exc)
    with pytest.raises(FileStorageUnavailableError, match="Object store unreachable"):
        await c.upload("k", b"x", "text/plain")
    with pytest.raises(FileStorageUnavailableError):
        await c.download("k")
    with pytest.raises(FileStorageUnavailableError):
        await c.delete("k")
    with pytest.raises(FileStorageUnavailableError):
        await c.exists("k")
    with pytest.raises(FileStorageUnavailableError):
        async for _ in c.download_stream("k"):
            pass


@pytest.mark.asyncio
async def test_a_store_answer_is_still_a_storage_error_not_an_outage():
    """A store that answered (missing key, refusal) keeps its FileStorageError."""
    c = _client(ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject"))
    with pytest.raises(FileStorageError, match="File not found"):
        await c.download("k")


def test_an_outage_is_not_a_storage_error():
    """Callers that swallow FileStorageError as "the object is gone" (hard-delete,
    archive cleanup) must not swallow an outage too."""
    assert not issubclass(FileStorageUnavailableError, FileStorageError)


@pytest.mark.asyncio
async def test_a_local_file_error_is_not_an_outage(tmp_path):
    """upload_file opens a local file inside the client — its OSError stays a
    FileStorageError, not a 503."""
    c = _client(AssertionError("store not reached"))
    c._bucket_ensured = True
    with pytest.raises(FileStorageError, match="Failed to upload file"):
        await c.upload_file("k", str(tmp_path / "missing.zip"))


# ---------------------------------------------------------------------------
# The status a FileServiceError's code maps to
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code,rule,status", [
    ("invalid_state", "storage_disabled", 503),
    ("registry_error", "registry_unavailable", 503),
    ("registry_error", None, 502),
    ("invalid_state", "file_deleted", 409),
    ("invalid_state", "not_inactive", 409),
    ("internal_error", None, 500),
    ("invalid_request", None, 400),
])
def test_file_error_status(code, rule, status):
    assert _file_error_status(FileServiceError("x", code, rule=rule)) == status
