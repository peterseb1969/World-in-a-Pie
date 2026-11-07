"""File bytes go in before the records that need them.

Provenance: CASE-1096 part 2. Every restore mode wrote its file records first
and uploaded the bytes at the very end, so a failure in between (a later
namespace's write, the object store) left file records whose bytes were
never uploaded. The bytes now go up first: a failure afterwards leaves at
most unreferenced bytes, which a retry overwrites.

The stand-in object store checks, at every upload, that no file record has
been written yet.
"""

from __future__ import annotations

import hashlib
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from document_store.services.backup_engine import COLLECTION_MAP, DirectRestoreEngine
from tests.test_merge_restore import (
    NAMESPACE,
    REMAP_TARGET,
    _all_collections,
    _archive,
    _clear_remap_target,
    _FakeRegistry,
    _seed_namespace,
)

CONTENT = b"the bytes"
CHECKSUM = hashlib.sha256(CONTENT).hexdigest()


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


def _storage_checking(mongo, namespace: str):
    """An object store whose upload asserts the namespace holds no file record yet."""
    uploaded: list[str] = []
    db_name, coll_name = COLLECTION_MAP["files"]

    async def upload(storage_key, content, content_type):
        records = await mongo[db_name][coll_name].count_documents({"namespace": namespace})
        assert records == 0, f"file record written before its bytes ({storage_key})"
        uploaded.append(storage_key)

    storage = MagicMock()
    storage.upload = upload
    storage.uploaded = uploaded
    return storage


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


@pytest.mark.asyncio
async def test_a_merge_uploads_before_writing_the_file_record(mongo):
    await _seed_namespace(mongo)
    storage = _storage_checking(mongo, NAMESPACE)
    engine = DirectRestoreEngine(mongo, storage, lambda e: None)
    with patch("document_store.services.backup_engine.ArchiveReader", return_value=_file_archive()):
        await engine.run_merge(MagicMock(), NAMESPACE)
    assert storage.uploaded == ["F1"]
    assert [f["file_id"] for f in await _files(mongo, NAMESPACE)] == ["F1"]


@pytest.mark.asyncio
async def test_a_plain_restore_uploads_before_writing_the_file_record(mongo):
    storage = _storage_checking(mongo, NAMESPACE)
    engine = DirectRestoreEngine(mongo, storage, lambda e: None)
    engine._upsert_namespace = AsyncMock()
    with patch("document_store.services.backup_engine.ArchiveReader", return_value=_file_archive()):
        # The archive carries a file row and no registry identity rows.
        await engine.run_restore(MagicMock(), NAMESPACE, allow_missing_identity=True)
    assert storage.uploaded == ["F1"]
    assert [f["file_id"] for f in await _files(mongo, NAMESPACE)] == ["F1"]


@pytest.mark.asyncio
async def test_a_fresh_restore_uploads_before_writing_the_file_record(mongo):
    await _clear_remap_target(mongo)
    storage = _storage_checking(mongo, REMAP_TARGET)
    registry = _FakeRegistry()
    http = MagicMock()
    http.post = registry.post
    http.put = AsyncMock(return_value=MagicMock(status_code=200, text="ok"))
    http.__aenter__ = AsyncMock(return_value=http)
    http.__aexit__ = AsyncMock(return_value=None)
    engine = DirectRestoreEngine(mongo, storage, lambda e: None)
    with patch("document_store.services.backup_engine.ArchiveReader", return_value=_file_archive()), \
            patch("httpx.AsyncClient", return_value=http):
        await engine.run_remap(MagicMock(), REMAP_TARGET)
    [new_id] = storage.uploaded
    assert new_id != "F1"
    assert [f["file_id"] for f in await _files(mongo, REMAP_TARGET)] == [new_id]
    await _clear_remap_target(mongo)
