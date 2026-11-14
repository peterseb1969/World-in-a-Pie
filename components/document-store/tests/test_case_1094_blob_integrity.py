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
    BackupEngineError,
    DirectBackupEngine,
    DirectRestoreEngine,
)
from document_store.services.file_storage_client import (
    FileStorageError,
    FileStorageUnavailableError,
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
            raise FileStorageError("Failed to download file: InternalError")
        yield {"F-GOOD": GOOD, "F-WRONG": b"other bytes"}[key]

    async def upload(self, storage_key, content, content_type=None):
        self.uploaded[storage_key] = content

    async def exists(self, storage_key):
        return storage_key in self.uploaded


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
async def test_an_archive_made_without_files_names_the_files_restored_without_content():
    """A backup made without its files (the default) restores file rows
    with nothing to serve: the job says so, where it used to say nothing."""
    storage, events = _Storage(), []
    engine = DirectRestoreEngine(MagicMock(), storage, events.append)
    await engine._restore_blobs(_reader(ROWS, {}, include_files=False), "ns")
    assert storage.uploaded == {}
    [warning] = _warnings(events)
    assert warning.startswith("3 file(s) are restored without content")
    assert "made without files" in warning
    assert all(f in warning for f in ("F-GOOD", "F-CORRUPT", "F-MISSING"))


@pytest.mark.asyncio
async def test_a_file_whose_object_is_still_here_is_not_named():
    """A plain restore keeps a file's id: an object this install still holds
    under it serves the file, so it is not named as restored without content."""
    storage, events = _Storage(), []
    storage.uploaded["F-GOOD"] = GOOD
    engine = DirectRestoreEngine(MagicMock(), storage, events.append)
    await engine._restore_blobs(_reader(ROWS, {}, include_files=False), "ns")
    [warning] = _warnings(events)
    assert "F-GOOD" not in warning and "2 file(s)" in warning


@pytest.mark.asyncio
async def test_a_fresh_restore_from_an_archive_without_files_names_every_file():
    """A fresh restore writes each file under a new id, where no object can
    be: from an archive without bytes, every one is named, by its new id."""
    storage, events = _Storage(), []
    engine = DirectRestoreEngine(MagicMock(), storage, events.append)
    id_map = {"F-GOOD": "N-GOOD", "F-CORRUPT": "N-CORRUPT", "F-MISSING": "N-MISSING"}
    await engine._remap_blobs(_reader(ROWS, {}, include_files=False), id_map)
    assert storage.uploaded == {}
    [warning] = _warnings(events)
    assert "3 file(s) are restored without content" in warning
    assert all(n in warning for n in id_map.values())


@pytest.mark.asyncio
async def test_files_without_bytes_are_one_warning_per_reason_with_a_sample():
    """A namespace whose every blob is bad is one warning per reason, not
    one per file: a job record holds every warning it is given."""
    storage, events = _Storage(), []
    engine = DirectRestoreEngine(MagicMock(), storage, events.append)
    rows = [{"file_id": f"F-{i:03}", "checksum": _sha(b"x")} for i in range(50)]
    blobs = {f"F-{i:03}": b"rot" for i in range(25)}
    await engine._restore_blobs(_reader(rows, blobs), "ns")
    warnings = _warnings(events)
    assert len(warnings) == 2
    from document_store.services.backup_engine import _FILE_WARNING_SAMPLE

    more = 25 - _FILE_WARNING_SAMPLE
    assert all(w.startswith("25 file(s)") and w.endswith(f"and {more} more") for w in warnings)


@pytest.mark.asyncio
async def test_the_manifest_names_the_files_the_backup_carries_without_bytes(namespace, tmp_path):
    """The archive itself says which of its file rows have no bytes in it,
    so a reader holding only the archive can tell."""
    client, ns = namespace
    events: list = []
    output = tmp_path / "with-files.zip"
    await DirectBackupEngine(client, _Storage(), events.append).run_backup(
        ns, output, include_files=True,
    )
    with ArchiveReader(output) as reader:
        manifest = reader.read_manifest()
        assert reader.list_blobs() == ["F-GOOD"]
    assert manifest.include_files is True
    assert manifest.namespaces[0].files_without_bytes == ["F-BROKEN", "F-WRONG"]


class _DownStorage(_Storage):
    async def download_stream(self, key, chunk_size=64 * 1024):
        raise FileStorageUnavailableError("Object store unreachable — retry shortly")
        yield b""  # pragma: no cover - makes this an async generator


@pytest.mark.asyncio
async def test_an_unreachable_object_store_fails_the_backup(namespace, tmp_path):
    """An outage is not a lost object: the backup stops, rather than
    complete as an archive of file records without their files."""
    client, ns = namespace
    events: list = []
    writer = ArchiveWriter(tmp_path / "down.zip", default_namespace=ns)
    engine = DirectBackupEngine(client, _DownStorage(), events.append)
    with pytest.raises(BackupEngineError, match="could not be reached"):
        await engine._backup_blobs(writer, ns, tmp_path / "down.zip", 0, 3)
    assert _warnings(events) == []


class _FullDiskWriter:
    """An archive writer whose scratch disk is full."""

    def open_blob(self, file_id):
        raise OSError(28, "No space left on device")

    def discard_blob(self, file_id):
        pass


@pytest.mark.asyncio
async def test_a_scratch_disk_failure_is_named_as_one(namespace, tmp_path):
    """A failed write to the archive's scratch space stops the backup and
    says so; it used to be reported as the object store failing to read."""
    client, ns = namespace
    engine = DirectBackupEngine(client, _Storage(), [].append)
    with pytest.raises(BackupEngineError, match="scratch space") as exc_info:
        await engine._backup_blobs(_FullDiskWriter(), ns, tmp_path / "x.zip", 0, 3)
    assert "object store" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_a_dry_run_counts_only_the_bytes_the_real_run_uploads():
    """The dry run checks the bytes as the real run does: it would upload
    only the matching blob, and names the files it would restore without."""
    storage, events = _Storage(), []
    engine = DirectRestoreEngine(MagicMock(), storage, events.append)
    await engine._dry_run_blob_report(_reader(ROWS, BLOBS))
    assert storage.uploaded == {}
    assert "dry run — would upload 1 file blob(s)" in [e.message for e in events]
    warnings = _warnings(events)
    assert any("F-CORRUPT" in w and "would be restored" in w and "do not match" in w for w in warnings)
    assert any("F-MISSING" in w and "would be restored" in w and "no bytes" in w for w in warnings)


@pytest.mark.asyncio
async def test_a_restore_hashes_the_bytes_off_the_event_loop(monkeypatch):
    """Reading and hashing a whole blob runs on a worker thread, as the
    backup's archive write does: on the loop it stalls every request."""
    import threading

    threads: list[str] = []
    original = DirectRestoreEngine._checked_blob

    def recording(reader, file_id, expected):
        threads.append(threading.current_thread().name)
        return original(reader, file_id, expected)

    monkeypatch.setattr(DirectRestoreEngine, "_checked_blob", staticmethod(recording))
    engine = DirectRestoreEngine(MagicMock(), _Storage(), [].append)
    await engine._restore_blobs(_reader(ROWS, BLOBS), "ns")
    assert threads and threading.main_thread().name not in threads


@pytest.mark.asyncio
async def test_a_backup_without_file_storage_does_not_claim_its_files(namespace, tmp_path):
    """Asked for files with no storage to read them from, the archive carries
    none, and its manifest says so: a later restore would otherwise name
    every file as having lost its bytes (CASE-1194, 1-R9)."""
    client, ns = namespace
    events: list = []
    output = tmp_path / "no-storage.zip"
    await DirectBackupEngine(client, None, events.append).run_backup(ns, output, include_files=True)

    with ArchiveReader(output) as reader:
        manifest = reader.read_manifest()
        assert manifest.include_files is False
        assert manifest.include_all_versions is True
        assert reader.list_blobs() == []
    assert any("File storage is not enabled" in w for w in _warnings(events))
