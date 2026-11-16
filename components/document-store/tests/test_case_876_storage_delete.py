"""CASE-876 — namespace deletion removes stored objects through document-store.

Namespace deletion used to leave every stored file object behind: the
Registry's own object-store step was never configured on any install and
used an authentication MinIO rejects. The Registry now sends the keys from
its deletion journal to POST /api/document-store/files/storage/delete,
which deletes them with document-store's own storage client.

Because the endpoint removes bytes outright, its guards are the contract
under test here: privileged keys only, the real Registry must hold a deletion
journal for the namespace that is still running (a namespace that is merely
absent from the Registry — gone, locked, or not visible to the key — is never
enough on its own, CASE-1139), the namespace must not be live, and a key
whose file record belongs to another namespace is refused per key.

The request is also bound to the deletion it serves (CASE-1301): it names the
journal and the step its keys come from, and only the keys that step recorded
are deleted. A key the journal never named — one left by an earlier, degraded
deletion, or a list replayed while a different deletion runs under the same
name — is refused per key, so the journal's counts say what it actually
removed.

The storage client is mocked; the signed delete against a real object store is
verified live (the case's reproduction re-run on a dev install).
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from document_store.models.file import File, FileMetadata, FileStatus
from document_store.services.file_storage_client import FileStorageError
from registry.main import app as registry_app
from registry.models.deletion_journal import DeletionJournal, DeletionStep
from registry.models.namespace import Namespace
from registry.services.namespace_deletion import _store_key_batch

URL = "/api/document-store/files/storage/delete"


async def _registry_namespace(
    prefix: str, headers: dict, *, locked: bool, keys: list[str] | None = None
) -> str:
    """Create the namespace; when it is locked, lock it for deletion, and when
    keys are given, write the running journal the endpoint requires with that
    step. Returns the journal's id ("" when no journal was written)."""
    async with AsyncClient(
        transport=ASGITransport(app=registry_app), base_url="http://registry"
    ) as reg:
        resp = await reg.put(
            f"/api/registry/namespaces/{prefix}",
            json={"description": "storage-delete test", "deletion_mode": "full"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
    if not locked:
        return ""
    ns = await Namespace.find_one({"prefix": prefix})
    assert ns is not None
    ns.status = "locked"
    await ns.save()
    return await _journal(prefix, "in_progress", keys) if keys else ""


async def _journal(prefix: str, status: str, keys: list[str] | None = None) -> str:
    """The Registry's deletion journal for the namespace, in the given state,
    with one file-storage step recording ``keys`` the way current journals do
    (in their own batch document). Returns the journal's id."""
    journal = DeletionJournal(
        namespace=prefix,
        status=status,
        steps=[DeletionStep(
            order=1, store="file_storage", action="delete_objects",
            storage_keys_batch=await _store_key_batch(prefix, keys or []),
        )],
    )
    await journal.insert()
    return str(journal.id)


def _request(namespace: str, journal_id: str, keys: list[str], step_order: int = 1) -> dict:
    """A storage delete as the Registry's step sends it: the namespace, the
    deletion and step it comes from, and that step's keys."""
    return {
        "namespace": namespace,
        "journal_id": journal_id,
        "step_order": step_order,
        "storage_keys": keys,
    }


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
    recordless = str(uuid.uuid4())  # record already deleted: a retried journal step
    own = await _file_record(ns)
    jid = await _registry_namespace(ns, auth_headers, locked=True, keys=[own, recordless])

    resp = await client.post(
        URL, json=_request(ns, jid, [own, recordless, own]), headers=auth_headers
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"namespace": ns, "deleted": 2, "refused": [], "failed": []}
    deleted = sorted(call.args[0] for call in storage.delete.await_args_list)
    assert deleted == sorted([own, recordless])  # duplicates sent once


@pytest.mark.asyncio
async def test_key_owned_by_another_namespace_is_refused_and_kept(client, auth_headers, storage):
    ns, other = _prefix(), _prefix()
    foreign = await _file_record(other)
    jid = await _registry_namespace(ns, auth_headers, locked=True, keys=[foreign])

    resp = await client.post(
        URL, json=_request(ns, jid, [foreign]), headers=auth_headers
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["refused"] == [{"storage_key": foreign, "owner_namespaces": [other]}]
    assert resp.json()["deleted"] == 0
    storage.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_key_the_step_did_not_record_is_refused_and_kept(client, auth_headers, storage):
    """A key with no file record at all used to go: nothing tied it to the
    deletion, only the fact that some deletion ran under the name."""
    ns = _prefix()
    recorded = str(uuid.uuid4())
    stranger = str(uuid.uuid4())
    jid = await _registry_namespace(ns, auth_headers, locked=True, keys=[recorded])

    resp = await client.post(
        URL, json=_request(ns, jid, [recorded, stranger]), headers=auth_headers
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted"] == 1
    assert len(body["refused"]) == 1
    assert body["refused"][0]["storage_key"] == stranger
    assert "not recorded by step 1" in body["refused"][0]["reason"]
    storage.delete.assert_awaited_once_with(recorded)


@pytest.mark.asyncio
async def test_a_request_naming_no_step_is_refused(client, auth_headers, storage):
    ns = _prefix()
    key = str(uuid.uuid4())
    await _registry_namespace(ns, auth_headers, locked=True, keys=[key])

    resp = await client.post(URL, json={"namespace": ns, "storage_keys": [key]}, headers=auth_headers)

    assert resp.status_code == 422, resp.text
    storage.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_request_naming_another_deletion_is_refused(client, auth_headers, storage):
    ns = _prefix()
    key = str(uuid.uuid4())
    jid = await _registry_namespace(ns, auth_headers, locked=True, keys=[key])

    resp = await client.post(
        URL, json=_request(ns, str(ObjectId()), [key]), headers=auth_headers
    )

    assert resp.status_code == 409, resp.text
    assert jid in resp.json()["detail"]
    storage.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_keys_replayed_against_a_later_deletion_of_the_name_delete_nothing(
    client, auth_headers, storage
):
    """The name is not the deletion. An old deletion's key list arriving while
    a newer one runs under the same prefix is refused: the new namespace's own
    objects may carry those keys."""
    ns = _prefix()
    old_key, new_key = str(uuid.uuid4()), str(uuid.uuid4())
    now = datetime.now(UTC)
    old = DeletionJournal(
        namespace=ns, status="completed", requested_at=now,
        steps=[DeletionStep(order=1, store="file_storage", storage_keys=[old_key])],
    )
    await old.insert()
    await _registry_namespace(ns, auth_headers, locked=True, keys=[new_key])

    resp = await client.post(
        URL, json=_request(ns, str(old.id), [old_key]), headers=auth_headers
    )

    assert resp.status_code == 409, resp.text
    storage.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_step_the_running_journal_does_not_have_is_refused(client, auth_headers, storage):
    ns = _prefix()
    key = str(uuid.uuid4())
    jid = await _registry_namespace(ns, auth_headers, locked=True, keys=[key])

    resp = await client.post(
        URL, json=_request(ns, jid, [key], step_order=7), headers=auth_headers
    )

    assert resp.status_code == 409, resp.text
    assert "no file-storage step 7" in resp.json()["detail"]
    storage.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_step_whose_recorded_keys_are_gone_is_refused(client, auth_headers, storage):
    """Keys unverifiable are keys undeletable: a step whose batch no longer
    exists (dropped when it completed) cannot authorise a delete."""
    ns = _prefix()
    key = str(uuid.uuid4())
    await _registry_namespace(ns, auth_headers, locked=False)
    record = await Namespace.find_one({"prefix": ns})
    record.status = "locked"
    await record.save()
    journal = DeletionJournal(
        namespace=ns, status="in_progress",
        steps=[DeletionStep(order=1, store="file_storage", storage_keys_batch=str(ObjectId()))],
    )
    await journal.insert()

    resp = await client.post(
        URL, json=_request(ns, str(journal.id), [key]), headers=auth_headers
    )

    assert resp.status_code == 409, resp.text
    assert "no longer has its recorded keys" in resp.json()["detail"]
    storage.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_namespace_is_refused(client, auth_headers, storage):
    ns = _prefix()
    await _registry_namespace(ns, auth_headers, locked=False)
    own = await _file_record(ns)

    resp = await client.post(
        URL, json=_request(ns, str(ObjectId()), [own]), headers=auth_headers
    )

    assert resp.status_code == 409, resp.text
    storage.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_namespace_gone_from_registry_is_allowed(client, auth_headers, storage):
    # A deletion removes the namespace record; resuming a journal whose
    # storage step failed must still be able to clean up.
    ns = _prefix()
    key = str(uuid.uuid4())
    jid = await _journal(ns, "failed", [key])

    resp = await client.post(URL, json=_request(ns, jid, [key]), headers=auth_headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] == 1
    storage.delete.assert_awaited_once_with(key)


@pytest.mark.asyncio
@pytest.mark.parametrize("journal", [None, "completed"], ids=["no_journal", "completed_journal"])
async def test_a_namespace_absent_from_the_registry_is_not_enough(client, auth_headers, storage, journal):
    # The Registry answers 404 alike for a namespace that is gone, one locked
    # for deletion, and one the caller's key holds no grant on. Without a
    # deletion it is running, no bytes go — and a completed journal may
    # belong to an earlier deletion of a name since re-created.
    ns = _prefix()
    jid = await _journal(ns, journal, [str(uuid.uuid4())]) if journal else str(ObjectId())
    resp = await client.post(
        URL, json=_request(ns, jid, [str(uuid.uuid4())]), headers=auth_headers
    )

    assert resp.status_code == 409, resp.text
    storage.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_locked_namespace_without_a_journal_is_refused(client, auth_headers, storage):
    ns = _prefix()
    await _registry_namespace(ns, auth_headers, locked=False)
    record = await Namespace.find_one({"prefix": ns})
    record.status = "locked"
    await record.save()
    resp = await client.post(
        URL, json=_request(ns, str(ObjectId()), [str(uuid.uuid4())]), headers=auth_headers
    )

    assert resp.status_code == 409, resp.text
    storage.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_storage_failure_is_reported_per_key(client, auth_headers, storage):
    ns = _prefix()
    ok, bad = await _file_record(ns), await _file_record(ns)
    jid = await _registry_namespace(ns, auth_headers, locked=True, keys=[ok, bad])

    async def _delete(key: str) -> None:
        if key == bad:
            raise FileStorageError("boom")

    storage.delete.side_effect = _delete

    resp = await client.post(
        URL, json=_request(ns, jid, [ok, bad]), headers=auth_headers
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted"] == 1
    assert [f["storage_key"] for f in body["failed"]] == [bad]


@pytest.mark.asyncio
async def test_an_object_already_gone_counts_as_deleted(client, auth_headers, storage):
    # CASE-1264: a store answering NoSuchKey for a key a retried step sends
    # again; the object is not there, which is what was asked.
    from document_store.services.file_storage_client import FileStorageNotFoundError

    ns = _prefix()
    gone = await _file_record(ns)
    jid = await _registry_namespace(ns, auth_headers, locked=True, keys=[gone])
    storage.delete.side_effect = FileStorageNotFoundError("NoSuchKey")

    resp = await client.post(URL, json=_request(ns, jid, [gone]), headers=auth_headers)

    assert resp.status_code == 200, resp.text
    assert (resp.json()["deleted"], resp.json()["failed"]) == (1, [])


@pytest.mark.asyncio
async def test_unreachable_registry_fails_closed(client, auth_headers, storage):
    broken = AsyncMock()
    broken.get_namespace_status = AsyncMock(side_effect=ConnectionError("down"))
    with patch("document_store.api.files.get_registry_client", return_value=broken):
        resp = await client.post(
            URL,
            json=_request(_prefix(), str(ObjectId()), [str(uuid.uuid4())]),
            headers=auth_headers,
        )

    assert resp.status_code == 503, resp.text
    storage.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_registry_that_cannot_name_the_step_fails_closed(client, auth_headers, storage):
    """Not answering is not the same as answering "no keys": the delete is 503,
    so the Registry retries the step instead of closing it over an outage."""
    ns = _prefix()
    key = str(uuid.uuid4())
    jid = await _registry_namespace(ns, auth_headers, locked=True, keys=[key])
    broken = AsyncMock()
    broken.get_namespace_status = AsyncMock(return_value="locked")
    broken.get_namespace_deletion_journal = AsyncMock(
        return_value={"status": "in_progress", "journal_id": jid}
    )
    broken.get_namespace_deletion_step_keys = AsyncMock(side_effect=ConnectionError("down"))
    with patch("document_store.api.files.get_registry_client", return_value=broken):
        resp = await client.post(URL, json=_request(ns, jid, [key]), headers=auth_headers)

    assert resp.status_code == 503, resp.text
    storage.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_privileged_key_is_forbidden(client, storage):
    resp = await client.post(
        URL,
        json=_request(_prefix(), str(ObjectId()), [str(uuid.uuid4())]),
        headers={"X-API-Key": "test_scoped_key"},
    )

    assert resp.status_code == 403, resp.text
    storage.delete.assert_not_awaited()
