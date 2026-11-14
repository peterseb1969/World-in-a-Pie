"""A restore never writes file bytes over an existing file's bytes; and an
archive with no namespace config says it is not a re-creation.

Provenance: CASE-1283. Blobs are stored under their bare file id, and every
restore mode uploaded with an unconditional PUT, so an archive naming another
namespace's file id (with bytes matching its own recorded checksum) replaced
that file's content. A plain restore also uploaded before its namespace step,
so a restore the Registry refused had already written bytes.

Now each key is checked with a HEAD before anything is uploaded: a free key
is written, a key already holding the same bytes is left as it is, and a key
holding other bytes refuses the restore with nothing uploaded.
"""

from __future__ import annotations

import hashlib
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from document_store.services.backup_engine import (
    COLLECTION_MAP,
    DirectRestoreEngine,
    RestoreEngineError,
)
from tests.test_merge_restore import (
    NAMESPACE,
    REMAP_TARGET,
    _all_collections,
    _archive,
    _clear_remap_target,
    _FakeRegistry,
    _seed_namespace,
)

CONTENT = b"the archive's bytes"
CHECKSUM = hashlib.sha256(CONTENT).hexdigest()
VICTIM = b"another file's bytes"


@pytest_asyncio.fixture
async def mongo(session_mongo_client):
    from tests.conftest import _ensure_beanie

    await _ensure_beanie(session_mongo_client)
    client = AsyncIOMotorClient(os.environ.get("MONGO_URI", "mongodb://localhost:27017/"))

    async def _clear():
        for db_name, coll_name in _all_collections():
            key = "prefix" if coll_name == "namespaces" else "namespace"
            await client[db_name][coll_name].delete_many({key: {"$in": [NAMESPACE, REMAP_TARGET]}})

    await _clear()
    yield client
    await _clear()
    client.close()


class _Store:
    """An object store in memory that records every PUT."""

    def __init__(self, objects=None, *, every_key_taken_by=None):
        self.objects: dict[str, bytes] = dict(objects or {})
        self.puts: list[str] = []
        self._every_key_taken_by = every_key_taken_by

    async def exists(self, storage_key):
        return self._every_key_taken_by is not None or storage_key in self.objects

    async def download(self, storage_key):
        if storage_key in self.objects:
            return self.objects[storage_key]
        return self._every_key_taken_by

    async def upload(self, storage_key, content, content_type=None):
        self.puts.append(storage_key)
        self.objects[storage_key] = content


def _file_archive():
    reader = _archive(
        {"files": [{"file_id": "F1", "namespace": NAMESPACE, "checksum": CHECKSUM}]},
        blobs=["F1"],
    )
    reader.read_blob = MagicMock(return_value=CONTENT)
    reader.entity_count = MagicMock(
        side_effect=lambda entity_type, namespace="": 1 if entity_type == "files" else 0
    )
    return reader


async def _files(mongo, namespace: str) -> list[dict]:
    db_name, coll_name = COLLECTION_MAP["files"]
    return await mongo[db_name][coll_name].find({"namespace": namespace}).to_list(None)


async def _plain_restore(mongo, storage, *, upsert=None):
    engine = DirectRestoreEngine(mongo, storage, lambda e: None)
    engine._upsert_namespace = upsert or AsyncMock()
    with patch("document_store.services.backup_engine.ArchiveReader", return_value=_file_archive()):
        await engine.run_restore(MagicMock(), NAMESPACE, allow_missing_identity=True)


@pytest.mark.asyncio
async def test_a_plain_restore_refuses_a_key_holding_other_bytes(mongo):
    storage = _Store({"F1": VICTIM})
    with pytest.raises(RestoreEngineError, match="never writes over an existing file's bytes"):
        await _plain_restore(mongo, storage)
    assert storage.puts == []
    assert storage.objects["F1"] == VICTIM
    assert await _files(mongo, NAMESPACE) == []


