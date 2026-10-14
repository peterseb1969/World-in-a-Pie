"""CASE-876 — namespace deletion removes stored objects through document-store.

Namespace deletion used to leave every stored file object behind: the
Registry's own object-store step was never configured on any install and
used an authentication MinIO rejects. The Registry now sends the keys from
its deletion journal to POST /api/document-store/files/storage/delete,
which deletes them with document-store's own storage client.

Because the endpoint removes bytes outright, its guards are the contract
under test here: privileged keys only, the namespace must be locked
(deletion in progress) or already gone in the real Registry, and a key whose
file record belongs to another namespace is refused per key. The storage
client is mocked; the signed delete against a real object store is verified
live (the case's reproduction re-run on a dev install).
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from document_store.models.file import File, FileMetadata, FileStatus
from document_store.services.file_storage_client import FileStorageError
from registry.main import app as registry_app
from registry.models.namespace import Namespace

URL = "/api/document-store/files/storage/delete"


async def _registry_namespace(prefix: str, headers: dict, *, locked: bool) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=registry_app), base_url="http://registry"
    ) as reg:
        resp = await reg.put(
            f"/api/registry/namespaces/{prefix}",
            json={"description": "storage-delete test", "deletion_mode": "full"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
    if locked:
        ns = await Namespace.find_one({"prefix": prefix})
        assert ns is not None
        ns.status = "locked"
        await ns.save()


async def _file_record(namespace: str) -> str:
    key = str(uuid.uuid4())
    await File(
        namespace=namespace,
        file_id=key,
        filename="probe.txt",
        content_type="text/plain",
        size_bytes=3,
        checksum=uuid.uuid4().hex * 2,
        storage_key=key,
        metadata=FileMetadata(),
        status=FileStatus.ORPHAN,
        reference_count=0,
        uploaded_at=datetime.now(UTC),
    ).insert()
    return key


@pytest.fixture
def storage():
    mock = AsyncMock()
    mock.delete = AsyncMock()
    with (
        patch("document_store.api.files.is_file_storage_enabled", return_value=True),
        patch("document_store.api.files.get_file_storage_client", return_value=mock),
    ):
        yield mock


def _prefix() -> str:
    return f"sd-{uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
async def test_locked_namespace_deletes_own_and_recordless_keys(client, auth_headers, storage):
    ns = _prefix()
    await _registry_namespace(ns, auth_headers, locked=True)
    own = await _file_record(ns)
    recordless = str(uuid.uuid4())  # record already deleted: a retried journal step

    resp = await client.post(
        URL, json={"namespace": ns, "storage_keys": [own, recordless, own]},
        headers=auth_headers,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"namespace": ns, "deleted": 2, "refused": [], "failed": []}
    deleted = sorted(call.args[0] for call in storage.delete.await_args_list)
    assert deleted == sorted([own, recordless])  # duplicates sent once


@pytest.mark.asyncio
async def test_key_owned_by_another_namespace_is_refused_and_kept(client, auth_headers, storage):
    ns, other = _prefix(), _prefix()
    await _registry_namespace(ns, auth_headers, locked=True)
    foreign = await _file_record(other)

    resp = await client.post(
        URL, json={"namespace": ns, "storage_keys": [foreign]}, headers=auth_headers
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["refused"] == [{"storage_key": foreign, "owner_namespaces": [other]}]
    assert resp.json()["deleted"] == 0
    storage.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_namespace_is_refused(client, auth_headers, storage):
    ns = _prefix()
    await _registry_namespace(ns, auth_headers, locked=False)
    own = await _file_record(ns)

    resp = await client.post(
        URL, json={"namespace": ns, "storage_keys": [own]}, headers=auth_headers
    )

    assert resp.status_code == 409, resp.text
    storage.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_namespace_gone_from_registry_is_allowed(client, auth_headers, storage):
    # A completed deletion removes the namespace record; resuming a journal
    # whose storage step failed must still be able to clean up.
    key = str(uuid.uuid4())

    resp = await client.post(
        URL, json={"namespace": _prefix(), "storage_keys": [key]}, headers=auth_headers
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] == 1
    storage.delete.assert_awaited_once_with(key)


@pytest.mark.asyncio
async def test_storage_failure_is_reported_per_key(client, auth_headers, storage):
    ns = _prefix()
    await _registry_namespace(ns, auth_headers, locked=True)
    ok, bad = await _file_record(ns), await _file_record(ns)

    async def _delete(key: str) -> None:
        if key == bad:
            raise FileStorageError("boom")

    storage.delete.side_effect = _delete

    resp = await client.post(
        URL, json={"namespace": ns, "storage_keys": [ok, bad]}, headers=auth_headers
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted"] == 1
    assert [f["storage_key"] for f in body["failed"]] == [bad]


@pytest.mark.asyncio
async def test_unreachable_registry_fails_closed(client, auth_headers, storage):
    broken = AsyncMock()
    broken.get_namespace_status = AsyncMock(side_effect=ConnectionError("down"))
    with patch("document_store.api.files.get_registry_client", return_value=broken):
        resp = await client.post(
            URL, json={"namespace": _prefix(), "storage_keys": [str(uuid.uuid4())]},
            headers=auth_headers,
        )

    assert resp.status_code == 503, resp.text
    storage.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_privileged_key_is_forbidden(client, storage):
    resp = await client.post(
        URL, json={"namespace": _prefix(), "storage_keys": [str(uuid.uuid4())]},
        headers={"X-API-Key": "test_scoped_key"},
    )

    assert resp.status_code == 403, resp.text
    storage.delete.assert_not_awaited()
