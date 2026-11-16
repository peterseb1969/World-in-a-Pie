"""Repair file rows whose storage_key is not their file_id.

A file's bytes live under its file id: an upload stores them there, and a
restore uploads the archive's blob there (a fresh restore under the
re-minted id). A fresh restore used to write the row with the SOURCE's
storage_key, so the restored file served the source's bytes on the same
install (and deleting it deleted the source's object), or nothing elsewhere.
Restores now write `storage_key = file_id`; this repairs rows written
before that.

Every such row ends pointing at its own file id. When no object is there yet
(the restore's archive carried no bytes for the file), the object its old key
names is copied there first, so the file keeps serving what it served and no
longer shares an object another file can delete — but only when those bytes
match the row's checksum: bytes that are not the file's are never copied in as
its own, and such a row is re-pointed without them (it served another file's
bytes; it now serves none) and named in the log. A row whose old key names no
object is re-pointed as it is: it had no bytes either way. A row the object
store could not be asked about, or the copy failed for, is left as it is,
counted and named in the log, and retried at the next start.

Runs at startup, once the object store is reachable and its bucket exists.
Idempotent: a repaired install has no such rows. Its query can use no index
(it compares two fields of each row), so a pass that leaves nothing to repair
records a marker in `document_store_migrations` and later starts skip the
scan: restores write `storage_key = file_id`, so no new such row appears. A
pass that leaves rows unrepaired records none, and the next start retries.

Nothing in this code can stop an older document-store image from writing
such rows again (an install rolled back, restored fresh, and rolled forward).
The skip is therefore logged at every start, with the marker's id and date:
deleting that marker document makes the next start scan again.
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple, Protocol

from motor.motor_asyncio import AsyncIOMotorCollection

logger = logging.getLogger("document_store.file_storage_keys")


class _Store(Protocol):
    async def exists(self, storage_key: str) -> bool: ...

    async def download(self, storage_key: str) -> bytes: ...

    async def copy_object(self, source_key: str, dest_key: str) -> None: ...


class StorageKeyRepair(NamedTuple):
    """What a repair pass did: rows re-pointed at their own id (``copied`` of
    them after copying the old key's object there), and the file ids it could
    not repair this time."""

    repaired: int
    copied: int
    failed: list[str]


_MIGRATIONS_COLLECTION = "document_store_migrations"


def _marker_id(collection: AsyncIOMotorCollection) -> str:
    return f"storage_keys_v1:{collection.name}"


async def repair_storage_keys(
    collection: AsyncIOMotorCollection, storage: _Store
) -> StorageKeyRepair:
    """Re-point file rows at the object under their own file id, copying the
    object their old key names there when it has none yet. Skipped once a
    pass has left nothing to repair (see the module docstring)."""
    markers = collection.database[_MIGRATIONS_COLLECTION]
    marker = await markers.find_one({"_id": _marker_id(collection)})
    if marker:
        logger.info(
            "File storage-key repair skipped: a clean pass was recorded at %s (%s.%s). "
            "Delete that document to scan again at the next start.",
            marker.get("completed_at"), _MIGRATIONS_COLLECTION, marker["_id"],
        )
        return StorageKeyRepair(0, 0, [])
    result = await _repair_pass(collection, storage)
    if not result.failed:
        from datetime import UTC, datetime

        await markers.update_one(
            {"_id": _marker_id(collection)},
            {"$set": {"completed_at": datetime.now(UTC), "repaired": result.repaired}},
            upsert=True,
        )
    return result


async def _repair_pass(
    collection: AsyncIOMotorCollection, storage: _Store
) -> StorageKeyRepair:
    """One scan: every row whose storage_key is not its file_id."""
    repaired = copied = 0
    failed: list[str] = []
    query: dict[str, Any] = {
        "file_id": {"$exists": True},
        "$expr": {"$ne": ["$storage_key", "$file_id"]},
    }
    async for row in collection.find(query, {"file_id": 1, "storage_key": 1, "checksum": 1}):
        file_id, old_key = row["file_id"], row.get("storage_key")
        try:
            if old_key and not await storage.exists(file_id) and await storage.exists(old_key):
                if await _holds_the_files_bytes(storage, old_key, row.get("checksum")):
                    await storage.copy_object(old_key, file_id)
                    copied += 1
                else:
                    logger.warning(
                        "File %s: the object its storage_key %r names does not match "
                        "its checksum, so it is not copied in as the file's own; the "
                        "row is re-pointed at its id and serves no bytes",
                        file_id, old_key,
                    )
            await collection.update_one(
                {"_id": row["_id"]}, {"$set": {"storage_key": file_id}}
            )
        except Exception:
            # One row the store cannot answer for never stops the others.
            failed.append(file_id)
            logger.warning(
                "File %s has storage_key %r and could not be repaired; left as it is",
                file_id, old_key, exc_info=True,
            )
            continue
        repaired += 1
    return StorageKeyRepair(repaired, copied, failed)


async def _holds_the_files_bytes(storage: _Store, key: str, checksum: Any) -> bool:
    """Whether the object under ``key`` is the file's bytes by its recorded
    SHA-256 (hex). A row that records no checksum cannot be checked, and is
    taken as it served."""
    if not checksum:
        return True
    import asyncio
    import hashlib

    data = await storage.download(key)
    return await asyncio.to_thread(lambda: hashlib.sha256(data).hexdigest()) == checksum