@pytest.mark.asyncio
async def test_a_plain_restore_leaves_a_key_holding_the_same_bytes(mongo):
    """A retry after a failed restore finds its own bytes there: nothing to write."""
    storage = _Store({"F1": CONTENT})
    await _plain_restore(mongo, storage)
    assert storage.puts == []
    assert [f["file_id"] for f in await _files(mongo, NAMESPACE)] == ["F1"]


@pytest.mark.asyncio
async def test_a_plain_restore_the_registry_refuses_uploads_nothing(mongo):
    storage = _Store()
    upsert = AsyncMock(side_effect=RestoreEngineError("namespace refused"))
    with pytest.raises(RestoreEngineError, match="namespace refused"):
        await _plain_restore(mongo, storage, upsert=upsert)
    assert storage.puts == []


@pytest.mark.asyncio
async def test_a_merge_refuses_a_key_holding_other_bytes(mongo):
    await _seed_namespace(mongo)
    storage = _Store({"F1": VICTIM})
    engine = DirectRestoreEngine(mongo, storage, lambda e: None)
    with patch("document_store.services.backup_engine.ArchiveReader", return_value=_file_archive()), \
            pytest.raises(RestoreEngineError, match="never writes over an existing file's bytes"):
        await engine.run_merge(MagicMock(), NAMESPACE)
    assert storage.puts == []
    assert storage.objects["F1"] == VICTIM
    assert await _files(mongo, NAMESPACE) == []


@pytest.mark.asyncio
async def test_a_fresh_restore_refuses_a_new_key_holding_other_bytes(mongo):
    await _clear_remap_target(mongo)
    storage = _Store(every_key_taken_by=VICTIM)
    registry = _FakeRegistry()
    http = MagicMock()
    http.post = registry.post
    http.put = AsyncMock(return_value=MagicMock(status_code=200, text="ok"))
    http.__aenter__ = AsyncMock(return_value=http)
    http.__aexit__ = AsyncMock(return_value=None)
    engine = DirectRestoreEngine(mongo, storage, lambda e: None)
    with patch("document_store.services.backup_engine.ArchiveReader", return_value=_file_archive()), \
            patch("httpx.AsyncClient", return_value=http), \
            pytest.raises(RestoreEngineError, match="never writes over an existing file's bytes"):
        await engine.run_remap(MagicMock(), REMAP_TARGET)
    assert storage.puts == []
    assert await _files(mongo, REMAP_TARGET) == []
    await _clear_remap_target(mongo)


@pytest.mark.asyncio
async def test_an_archive_with_no_namespace_config_says_it_is_not_a_recreation():
    """A namespace backed up while it had no Registry record carries no config,
    so no id: the guide said such archives are refused, and the restore
    created the namespace under a new id without a word."""
    from tests.test_case_1084_archive_namespace_id import _registry, _restore_engine

    events: list = []
    with _registry({"prefix": "kb", "namespace_id": "019a0000-0000-7000-8000-00000000c0de"}):
        await _restore_engine(events)._upsert_namespace("kb", None)
    warnings = [e.message for e in events if e.phase == "warning"]
    assert any("records no namespace config" in w for w in warnings)


@pytest.mark.asyncio
async def test_a_key_another_file_uses_is_refused_even_with_the_same_bytes(mongo):
    """Two records on one object: deleting either deletes the other's bytes."""
    db_name, coll_name = COLLECTION_MAP["files"]
    await mongo[db_name][coll_name].insert_one(
        {"file_id": "F1", "storage_key": "F1", "namespace": REMAP_TARGET, "checksum": CHECKSUM},
    )
    storage = _Store({"F1": CONTENT})
    with pytest.raises(RestoreEngineError, match="never writes over an existing file's bytes"):
        await _plain_restore(mongo, storage)
    assert storage.puts == []
    assert await _files(mongo, NAMESPACE) == []
