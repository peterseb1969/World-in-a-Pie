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
longer shares an object another file can delete. A row whose old key names no
object is re-pointed as it is: it had no bytes either way. A row the object
store could not be asked about, or the copy failed for, is left as it is,
counted and named in the log, and retried at the next start.

Runs at startup, once the object store is reachable and its bucket exists.
Idempotent: a repaired install has no such rows.
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple, Protocol

from motor.motor_asyncio import AsyncIOMotorCollection

logger = logging.getLogger("document_store.file_storage_keys")


class _Store(Protocol):
    async def exists(self, storage_key: str) -> bool: ...

    async def copy_object(self, source_key: str, dest_key: str) -> None: ...


class StorageKeyRepair(NamedTuple):
    """What a repair pass did: rows re-pointed at their own id (``copied`` of
    them after copying the old key's object there), and the file ids it could
    not repair this time."""

    repaired: int
    copied: int
    failed: list[str]


async def repair_storage_keys(
    collection: AsyncIOMotorCollection, storage: _Store
) -> StorageKeyRepair:
    """Re-point file rows at the object under their own file id, copying the
    object their old key names there when it has none yet."""
    repaired = copied = 0
    failed: list[str] = []
    query: dict[str, Any] = {
        "file_id": {"$exists": True},
        "$expr": {"$ne": ["$storage_key", "$file_id"]},
    }
    async for row in collection.find(query, {"file_id": 1, "storage_key": 1}):
        file_id, old_key = row["file_id"], row.get("storage_key")
        try:
            if old_key and not await storage.exists(file_id) and await storage.exists(old_key):
                await storage.copy_object(old_key, file_id)
                copied += 1
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
