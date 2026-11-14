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

    async def download(self, storage_key):
        return self.objects[storage_key]

    async def copy_object(self, source_key, dest_key):
        self.objects[dest_key] = self.objects[source_key]


@pytest_asyncio.fixture
async def files():
    client = AsyncIOMotorClient(os.environ["MONGO_URI"])
    coll = client["wip_test_storage_keys"][f"files_{uuid.uuid4().hex[:8]}"]
    yield coll
    await coll.drop()
    await coll.database["document_store_migrations"].delete_many(
        {"_id": f"storage_keys_v1:{coll.name}"},
    )
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
async def test_the_alias_is_copied_only_when_its_bytes_are_the_files(files):
    """The row's checksum decides: bytes that are not the file's are never
    copied in as its own (CASE-1287, 24b7df2f R8)."""
    import hashlib

    await files.insert_many([
        {"file_id": "GOOD", "storage_key": "OLD",
         "checksum": hashlib.sha256(b"bytes of OLD").hexdigest()},
        {"file_id": "BAD", "storage_key": "OLD",
         "checksum": hashlib.sha256(b"what was uploaded").hexdigest()},
        {"file_id": "OLD", "storage_key": "OLD"},
    ])
    storage = _Storage({"OLD"})
    assert await repair_storage_keys(files, storage) == (2, 1, [])
    assert storage.objects["GOOD"] == b"bytes of OLD"
    assert "BAD" not in storage.objects
    assert (await files.find_one({"file_id": "BAD"}))["storage_key"] == "BAD"


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


@pytest.mark.asyncio
async def test_a_clean_pass_marks_the_install_and_later_starts_skip_the_scan(files):
    """The repair's query uses no index; once a pass leaves nothing to
    repair, later starts do not scan (CASE-1287 ff44d9ba R5, Peter)."""
    await files.insert_one({"file_id": "NEW", "storage_key": "OLD"})
    assert await repair_storage_keys(files, _Storage({"OLD"})) == (1, 1, [])
    # A row that would need repair now (it cannot arise from a restore any
    # more) is not looked at: the scan is skipped.
    await files.insert_one({"file_id": "LATE", "storage_key": "OLD"})
    assert await repair_storage_keys(files, _Storage({"OLD"})) == (0, 0, [])
    assert (await files.find_one({"file_id": "LATE"}))["storage_key"] == "OLD"


@pytest.mark.asyncio
async def test_a_pass_that_leaves_failures_scans_again_next_time(files):
    await files.insert_one({"file_id": "A-NEW", "storage_key": "A-OLD"})
    down = _Storage({"A-OLD"}, down={"A-NEW"})
    assert await repair_storage_keys(files, down) == (0, 0, ["A-NEW"])
    assert await repair_storage_keys(files, _Storage({"A-OLD"})) == (1, 1, [])
