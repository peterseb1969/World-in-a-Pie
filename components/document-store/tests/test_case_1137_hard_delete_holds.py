"""A hard delete releases only what the document still holds, tells
reporting every version went, and lets only documents that will go exempt
each other.

A document holds the file references of its latest version while that
version is active: superseding a version releases its set, and a soft delete
releases the active one. The whole-document hard delete released every
stored version's set again, so a file another document still used reached
zero and was deleted. Its event carried one arbitrary version, so reporting
kept the others' rows. And the "deleted in the same request" exemption came
from the request items before any outcome: a referrer whose own delete was
refused still let what it referenced go. Provenance: CASE-1137.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
import pytest_asyncio

from document_store.models.document import Document, DocumentStatus
from document_store.models.file import File, FileMetadata, FileStatus
from registry.models.namespace import Namespace

NS = "wip"
URL = "/api/document-store/documents"


@pytest_asyncio.fixture
async def full_mode():
    """'wip' in deletion_mode full, file storage on (reference counts move),
    and orphaned files recorded instead of removed from the object store."""
    ns = await Namespace.find_one({"prefix": NS})
    ns.deletion_mode = "full"
    await ns.save()
    removed: list[str] = []

    async def record(_self, file_id, **_exemption):
        removed.append(file_id)
        return True

    with (
        patch("document_store.services.document_service.is_file_storage_enabled", return_value=True),
        patch("document_store.services.file_storage_client.is_file_storage_enabled", return_value=True),
        patch("document_store.services.file_service.FileService.hard_delete_file", record),
    ):
        yield removed


def _id() -> str:
    return str(uuid.uuid4())


async def _file(count: int) -> str:
    file_id = _id()
    await File(
        namespace=NS, file_id=file_id, filename="f.bin", content_type="application/octet-stream",
        size_bytes=1, checksum=uuid.uuid4().hex * 2, storage_key=file_id, metadata=FileMetadata(),
        status=FileStatus.ACTIVE, reference_count=count, uploaded_at=datetime.now(UTC),
    ).insert()
    return file_id


async def _row(doc_id: str, version: int, status: DocumentStatus, *, files=(), refs=()) -> None:
    await Document(
        namespace=NS, document_id=doc_id, template_id=_id(), template_version=1,
        template_value="T1137", identity_hash=f"h-{doc_id}", version=version, data={},
        file_references=[{"field_path": "f", "file_id": f} for f in files],
        references=[{"field_path": "r", "resolved": {"document_id": r}} for r in refs],
        status=status,
    ).insert()


async def _count(file_id: str) -> int:
    return (await File.find_one({"file_id": file_id})).reference_count


async def _delete(client, headers, *items):
    resp = await client.request("DELETE", URL, headers=headers, json=list(items))
    assert resp.status_code == 200, resp.text
    return resp.json()["results"]


# --- file references (item 1) ---------------------------------------------


@pytest.mark.asyncio
async def test_an_edited_document_releases_a_shared_file_once(client, auth_headers, full_mode):
    shared = await _file(2)  # held by A (its latest version) and by B
    a, b = _id(), _id()
    await _row(a, 1, DocumentStatus.INACTIVE, files=[shared])  # superseded: released already
    await _row(a, 2, DocumentStatus.ACTIVE, files=[shared])
    await _row(b, 1, DocumentStatus.ACTIVE, files=[shared])
    results = await _delete(client, auth_headers, {"id": a, "hard_delete": True})
    assert results[0]["status"] == "deleted", results
    assert await _count(shared) == 1
    assert shared not in full_mode  # B still uses it: never removed


@pytest.mark.asyncio
async def test_a_soft_deleted_document_releases_nothing_again(client, auth_headers, full_mode):
    shared = await _file(1)  # held by B only: A's soft delete released its hold
    a, b = _id(), _id()
    await _row(a, 1, DocumentStatus.INACTIVE, files=[shared])
    await _row(b, 1, DocumentStatus.ACTIVE, files=[shared])
    await _delete(client, auth_headers, {"id": a, "hard_delete": True})
    assert await _count(shared) == 1


@pytest.mark.asyncio
async def test_deleting_a_superseded_version_releases_nothing(client, auth_headers, full_mode):
    shared = await _file(2)
    a, b = _id(), _id()
    await _row(a, 1, DocumentStatus.INACTIVE, files=[shared])
    await _row(a, 2, DocumentStatus.ACTIVE, files=[shared])
    await _row(b, 1, DocumentStatus.ACTIVE, files=[shared])
    results = await _delete(client, auth_headers, {"id": a, "hard_delete": True, "version": 1})
    assert results[0]["status"] == "deleted", results
    assert await _count(shared) == 2


@pytest.mark.asyncio
async def test_deleting_the_held_version_releases_its_files(client, auth_headers, full_mode):
    own = await _file(1)
    a = _id()
    await _row(a, 1, DocumentStatus.INACTIVE)
    await _row(a, 2, DocumentStatus.ACTIVE, files=[own])
    await _delete(client, auth_headers, {"id": a, "hard_delete": True, "version": 2})
    assert await _count(own) == 0
    assert own in full_mode  # nothing holds it now


@pytest.mark.asyncio
async def test_a_file_a_supersede_left_unheld_goes_with_the_document(client, auth_headers, full_mode):
    dropped = await _file(0)  # v1 used it, v2 dropped it: released, never removed
    a = _id()
    await _row(a, 1, DocumentStatus.INACTIVE, files=[dropped])
    await _row(a, 2, DocumentStatus.ACTIVE)
    await _delete(client, auth_headers, {"id": a, "hard_delete": True})
    assert dropped in full_mode


# --- the delete event (item 2) ------------------------------------------------


@pytest.mark.asyncio
async def test_a_whole_document_delete_tells_reporting_every_version_went(
    client, auth_headers, full_mode,
):
    a = _id()
    await _row(a, 1, DocumentStatus.INACTIVE)
    await _row(a, 2, DocumentStatus.ACTIVE)
    sent: list[dict] = []

    async def capture(_event_type, payload, **_kw):
        sent.append(payload)

    with patch("document_store.services.document_service.publish_document_event", capture):
        await _delete(client, auth_headers, {"id": a, "hard_delete": True})
    assert sent and sent[-1]["hard_delete"] is True and sent[-1]["version"] is None


# --- the same-request exemption (item 3) ----------------------------------------


@pytest.mark.asyncio
async def test_a_referrer_that_stays_does_not_let_its_target_go(client, auth_headers, full_mode):
    """Z references X, X references Y; the request deletes Y and X. X is
    refused (Z still references it), so it must not exempt Y: Y going would
    leave X pointing at nothing."""
    x, y, z = _id(), _id(), _id()
    await _row(y, 1, DocumentStatus.ACTIVE)
    await _row(x, 1, DocumentStatus.ACTIVE, refs=[y])
    await _row(z, 1, DocumentStatus.ACTIVE, refs=[x])
    results = await _delete(client, auth_headers,
                            {"id": y, "hard_delete": True}, {"id": x, "hard_delete": True})
    assert [r["status"] for r in results] == ["error", "error"], results
    assert {r["details"]["rule"] for r in results} == {"referenced"}
    assert await Document.find_one({"document_id": y}) is not None


@pytest.mark.asyncio
async def test_documents_that_all_go_still_exempt_each_other(client, auth_headers, full_mode):
    """An edge and its endpoint go together without force, in either order."""
    x, y = _id(), _id()
    await _row(y, 1, DocumentStatus.ACTIVE)
    await _row(x, 1, DocumentStatus.ACTIVE, refs=[y])
    results = await _delete(client, auth_headers,
                            {"id": y, "hard_delete": True}, {"id": x, "hard_delete": True})
    assert [r["status"] for r in results] == ["deleted", "deleted"], results
