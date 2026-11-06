"""A file's bytes are checked against its record, on backup and on restore.

A blob that failed to download partway, or whose bytes do not match the
file's SHA-256 checksum, used to stay in the archive and be restored as the
file, with only a server log line. Now the backup drops it from the archive
and names the file on the job, and still completes. A restore uploads only
bytes that match, and names every file restored without them: missing,
unreadable or mismatching. (CASE-1094.)
"""

from __future__ import annotations

import hashlib
import os
import uuid
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from wip_archive.archive import ArchiveReader, ArchiveWriter
from wip_archive.models import EntityCounts, Manifest

from document_store.services.backup_engine import (
    _DB_DOCUMENT_STORE,
    DirectBackupEngine,
    DirectRestoreEngine,
)

GOOD, BROKEN, WRONG = b"good bytes", b"broken bytes", b"right bytes"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _Storage:
    """Serves GOOD for F-GOOD, fails F-BROKEN after one chunk, and serves
    other bytes than recorded for F-WRONG."""

    def __init__(self):
        self.uploaded: dict[str, bytes] = {}

    async def download_stream(self, key, chunk_size=64 * 1024):
        if key == "F-BROKEN":
            yield BROKEN[:4]
            raise OSError("connection reset")
        yield {"F-GOOD": GOOD, "F-WRONG": b"other bytes"}[key]

    async def upload(self, storage_key, content, content_type=None):
        self.uploaded[storage_key] = content


def _warnings(events) -> list[str]:
    return [e.message for e in events if e.phase == "warning"]


@pytest_asyncio.fixture
async def namespace():
    client = AsyncIOMotorClient(os.environ["MONGO_URI"])
    ns = f"case1094-{uuid.uuid4().hex[:8]}"
    files = client[_DB_DOCUMENT_STORE]["files"]
    await files.insert_many([
        {"file_id": "F-GOOD", "storage_key": "F-GOOD", "namespace": ns, "status": "active", "checksum": _sha(GOOD)},
        {"file_id": "F-BROKEN", "storage_key": "F-BROKEN", "namespace": ns, "status": "active", "checksum": _sha(BROKEN)},
        {"file_id": "F-WRONG", "storage_key": "F-WRONG", "namespace": ns, "status": "active", "checksum": _sha(WRONG)},
    ])
    yield client, ns
    await files.delete_many({"namespace": ns})
    client.close()


@pytest.mark.asyncio
async def test_the_backup_drops_bad_blobs_names_them_and_completes(namespace, tmp_path):
    client, ns = namespace
    events: list = []
    engine = DirectBackupEngine(client, _Storage(), events.append)
    output = tmp_path / "b.zip"
    writer = ArchiveWriter(output, default_namespace=ns)
    await engine._backup_blobs(writer, ns, output, 0, 3)
    writer.write(Manifest(namespace=ns, counts=EntityCounts(), include_files=True))

    with ArchiveReader(output) as reader:
        assert reader.list_blobs() == ["F-GOOD"]
        assert reader.read_blob("F-GOOD") == GOOD
    warnings = _warnings(events)
    assert len(warnings) == 2
    assert any("F-BROKEN" in w and "could not be read" in w for w in warnings)
    assert any("F-WRONG" in w and "do not match" in w for w in warnings)


def _reader(rows, blobs, *, include_files=True):
    reader = MagicMock()
    reader.list_namespaces = MagicMock(return_value=["src"])
    reader.read_entities = MagicMock(
        side_effect=lambda et, namespace=None: [dict(r) for r in rows] if et == "files" else []
    )
    reader.list_blobs = MagicMock(return_value=list(blobs))
    reader.read_blob = MagicMock(side_effect=blobs.get)
    reader.read_manifest = MagicMock(return_value=Manifest(
        namespace="src", counts=EntityCounts(), include_files=include_files,
    ))
    return reader


ROWS = [
    {"file_id": "F-GOOD", "checksum": _sha(GOOD)},
    {"file_id": "F-CORRUPT", "checksum": _sha(b"what was backed up")},
    {"file_id": "F-MISSING", "checksum": _sha(b"never archived")},
]
BLOBS = {"F-GOOD": GOOD, "F-CORRUPT": b"bit rot"}


@pytest.mark.asyncio
async def test_a_restore_uploads_only_matching_bytes_and_names_the_rest():
    storage, events = _Storage(), []
    engine = DirectRestoreEngine(MagicMock(), storage, events.append)
    await engine._restore_blobs(_reader(ROWS, BLOBS), "ns")
    assert storage.uploaded == {"F-GOOD": GOOD}
    warnings = _warnings(events)
    assert any("F-CORRUPT" in w and "do not match" in w for w in warnings)
    assert any("F-MISSING" in w and "no bytes" in w for w in warnings)


@pytest.mark.asyncio
async def test_a_fresh_restore_checks_the_bytes_under_the_old_id():
    storage, events = _Storage(), []
    engine = DirectRestoreEngine(MagicMock(), storage, events.append)
    id_map = {"F-GOOD": "N-GOOD", "F-CORRUPT": "N-CORRUPT", "F-MISSING": "N-MISSING"}
    await engine._remap_blobs(_reader(ROWS, BLOBS), id_map)
    assert storage.uploaded == {"N-GOOD": GOOD}
    assert len(_warnings(events)) == 2


@pytest.mark.asyncio
async def test_an_archive_made_without_files_reports_no_missing_bytes():
    storage, events = _Storage(), []
    engine = DirectRestoreEngine(MagicMock(), storage, events.append)
    await engine._restore_blobs(_reader(ROWS, {}, include_files=False), "ns")
    assert storage.uploaded == {}
    assert _warnings(events) == []
