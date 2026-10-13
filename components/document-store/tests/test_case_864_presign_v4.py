"""Presigned download URLs are SigV4 (CASE-864).

Ordinary S3 requests always signed V4, but generate_presigned_url fell
back to the legacy V2 query form. Garage rejects V2 query auth as
anonymous (403), so presigned downloads would silently break there; V2's
unsigned Host was also the only reason the public-host rewrite worked.
Under V4 the Host is signed, and the deployer's storage routes forward
the upstream Host at the edge (`upstream_host_header`) so the store
receives the Host the URL was signed against.

Presigning is local computation (no server round-trip), so these tests
run against a non-existent endpoint.
"""

import pytest

from document_store.services.file_storage_client import FileStorageClient


def _client(**kwargs) -> FileStorageClient:
    return FileStorageClient(
        endpoint_url="http://wip-garage:3900",
        access_key="GK00112233445566778899aabb",
        secret_key="0" * 64,
        bucket="wip-attachments",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_presigned_url_is_sigv4():
    url = await _client().generate_download_url("k/file.bin", expires_in=300)
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in url
    # The legacy V2 query markers must be gone.
    assert "AWSAccessKeyId=" not in url
    assert "X-Amz-SignedHeaders=host" in url


@pytest.mark.asyncio
async def test_public_endpoint_rewrite_still_applies():
    c = _client(public_endpoint_url="https://host.example:8443/garage")
    url = await c.generate_download_url("k/file.bin", expires_in=300)
    assert url.startswith("https://host.example:8443/garage/wip-attachments/k/")
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in url
