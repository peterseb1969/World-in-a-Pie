"""File rows that a fresh restore wrote with the source's storage_key are
re-pointed at their own file id. A row with no object there yet gets a copy
of the object its old key names first, so it keeps serving what it served
and no longer shares an object another file can delete.

Restores now write `storage_key = file_id` (see test_remap_integration's
TestRestoredFilesOwnTheirBytes). This covers rows written before that.
(CASE-1093.)
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from document_store.services.file_storage_client import FileStorageUnavailableError
from document_store.services.file_storage_keys import repair_storage_keys


class _Storage:
    def __init__(self, keys, *, down=()):
        self.objects = {k: f"bytes of {k}".encode() for k in keys}
        self.down = set(down)

    async def exists(self, storage_key):
        if storage_key in self.down:
            raise FileStorageUnavailableError("unreachable")
        return storage_key in self.objects

    async def copy_object(self, source_key, dest_key):
        self.objects[dest_key] = self.objects[source_key]


@pytest_asyncio.fixture
async def files():
    client = AsyncIOMotorClient(os.environ["MONGO_URI"])
    coll = client["wip_test_storage_keys"][f"files_{uuid.uuid4().hex[:8]}"]
    yield coll
    await coll.drop()
    client.close()


@pytest.mark.asyncio
async def test_a_row_with_its_own_object_is_repointed(files):
    await files.insert_many([
        {"file_id": "NEW", "storage_key": "OLD"},   # a fresh-restored copy
        {"file_id": "OLD", "storage_key": "OLD"},   # its source, untouched
    ])
    storage = _Storage({"NEW", "OLD"})
    assert await repair_storage_keys(files, storage) == (1, 0, [])
    assert (await files.find_one({"file_id": "NEW"}))["storage_key"] == "NEW"
    assert (await files.find_one({"file_id": "OLD"}))["storage_key"] == "OLD"
    assert storage.objects["NEW"] == b"bytes of NEW"


@pytest.mark.asyncio
async def test_a_row_without_its_own_object_gets_a_copy_and_no_longer_aliases(files):
    """Restored from an archive without bytes, the row served the source's
    object, and deleting it deleted the source's: now it has its own copy."""
    await files.insert_many([
        {"file_id": "NEW", "storage_key": "OLD"},
        {"file_id": "OLD", "storage_key": "OLD"},
    ])
    storage = _Storage({"OLD"})
    assert await repair_storage_keys(files, storage) == (1, 1, [])
    assert (await files.find_one({"file_id": "NEW"}))["storage_key"] == "NEW"
    assert storage.objects["NEW"] == storage.objects["OLD"] == b"bytes of OLD"


@pytest.mark.asyncio
async def test_a_row_whose_old_key_names_no_object_is_repointed_as_it_is(files):
    await files.insert_one({"file_id": "NEW", "storage_key": "GONE"})
    storage = _Storage(set())
    assert await repair_storage_keys(files, storage) == (1, 0, [])
    assert (await files.find_one({"file_id": "NEW"}))["storage_key"] == "NEW"
    assert storage.objects == {}


@pytest.mark.asyncio
async def test_a_row_the_store_cannot_answer_for_does_not_stop_the_others(files):
    await files.insert_many([
        {"file_id": "A-NEW", "storage_key": "A-OLD"},
        {"file_id": "B-NEW", "storage_key": "B-OLD"},
    ])
    storage = _Storage({"A-OLD", "B-OLD"}, down={"A-NEW"})
    assert await repair_storage_keys(files, storage) == (1, 1, ["A-NEW"])
    assert (await files.find_one({"file_id": "A-NEW"}))["storage_key"] == "A-OLD"
    assert (await files.find_one({"file_id": "B-NEW"}))["storage_key"] == "B-NEW"


@pytest.mark.asyncio
async def test_a_second_run_finds_nothing(files):
    await files.insert_one({"file_id": "NEW", "storage_key": "OLD"})
    await repair_storage_keys(files, _Storage({"OLD"}))
    assert await repair_storage_keys(files, _Storage({"OLD"})) == (0, 0, [])
