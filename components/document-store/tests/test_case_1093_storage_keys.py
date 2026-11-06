"""File rows that a fresh restore wrote with the source's storage_key are
re-pointed at the object under their own file id, once that object exists.

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

from document_store.services.file_storage_keys import repair_storage_keys


class _Storage:
    def __init__(self, keys):
        self.keys = set(keys)

    async def exists(self, storage_key):
        return storage_key in self.keys


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
    repaired, left = await repair_storage_keys(files, _Storage({"NEW", "OLD"}))
    assert (repaired, left) == (1, [])
    assert (await files.find_one({"file_id": "NEW"}))["storage_key"] == "NEW"
    assert (await files.find_one({"file_id": "OLD"}))["storage_key"] == "OLD"


@pytest.mark.asyncio
async def test_a_row_without_its_own_object_keeps_its_key(files):
    await files.insert_one({"file_id": "NEW", "storage_key": "OLD"})
    repaired, left = await repair_storage_keys(files, _Storage({"OLD"}))
    assert (repaired, left) == (0, ["NEW"])
    assert (await files.find_one({"file_id": "NEW"}))["storage_key"] == "OLD"


@pytest.mark.asyncio
async def test_a_second_run_finds_nothing(files):
    await files.insert_one({"file_id": "NEW", "storage_key": "OLD"})
    await repair_storage_keys(files, _Storage({"NEW"}))
    assert await repair_storage_keys(files, _Storage({"NEW"})) == (0, [])
